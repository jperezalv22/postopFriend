"""Consola de conocimiento: alta, baja y verificación del olvido. Es la compuerta G5.

El contrato del reto es subir, listar, eliminar y mostrar «procesado y disponible».
Encima de eso hay dos endpoints que existen para que el jurado no tenga que creer
en nada:

    POST /api/kb/verificar-olvido   corre una búsqueda contra el documento borrado y
                                    devuelve el conteo de fragmentos en cero, con el
                                    JSON de la búsqueda. G5 en un clic.
    POST /api/kb/probar             recupera fragmentos SIN pasar por el LLM. Deja ver
                                    el RAG desnudo: qué trae y con qué puntaje.

Y uno que hace clicables las citas:

    GET  /api/kb/source/{doc_id}    sirve el PDF real, para abrirlo en #page=N.

El alta y la baja llaman a `app.rag.pipeline`, el mismo camino que usa
`scripts/build_index.py` para el corpus base. Dos caminos distintos significarían
que uno de los dos llega sin probar al día de la evaluación.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.obs.logger import ahora_iso
from app.rag import pipeline, retriever, store
from app.store import db

log = logging.getLogger("postopfriend.kb")
router = APIRouter(prefix="/api/kb", tags=["conocimiento"])

MAX_BYTES = 60 * 1024 * 1024
EXTENSIONES = {".pdf", ".docx", ".txt", ".md"}

# Progreso de las ingestas en curso. En memoria a propósito: si el servidor se
# reinicia, la ingesta se perdió de todas formas y el documento no quedó indexado.
_progreso: dict[str, dict[str, Any]] = {}


# ─── Estado del conocimiento ─────────────────────────────────────────────────

@router.get("/estado")
def estado() -> dict[str, Any]:
    e = store.estado()
    fila = db.conexion().execute(
        "SELECT COUNT(*) AS n FROM documentos WHERE origen = 'subido' AND estado = 'disponible'"
    ).fetchone()
    return {**e, "documentos_subidos": int(fila["n"])}


@router.get("/documentos")
def listar(origen: str = "", procedimiento: str = "", buscar: str = "") -> dict[str, Any]:
    condiciones, parametros = [], []
    if origen:
        condiciones.append("origen = ?")
        parametros.append(origen)
    if procedimiento:
        condiciones.append("procedimiento = ?")
        parametros.append(procedimiento)
    if buscar:
        condiciones.append("LOWER(titulo) LIKE ?")
        parametros.append(f"%{buscar.lower()}%")

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    filas = db.conexion().execute(
        f"""SELECT doc_id, titulo, archivo, procedimiento, idioma, paginas, chunks,
                   substr(sha256, 1, 16) AS sha256, origen, estado, detalle, ingesta_ts
            FROM documentos {where}
            ORDER BY (origen = 'subido') DESC, titulo""",
        parametros,
    ).fetchall()
    return {"kb_version": db.kb_version(), "documentos": [dict(f) for f in filas]}


# ─── Alta ────────────────────────────────────────────────────────────────────

@router.post("/documentos")
async def subir(
    archivo: UploadFile,
    tareas: BackgroundTasks,
    procedimiento: str = "general",
) -> dict[str, Any]:
    """Recibe el archivo y arranca la ingesta en segundo plano.

    El documento sube como `procedimiento="general"` salvo que se indique otro. Eso
    NO lo excluye de las búsquedas: el procedimiento es un boost, nunca un filtro
    (ver retriever.py). Si fuera filtro, un PDF del jurado sobre cualquier tema
    quedaría fuera de toda consulta y G5 fallaría en directo.
    """
    nombre = Path(archivo.filename or "documento").name
    extension = Path(nombre).suffix.lower()
    if extension not in EXTENSIONES:
        raise HTTPException(400, f"formato no admitido: {extension or 'sin extensión'}. "
                                 f"Se aceptan {', '.join(sorted(EXTENSIONES))}")

    contenido = await archivo.read()
    if len(contenido) > MAX_BYTES:
        raise HTTPException(413, f"el archivo pesa {len(contenido) / 1e6:.0f} MB y el "
                                 f"límite es {MAX_BYTES // 10**6} MB")
    if not contenido:
        raise HTTPException(400, "el archivo está vacío")

    s = get_settings()
    s.dir_uploads.mkdir(parents=True, exist_ok=True)
    # Prefijo único: dos jurados subiendo «guia.pdf» no se pisan.
    destino = s.dir_uploads / f"{uuid.uuid4().hex[:8]}-{nombre}"
    destino.write_bytes(contenido)

    tarea_id = uuid.uuid4().hex[:12]
    _progreso[tarea_id] = {
        "tarea_id": tarea_id, "archivo": nombre, "fase": "recibido",
        "porcentaje": 5, "iniciado": ahora_iso(), "terminado": False,
    }
    tareas.add_task(_ingestar, tarea_id, destino, nombre, procedimiento)
    return _progreso[tarea_id]


FASES = {
    "recibido": (5, "recibido"),
    "extrayendo": (20, "extrayendo texto"),
    "troceando": (40, "troceando"),
    "embebiendo": (55, "generando embeddings"),
    "indexando": (85, "indexando"),
    "disponible": (100, "disponible"),
    "duplicado": (100, "duplicado"),
    "error": (100, "error"),
}


def _ingestar(tarea_id: str, ruta: Path, titulo: str, procedimiento: str) -> None:
    def avisar(fase: str, datos: dict[str, Any]) -> None:
        porcentaje, etiqueta = FASES.get(fase, (50, fase))
        _progreso[tarea_id].update(
            {"fase": fase, "etiqueta": etiqueta, "porcentaje": porcentaje, **datos}
        )

    try:
        resumen = pipeline.indexar(
            ruta, titulo=Path(titulo).stem, procedimiento=procedimiento,
            carpeta="subidos", origen="subido", progreso=avisar,
        )
        if resumen.estado == "disponible":
            version = pipeline.finalizar_cambio(
                "alta",
                {"doc_id": resumen.doc_id, "titulo": resumen.titulo,
                 "fragmentos": resumen.fragmentos, "sha256": resumen.sha256,
                 "archivo": resumen.archivo},
            )
            resumen_dict = {**resumen.como_dict(), "kb_version": version}
        else:
            resumen_dict = resumen.como_dict()

        _progreso[tarea_id].update(
            {"terminado": True, "resultado": resumen_dict,
             "fase": resumen.estado, "porcentaje": 100,
             "etiqueta": FASES.get(resumen.estado, (100, resumen.estado))[1]}
        )
    except Exception as e:  # ningún PDF raro puede tumbar el servidor (riesgo R7)
        log.exception("la ingesta de %s falló", titulo)
        _progreso[tarea_id].update(
            {"terminado": True, "fase": "error", "porcentaje": 100,
             "etiqueta": "error", "resultado": {"estado": "error", "detalle": str(e)}}
        )


@router.get("/progreso/{tarea_id}")
def progreso(tarea_id: str) -> dict[str, Any]:
    if tarea_id not in _progreso:
        raise HTTPException(404, "tarea desconocida")
    return _progreso[tarea_id]


# ─── Baja ────────────────────────────────────────────────────────────────────

@router.delete("/documentos/{doc_id}")
def eliminar(doc_id: str) -> dict[str, Any]:
    resultado = pipeline.dar_de_baja(doc_id)
    if not resultado["ok"]:
        raise HTTPException(404, resultado["motivo"])
    return resultado


@router.post("/verificar-olvido")
def verificar_olvido(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Demuestra que un documento eliminado ya no se recupera.

    Convierte G5 en una demostración de un clic: se ve la consulta, el JSON de la
    búsqueda y el conteo de fragmentos de ese documento en cero.
    """
    doc_id = str(cuerpo.get("doc_id") or "")
    if not doc_id:
        raise HTTPException(400, "falta doc_id")
    return pipeline.verificar_olvido(doc_id, str(cuerpo.get("consulta") or "") or None)


# ─── Probar el conocimiento sin el LLM ───────────────────────────────────────

@router.post("/probar")
def probar(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Recuperación desnuda: fragmentos y puntajes, sin generación.

    Separa dos preguntas que suelen confundirse cuando una respuesta sale mal: ¿el
    buscador no encontró la fuente, o la encontró y el modelo la ignoró?
    """
    consulta = str(cuerpo.get("consulta") or "").strip()
    if not consulta:
        raise HTTPException(400, "falta la consulta")
    resultado = retriever.solo_fragmentos(
        consulta,
        procedimiento=str(cuerpo.get("procedimiento") or "") or None,
        top_k=int(cuerpo.get("top_k") or 8),
    )
    # Se informa también qué habría decidido el umbral, sin aplicarlo.
    con_umbral = retriever.recuperar(consulta, str(cuerpo.get("procedimiento") or "") or None)
    return {
        **resultado.como_dict(),
        "abstendria": con_umbral.abstiene,
        "motivo_abstencion": con_umbral.motivo,
    }


# ─── Fuente original, para que la cita sea verificable ───────────────────────

@router.get("/source/{doc_id}")
def fuente(doc_id: str):
    """Sirve el archivo original. Es lo que hace que una cita se pueda comprobar.

    La UI enlaza a `/api/kb/source/{doc_id}#page=N`, y el visor del navegador abre
    el PDF en esa página exacta. La rúbrica pide que la referencia «resista una
    verificación contra la fuente real»: esto es esa verificación, en un clic.
    """
    fila = db.conexion().execute(
        "SELECT archivo, titulo, origen FROM documentos WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    if fila is None:
        raise HTTPException(404, "documento no encontrado")

    s = get_settings()
    base = s.dir_uploads if fila["origen"] == "subido" else s.dir_textos
    ruta = (base / str(fila["archivo"])).resolve()

    # El doc_id viene de la base, no del usuario, pero la ruta se comprueba igual:
    # un `archivo` con `../` en la base bastaría para servir cualquier cosa del disco.
    if not str(ruta).startswith(str(base.resolve())) or not ruta.is_file():
        raise HTTPException(404, f"el archivo no está en disco: {fila['archivo']}")

    tipos = {".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
             ".md": "text/markdown; charset=utf-8"}
    return FileResponse(
        ruta,
        media_type=tipos.get(ruta.suffix.lower(), "application/octet-stream"),
        filename=ruta.name,
        # inline para que el PDF se abra en el visor y respete #page=N en vez de
        # descargarse, que rompería la verificación de la cita.
        headers={"Content-Disposition": f'inline; filename="{ruta.name}"'},
    )


# ─── Auditoría ───────────────────────────────────────────────────────────────

@router.get("/auditoria")
def auditoria(limite: int = 50) -> dict[str, Any]:
    """Quién cambió el conocimiento, qué, cuándo y cuántos fragmentos."""
    from app.obs.logger import leer_jsonl

    registros = leer_jsonl(get_settings().ruta_kb_audit)
    return {"eventos": registros[-limite:][::-1]}

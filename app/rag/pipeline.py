"""Alta y baja de documentos. Un solo camino para el corpus base y para la consola.

Que `scripts/build_index.py` y el botón «subir» de la consola llamen exactamente a
la misma función no es elegancia: es lo que garantiza que un documento subido en
caliente durante la evaluación quede indexado igual que los 107 de base. Si
fueran dos caminos, uno de los dos estaría sin probar el día de la sesión.

Toda alta y toda baja terminan igual: `kb_version += 1` y el índice léxico
invalidado. Es la regla que sostiene la compuerta G5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.obs.logger import ahora_iso, registrar_kb
from app.rag import chunker, embedder, ingest, store
from app.store import db

log = logging.getLogger("postopfriend.rag")

Progreso = Callable[[str, dict[str, Any]], None]


@dataclass
class ResumenIngesta:
    doc_id: str
    titulo: str
    archivo: str
    estado: str              # disponible | sin_texto | error | duplicado
    detalle: str = ""
    paginas: int = 0
    fragmentos: int = 0
    idioma: str = "es"
    sha256: str = ""
    origen: str = "base"
    procedimiento: str = "general"
    paginas_sin_texto: list[int] = field(default_factory=list)
    segundos: float = 0.0

    def como_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "titulo": self.titulo,
            "archivo": self.archivo,
            "estado": self.estado,
            "detalle": self.detalle,
            "paginas": self.paginas,
            "fragmentos": self.fragmentos,
            "idioma": self.idioma,
            "sha256": self.sha256[:16],
            "origen": self.origen,
            "procedimiento": self.procedimiento,
            "paginas_sin_texto": self.paginas_sin_texto,
            "segundos": round(self.segundos, 2),
        }


def doc_id_de(sha256: str) -> str:
    """Identidad por contenido: el mismo texto siempre es el mismo documento.

    Es lo que deduplica el corpus base, que trae el mismo documento dos veces con
    distinta capitalización en el nombre.
    """
    return sha256[:16]


def _documento_existente(sha256: str) -> dict[str, Any] | None:
    fila = db.conexion().execute(
        "SELECT * FROM documentos WHERE sha256 = ? AND estado = 'disponible'", (sha256,)
    ).fetchone()
    return dict(fila) if fila else None


def indexar(
    ruta: Path,
    titulo: str | None = None,
    procedimiento: str = "general",
    carpeta: str = "",
    origen: str = "base",
    progreso: Progreso | None = None,
) -> ResumenIngesta:
    """Extrae, trocea, embebe e indexa un documento. Nunca lanza."""
    import time

    t0 = time.perf_counter()

    def avisar(fase: str, **datos: Any) -> None:
        if progreso:
            progreso(fase, datos)

    avisar("recibido", archivo=ruta.name)

    avisar("extrayendo")
    doc = ingest.extraer(ruta, titulo=titulo)
    resumen = ResumenIngesta(
        doc_id="",
        titulo=doc.titulo,
        archivo=str(ruta.name if origen == "subido" else f"{carpeta}/{ruta.name}"),
        estado=doc.estado,
        detalle=doc.detalle,
        paginas=doc.total_paginas,
        idioma=doc.idioma,
        sha256=doc.sha256,
        origen=origen,
        procedimiento=procedimiento,
        paginas_sin_texto=doc.paginas_sin_texto,
    )
    if doc.estado != "disponible":
        resumen.segundos = time.perf_counter() - t0
        avisar("error", detalle=doc.detalle)
        _registrar_documento(resumen)
        return resumen

    resumen.doc_id = doc_id_de(doc.sha256)

    previo = _documento_existente(doc.sha256)
    if previo and previo["doc_id"] == resumen.doc_id:
        resumen.estado = "duplicado"
        resumen.detalle = f"mismo contenido que «{previo['titulo']}»: no se reindexa"
        resumen.fragmentos = int(previo["chunks"] or 0)
        resumen.segundos = time.perf_counter() - t0
        avisar("duplicado", detalle=resumen.detalle)
        return resumen

    avisar("troceando")
    fragmentos = chunker.trocear(doc, resumen.doc_id, procedimiento)
    if not fragmentos:
        resumen.estado = "sin_texto"
        resumen.detalle = "no se obtuvo ningún fragmento indexable"
        resumen.segundos = time.perf_counter() - t0
        avisar("error", detalle=resumen.detalle)
        _registrar_documento(resumen)
        return resumen

    avisar("embebiendo", fragmentos=len(fragmentos))
    try:
        vectores = embedder.embeber_pasajes([f.texto for f in fragmentos])
    except Exception as e:
        resumen.estado, resumen.detalle = "error", f"fallo al generar embeddings: {e}"
        resumen.segundos = time.perf_counter() - t0
        avisar("error", detalle=resumen.detalle)
        _registrar_documento(resumen)
        return resumen

    avisar("indexando")
    meta = store.MetaDocumento(
        doc_id=resumen.doc_id,
        titulo=doc.titulo,
        archivo=resumen.archivo,
        procedimiento=procedimiento,
        carpeta=carpeta or procedimiento,
        idioma=doc.idioma,
        sha256=doc.sha256,
        origen=origen,
        paginas=doc.total_paginas,
        ingesta_ts=ahora_iso(),
    )
    # Un reintento tras un fallo a mitad podría dejar fragmentos huérfanos.
    store.eliminar_documento(resumen.doc_id)
    resumen.fragmentos = store.agregar_fragmentos(meta, fragmentos, vectores)
    resumen.estado = "disponible"
    resumen.segundos = time.perf_counter() - t0

    _registrar_documento(resumen)
    avisar("disponible", fragmentos=resumen.fragmentos, segundos=round(resumen.segundos, 1))
    return resumen


def _registrar_documento(r: ResumenIngesta) -> None:
    with db.transaccion() as con:
        con.execute(
            """INSERT INTO documentos
                 (doc_id, titulo, archivo, procedimiento, idioma, paginas, chunks,
                  sha256, origen, estado, detalle, ingesta_ts, kb_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(doc_id) DO UPDATE SET
                 titulo=excluded.titulo, archivo=excluded.archivo,
                 procedimiento=excluded.procedimiento, idioma=excluded.idioma,
                 paginas=excluded.paginas, chunks=excluded.chunks,
                 origen=excluded.origen, estado=excluded.estado,
                 detalle=excluded.detalle, ingesta_ts=excluded.ingesta_ts,
                 kb_version=excluded.kb_version""",
            (
                r.doc_id or f"err-{abs(hash(r.archivo)) % 10**12}",
                r.titulo, r.archivo, r.procedimiento, r.idioma, r.paginas,
                r.fragmentos, r.sha256, r.origen, r.estado, r.detalle,
                ahora_iso(), db.kb_version(),
            ),
        )


def finalizar_cambio(evento: str, detalle: dict[str, Any]) -> int:
    """Cierra un alta o una baja: sube `kb_version`, invalida cachés y audita."""
    version = db.incrementar_kb_version()
    store.invalidar_indice()
    registrar_kb(evento, {**detalle, "kb_version": version})
    return version


def dar_de_baja(doc_id: str) -> dict[str, Any]:
    """Elimina un documento del conocimiento. Es la mitad «baja» de la compuerta G5."""
    fila = db.conexion().execute("SELECT * FROM documentos WHERE doc_id = ?", (doc_id,)).fetchone()
    if fila is None:
        return {"ok": False, "motivo": "documento no encontrado"}

    fragmentos = store.eliminar_documento(doc_id)
    with db.transaccion() as con:
        con.execute("DELETE FROM documentos WHERE doc_id = ?", (doc_id,))

    # El archivo subido también se borra; el corpus base se conserva en disco.
    if fila["origen"] == "subido":
        archivo = get_settings().dir_uploads / str(fila["archivo"])
        archivo.unlink(missing_ok=True)

    version = finalizar_cambio(
        "baja",
        {"doc_id": doc_id, "titulo": fila["titulo"], "fragmentos": fragmentos,
         "sha256": fila["sha256"], "origen": fila["origen"]},
    )
    return {
        "ok": True, "doc_id": doc_id, "titulo": fila["titulo"],
        "fragmentos_eliminados": fragmentos, "kb_version": version,
    }


def verificar_olvido(doc_id: str, consulta: str | None = None) -> dict[str, Any]:
    """Demuestra que un documento eliminado ya no se recupera.

    Convierte la compuerta G5 en una demostración de un clic: el jurado ve la
    consulta, el JSON de la búsqueda y el conteo de fragmentos en cero.
    """
    from app.rag import retriever

    en_chroma = store.contar_fragmentos(doc_id)
    en_sqlite = db.conexion().execute(
        "SELECT COUNT(*) AS n FROM documentos WHERE doc_id = ?", (doc_id,)
    ).fetchone()["n"]

    resultado = None
    fragmentos_del_doc = 0
    if consulta:
        resultado = retriever.solo_fragmentos(consulta, top_k=10)
        fragmentos_del_doc = sum(1 for c in resultado.citas if c.doc_id == doc_id)

    return {
        "doc_id": doc_id,
        "fragmentos_en_chroma": en_chroma,
        "registros_en_sqlite": en_sqlite,
        "kb_version": db.kb_version(),
        "consulta": consulta,
        "fragmentos_recuperados_de_este_documento": fragmentos_del_doc,
        "olvidado": en_chroma == 0 and en_sqlite == 0 and fragmentos_del_doc == 0,
        "busqueda": resultado.como_dict() if resultado else None,
    }

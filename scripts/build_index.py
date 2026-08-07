"""Reconstruye el índice RAG desde `dataset/textos/`.

El jurado normalmente NO corre esto: el índice viene construido y versionado en el
repo precisamente para no gastar su cuota de 15 minutos (compuerta G2). Está aquí
para que el proceso sea reproducible y auditable.

    python scripts/build_index.py             # incremental: salta lo ya indexado
    python scripts/build_index.py --limpiar   # borra el índice y lo rehace
    python scripts/build_index.py --solo apendicitis
"""

import _bootstrap  # noqa: F401

import argparse
import json
import shutil
import time
from pathlib import Path

from app.config import get_settings
from app.rag import embedder, pipeline, store
from app.store import db

ANCHO = 78


def cargar_manifiesto(s) -> dict[str, dict]:
    ruta = s.dir_textos / "manifiesto.json"
    if not ruta.exists():
        raise SystemExit(
            "Falta dataset/textos/manifiesto.json.\n"
            "Corra primero: python scripts/normalizar_corpus.py"
        )
    datos = json.loads(ruta.read_text("utf-8"))
    return {d["archivo"]: d for d in datos["documentos"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limpiar", action="store_true", help="borra el índice antes de construir")
    ap.add_argument("--solo", default="", help="indexa solo una carpeta (p. ej. apendicitis)")
    ap.add_argument("--limite", type=int, default=0, help="tope de documentos (para pruebas)")
    args = ap.parse_args()

    s = get_settings()
    s.crear_directorios()
    db.inicializar()
    manifiesto = cargar_manifiesto(s)

    if args.limpiar:
        print("Borrando índice anterior…")
        store.invalidar_indice()
        store._cliente.cache_clear()
        shutil.rmtree(s.dir_chroma, ignore_errors=True)
        s.dir_chroma.mkdir(parents=True, exist_ok=True)
        with db.transaccion() as con:
            con.execute("DELETE FROM documentos")

    entradas = [
        (s.dir_textos / rel, meta)
        for rel, meta in sorted(manifiesto.items())
        if (not args.solo or meta["carpeta"] == args.solo)
    ]
    if args.limite:
        entradas = entradas[: args.limite]

    print(f"\nÍndice RAG · {len(entradas)} documentos · modelo {s.embed_model}")
    print("Descargando el modelo de embeddings si hace falta (~220 MB la primera vez)…")
    t_modelo = time.perf_counter()
    embedder.precalentar()
    print(f"Modelo listo en {time.perf_counter() - t_modelo:.1f} s")
    print("─" * ANCHO)

    t0 = time.perf_counter()
    conteo = {"disponible": 0, "duplicado": 0, "sin_texto": 0, "error": 0}
    fragmentos = 0
    incidencias: list[str] = []

    for i, (ruta, meta) in enumerate(entradas, start=1):
        if not ruta.exists():
            print(f"[{i:3d}/{len(entradas)}] falta en disco: {meta['archivo']}")
            conteo["error"] += 1
            continue

        resumen = pipeline.indexar(
            ruta,
            titulo=meta["titulo"],
            procedimiento=meta["procedimiento"],
            carpeta=meta["carpeta"],
            origen="base",
        )
        conteo[resumen.estado] = conteo.get(resumen.estado, 0) + 1
        fragmentos += resumen.fragmentos

        marca = {"disponible": "ok", "duplicado": "dup", "sin_texto": "SIN TEXTO", "error": "ERROR"}
        print(
            f"[{i:3d}/{len(entradas)}] {marca.get(resumen.estado, resumen.estado):<9} "
            f"{resumen.paginas:>4}p {resumen.fragmentos:>5}f {resumen.segundos:>6.1f}s  "
            f"{resumen.titulo[:44]}",
            flush=True,  # sin esto, redirigir a un archivo oculta el progreso 10 minutos
        )
        if resumen.estado in ("sin_texto", "error", "duplicado"):
            incidencias.append(f"  {resumen.estado:<10} {meta['archivo']}\n             {resumen.detalle}")
        if resumen.paginas_sin_texto and resumen.estado == "disponible":
            incidencias.append(
                f"  parcial    {meta['archivo']}\n"
                f"             {len(resumen.paginas_sin_texto)} de {resumen.paginas} páginas sin capa de texto"
            )

    version = pipeline.finalizar_cambio(
        "reconstruccion", {"documentos": conteo["disponible"], "fragmentos": fragmentos}
    )
    total = time.perf_counter() - t0

    print("─" * ANCHO)
    print(
        f"{conteo['disponible']} indexados · {conteo['duplicado']} duplicados · "
        f"{conteo['sin_texto']} sin capa de texto · {conteo['error']} con error"
    )
    print(f"{fragmentos} fragmentos · {total / 60:.1f} min · kb_version={version}")
    estado = store.estado()
    print(f"Chroma: {estado['fragmentos']} fragmentos de {estado['documentos']} documentos")
    peso = sum(f.stat().st_size for f in Path(s.dir_chroma).rglob("*") if f.is_file())
    print(f"Índice en disco: {peso / 1e6:.0f} MB")

    if incidencias:
        print(f"\nIncidencias ({len(incidencias)}):")
        print("\n".join(incidencias))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

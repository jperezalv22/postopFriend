"""Diagnóstico de entorno en ~10 s. Es el primer paso del README.

Contesta una sola pregunta: «¿esto va a arrancar?». Y si no, dice exactamente
qué falta y con qué comando se arregla. Existe para que la compuerta G2 falle
temprano y con un mensaje claro, en vez de tarde y con un stack trace.

    python scripts/doctor.py
    python scripts/doctor.py --sin-red    # omite las comprobaciones que llaman a Groq
"""

import _bootstrap  # noqa: F401

import argparse
import importlib.metadata as md
import platform
import sys
import time

from app.config import get_settings

OK, AVISO, FALLO = "  OK  ", " AVISO", " FALLO"
_resultados: list[tuple[str, str, str]] = []


def marcar(estado: str, titulo: str, detalle: str = "") -> None:
    _resultados.append((estado, titulo, detalle))
    print(f"[{estado}] {titulo}" + (f"\n         {detalle}" if detalle else ""))


def revisar_python() -> None:
    v = sys.version_info
    detalle = f"{platform.python_version()} · {platform.system()} {platform.release()}"
    if v >= (3, 11):
        marcar(OK, "Python", detalle)
    else:
        marcar(FALLO, "Python", f"{detalle} — se requiere 3.11 o superior")


def revisar_entorno_virtual() -> None:
    en_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if en_venv:
        marcar(OK, "Entorno virtual", sys.prefix)
    else:
        marcar(
            AVISO,
            "Entorno virtual",
            "Está usando el Python del sistema. Recomendado: python -m venv .venv",
        )


def revisar_dependencias() -> None:
    requeridas = [
        "fastapi", "uvicorn", "groq", "pydantic-settings", "PyYAML", "openpyxl",
        "PyMuPDF", "python-docx", "chromadb", "fastembed", "onnxruntime",
        "rank-bm25", "edge-tts", "numpy", "python-multipart",
    ]
    faltantes = []
    for paquete in requeridas:
        try:
            md.version(paquete)
        except md.PackageNotFoundError:
            faltantes.append(paquete)
    if faltantes:
        marcar(
            FALLO,
            f"Dependencias ({len(requeridas) - len(faltantes)}/{len(requeridas)})",
            f"Faltan: {', '.join(faltantes)}\n         Solución: pip install -r requirements.txt",
        )
    else:
        marcar(OK, f"Dependencias ({len(requeridas)}/{len(requeridas)})", "todas instaladas")


def revisar_dataset(s) -> None:
    if not s.dir_textos.is_dir():
        marcar(FALLO, "Corpus clínico", f"No existe {s.dir_textos}")
        return
    documentos = [p for p in s.dir_textos.rglob("*") if p.is_file()]
    xlsx = list(s.dir_dataset.glob("*.xlsx"))
    estado = OK if len(documentos) >= 100 and len(xlsx) == 4 else AVISO
    marcar(estado, "Corpus clínico", f"{len(documentos)} documentos · {len(xlsx)} xlsx del dataset")


def revisar_indice(s) -> None:
    sqlite_chroma = s.dir_chroma / "chroma.sqlite3"
    if not sqlite_chroma.exists():
        marcar(
            AVISO,
            "Índice RAG",
            "No hay índice. Solución: python scripts/build_index.py\n"
            "         (el repo lo trae construido; solo hace falta si lo borró)",
        )
        return
    try:
        from app.store import db
        db.inicializar()
        from app.rag import store

        e = store.estado()
        marcar(
            OK,
            "Índice RAG",
            f"{e['fragmentos']} fragmentos de {e['documentos']} documentos · kb_version={e['kb_version']}",
        )
    except Exception as ex:
        marcar(FALLO, "Índice RAG", f"el índice existe pero no se pudo abrir: {ex}")


def revisar_modelo_embeddings(s) -> None:
    from app.rag import embedder

    if embedder.modelo_descargado():
        peso = sum(f.stat().st_size for f in s.dir_modelos.rglob("*") if f.is_file())
        marcar(OK, "Modelo de embeddings", f"{s.embed_model} en caché · {peso / 1e6:.0f} MB")
    else:
        marcar(
            AVISO,
            "Modelo de embeddings",
            f"{s.embed_model} sin descargar (~220 MB la primera vez que se use).\n"
            "         Solución: python scripts/precalentar.py",
        )


def revisar_clave(s) -> None:
    if not s.groq_api_key:
        marcar(
            FALLO,
            "GROQ_API_KEY",
            "Sin definir. Solución: copie .env.example a .env y pegue su clave.\n"
            "         Gratis en https://console.groq.com/keys",
        )
    elif not s.groq_api_key.startswith("gsk_"):
        marcar(AVISO, "GROQ_API_KEY", "Definida, pero no empieza por 'gsk_'. ¿Está completa?")
    else:
        marcar(OK, "GROQ_API_KEY", f"definida (…{s.groq_api_key[-4:]})")


def revisar_groq_vivo(s) -> None:
    if not s.groq_api_key:
        marcar(AVISO, "Conexión con Groq", "omitida: no hay clave")
        return
    try:
        from groq import Groq

        t0 = time.perf_counter()
        ids = {m.id for m in Groq(api_key=s.groq_api_key).models.list().data}
        ms = (time.perf_counter() - t0) * 1000
        faltan = [m for m in (s.llm_model, s.stt_model) if m not in ids]
        if faltan:
            marcar(FALLO, "Conexión con Groq", f"El catálogo no ofrece: {', '.join(faltan)}")
        else:
            marcar(OK, "Conexión con Groq", f"{len(ids)} modelos · {ms:.0f} ms · {s.llm_model} y {s.stt_model} disponibles")
    except Exception as e:  # red caída, clave inválida, cuota agotada…
        marcar(FALLO, "Conexión con Groq", f"{type(e).__name__}: {e}")


def revisar_tts(s) -> None:
    if s.tts_backend != "edge":
        marcar(AVISO, "TTS", f"backend={s.tts_backend} (no se comprueba aquí)")
        return
    try:
        import asyncio

        import edge_tts

        async def hay_voz() -> bool:
            voces = await edge_tts.list_voices()
            return any(v["ShortName"] == s.tts_voice for v in voces)

        if asyncio.run(hay_voz()):
            marcar(OK, "TTS (edge-tts)", f"voz {s.tts_voice} disponible")
        else:
            marcar(AVISO, "TTS (edge-tts)", f"la voz {s.tts_voice} no aparece en el catálogo")
    except Exception as e:
        marcar(AVISO, "TTS (edge-tts)", f"{type(e).__name__}: {e} — se usará el respaldo del navegador")


def revisar_vendor() -> None:
    """El VAD vive en el navegador: si falta un archivo, Python no se entera."""
    from app.voice import vendor

    problemas = vendor.faltantes()
    if not problemas:
        marcar(
            OK,
            "Recursos de voz empaquetados",
            f"{len(vendor.REQUERIDOS)} archivos en app/static/vendor/ "
            f"(onnxruntime {vendor.VERSION_ONNXRUNTIME}, vad {vendor.VERSION_VAD})",
        )
        return
    marcar(
        FALLO,
        "Recursos de voz empaquetados",
        "\n         ".join(problemas)
        + "\n         arréglelo con: python scripts/vendorizar_voz.py",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-red", action="store_true", help="omite las comprobaciones en línea")
    args = ap.parse_args()

    s = get_settings()
    s.crear_directorios()

    print("\npostopFriend · diagnóstico de entorno")
    print("─" * 72)
    revisar_python()
    revisar_entorno_virtual()
    revisar_dependencias()
    revisar_dataset(s)
    revisar_modelo_embeddings(s)
    revisar_indice(s)
    revisar_vendor()
    revisar_clave(s)
    if not args.sin_red:
        revisar_groq_vivo(s)
        revisar_tts(s)
    print("─" * 72)

    fallos = [r for r in _resultados if r[0] == FALLO]
    avisos = [r for r in _resultados if r[0] == AVISO]
    if fallos:
        print(f"\n{len(fallos)} problema(s) que impiden arrancar. Resuélvalos y vuelva a correr esto.\n")
        return 1
    if avisos:
        print(f"\nTodo listo para arrancar, con {len(avisos)} aviso(s). Siguiente paso:")
    else:
        print("\nTodo en orden. Siguiente paso:")
    print("  uvicorn app.main:app --reload   →   http://127.0.0.1:8000\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

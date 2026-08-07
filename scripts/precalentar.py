"""Descarga el modelo de embeddings y deja el audio de los guiones fijos en caché.

Se corre en `setup.ps1`/`setup.sh` a propósito: si la descarga de 220 MB va a
fallar, que falle durante la preparación y no en el primer turno de la llamada,
con el jurado mirando 17 segundos de silencio sin explicación.

    python scripts/precalentar.py
    python scripts/precalentar.py --sin-voz
"""

import _bootstrap  # noqa: F401

import argparse
import asyncio
import time

from app.config import get_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-voz", action="store_true", help="omite la caché de audio")
    args = ap.parse_args()

    s = get_settings()
    s.crear_directorios()

    print(f"Modelo de embeddings: {s.embed_model}")
    t0 = time.perf_counter()
    from app.rag import embedder

    embedder.precalentar()
    peso = sum(f.stat().st_size for f in s.dir_modelos.rglob("*") if f.is_file())
    print(f"  listo en {time.perf_counter() - t0:.1f} s · {peso / 1e6:.0f} MB en data/modelos/")

    if args.sin_voz:
        return 0

    from app.agent.scripts_es_co import GUIONES_A_CACHEAR
    from app.voice import tts

    print(f"Guiones fijos: sintetizando {len(GUIONES_A_CACHEAR)} frases…")
    t0 = time.perf_counter()
    hechas = asyncio.run(tts.precachear(list(GUIONES_A_CACHEAR)))
    if hechas == len(GUIONES_A_CACHEAR):
        print(f"  {hechas} frases en caché en {time.perf_counter() - t0:.1f} s")
    else:
        print(f"  solo {hechas} de {len(GUIONES_A_CACHEAR)}: ¿hay conexión a internet?")
        print("  no es bloqueante; se sintetizarán durante la llamada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

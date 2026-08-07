"""Prueba la cadena de voz de punta a punta sin micrófono ni navegador.

Sintetiza una frase con la voz colombiana, la vuelve a transcribir con Whisper y
compara. Si el texto vuelve intacto, STT y TTS están bien y cualquier fallo
posterior es del navegador, no del servidor. Sirve para aislar la compuerta G4.

    python scripts/probar_voz.py
    python scripts/probar_voz.py --texto "me sale un liquidito amarillo de la herida"
"""

import _bootstrap  # noqa: F401

import argparse
import asyncio
import time

from app.config import get_settings
from app.voice import stt, tts

FRASE = (
    "Buenos días, le habla Sofía del programa de seguimiento del hospital. "
    "Lo llamo porque hace siete días le hicieron la apendicectomía. "
    "¿Cómo ha seguido con el dolor?"
)


async def main_async(texto: str) -> int:
    s = get_settings()
    s.crear_directorios()

    print(f"\nVoz: {s.tts_voice} · ritmo {s.tts_rate} · STT: {s.stt_model}")
    print("─" * 72)
    print(f"Texto a sintetizar:\n  {texto}\n")

    # ─── TTS con medición del primer trozo ───────────────────────────────────
    t0 = time.perf_counter()
    primer_trozo_ms = None
    trozos: list[bytes] = []
    async for trozo in tts.sintetizar_stream(texto):
        if primer_trozo_ms is None:
            primer_trozo_ms = (time.perf_counter() - t0) * 1000
        trozos.append(trozo)
    audio = b"".join(trozos)
    total_ms = (time.perf_counter() - t0) * 1000

    if not audio:
        print("TTS no produjo audio. Revise la conexión a internet.")
        return 1

    salida = s.dir_data / "prueba_voz.mp3"
    salida.write_bytes(audio)
    print(f"TTS  primer trozo en {primer_trozo_ms:.0f} ms · audio completo en {total_ms:.0f} ms")
    print(f"     {len(audio) / 1024:.0f} KB · {len(trozos)} trozos · guardado en {salida}")

    # ─── Caché de guiones fijos ──────────────────────────────────────────────
    t0 = time.perf_counter()
    await tts.sintetizar(texto, cachear=True)
    frio = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    await tts.sintetizar(texto, cachear=True)
    caliente = (time.perf_counter() - t0) * 1000
    print(f"     caché de guiones: {frio:.0f} ms en frío → {caliente:.0f} ms en caliente")

    # ─── STT sobre el audio que acabamos de generar ──────────────────────────
    transcripcion = await stt.transcribir(audio, nombre="prueba.mp3")
    print(f"\nSTT  {transcripcion.ms:.0f} ms")
    print(f"     {transcripcion.texto or '(vacío) ' + transcripcion.motivo}")

    palabras_ida = set(texto.lower().split())
    palabras_vuelta = set(transcripcion.texto.lower().split())
    if palabras_ida:
        coincidencia = len(palabras_ida & palabras_vuelta) / len(palabras_ida)
        print(f"\nCoincidencia de palabras en el viaje de ida y vuelta: {coincidencia:.0%}")
    print("─" * 72)
    print("Cadena de voz operativa.\n" if transcripcion.texto else "Revise el STT.\n")
    return 0 if transcripcion.texto else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--texto", default=FRASE)
    args = ap.parse_args()
    return asyncio.run(main_async(args.texto))


if __name__ == "__main__":
    raise SystemExit(main())

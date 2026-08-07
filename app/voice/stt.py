"""Transcripción con Groq `whisper-large-v3-turbo`.

~200–400 ms para enunciados cortos, mismo proveedor que el LLM — una sola clave
para el jurado, que es un problema menos en la compuerta G2 — y admite un `prompt`
de sesgo léxico.

Ese `prompt` importa más de lo que parece. Whisper transcribe «apendicectomía»
como «apendicetomía» o «apendice ectomia», y no tiene ni idea de qué es un
«chuzón». El glosario clínico y regional le da el vocabulario por adelantado, y
una palabra mal transcrita es una variable clínica que el extractor no ve.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass

from app.config import get_settings

log = logging.getLogger("postopfriend.voz")

# Sesgo léxico: términos del procedimiento y jerga colombiana de síntomas.
# Whisper acepta hasta 224 tokens de prompt; se mantiene por debajo a propósito.
PROMPT_SESGO = (
    "Llamada de seguimiento postoperatorio en Colombia. "
    "Términos: apendicectomía, colecistectomía, colectomía, mastectomía, "
    "reemplazo de cadera, herida quirúrgica, dehiscencia, secreción purulenta, "
    "eritema, fiebre, escalofríos, náuseas, drenaje, puntos, cicatriz. "
    "Habla coloquial: maluco, guayabo, chuzón, punzada, me late, ardor, calentura, "
    "fiebrecita, tembladera, flojera, mareado, no he podido obrar, me sale materia, "
    "se me abrió, aguantable, un tris, harto, trasnochada."
)

# Whisper alucina texto sobre silencio o ruido. Estas son sus muletillas típicas:
# devolverlas como si el paciente las hubiera dicho contamina la extracción clínica.
ALUCINACIONES = {
    "gracias por ver el video", "subtítulos realizados por la comunidad de amara.org",
    "¡suscríbete al canal!", "amara.org", "subtitulado por", "gracias por vernos",
    "thanks for watching", "subtitles by", "www.mooji.org", "¡gracias!", "gracias.",
    "you", ".", "..", "...",
}


@dataclass
class Transcripcion:
    texto: str
    duracion_audio_s: float
    ms: float
    vacia: bool = False
    motivo: str = ""


def _es_alucinacion(texto: str) -> bool:
    limpio = texto.strip().lower().rstrip(".!¡?¿ ")
    return not limpio or limpio in {a.rstrip(". ") for a in ALUCINACIONES}


def _cliente():
    from groq import Groq

    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("Falta GROQ_API_KEY: copie .env.example a .env")
    return Groq(api_key=s.groq_api_key)


def transcribir_bytes(audio: bytes, nombre: str = "turno.webm",
                      duracion_s: float = 0.0) -> Transcripcion:
    """Transcribe un enunciado. Nunca lanza: un fallo vuelve como transcripción vacía."""
    import time

    s = get_settings()
    t0 = time.perf_counter()

    if len(audio) < 2000:  # menos de ~0.1 s: el VAD disparó con un ruido
        return Transcripcion("", duracion_s, 0.0, vacia=True, motivo="audio demasiado corto")

    try:
        archivo = io.BytesIO(audio)
        archivo.name = nombre
        respuesta = _cliente().audio.transcriptions.create(
            file=archivo,
            model=s.stt_model,
            language="es",
            temperature=0.0,  # sin creatividad: se quiere lo que se dijo, no lo que suena bien
            prompt=PROMPT_SESGO,
            response_format="json",
        )
        texto = (getattr(respuesta, "text", "") or "").strip()
    except Exception as e:
        log.warning("STT falló: %s", e)
        return Transcripcion("", duracion_s, (time.perf_counter() - t0) * 1000,
                             vacia=True, motivo=f"error de transcripción: {type(e).__name__}")

    ms = (time.perf_counter() - t0) * 1000
    if _es_alucinacion(texto):
        return Transcripcion("", duracion_s, ms, vacia=True,
                             motivo="transcripción vacía o alucinada sobre silencio")
    return Transcripcion(texto, duracion_s, ms)


async def transcribir(audio: bytes, nombre: str = "turno.webm",
                      duracion_s: float = 0.0) -> Transcripcion:
    """Versión asíncrona: el SDK de Groq es síncrono y bloquearía el bucle de eventos."""
    return await asyncio.to_thread(transcribir_bytes, audio, nombre, duracion_s)

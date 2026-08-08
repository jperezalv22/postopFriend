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
import re
import unicodedata
from dataclasses import dataclass

from app.config import get_settings

log = logging.getLogger("postopfriend.voz")

# Sesgo léxico: términos del procedimiento y jerga colombiana de síntomas.
# Whisper acepta hasta 224 tokens de prompt; se mantiene por debajo a propósito.
#
# La lista va pelada —sin una sola frase, sin dos puntos, sin punto final— y eso
# no es cuestión de estilo. El prompt anterior abría con «Llamada de seguimiento
# postoperatorio en Colombia. Términos: … Habla coloquial: …», y Whisper
# continuaba esa prosa en vez de transcribir. Medido sobre una tanda de 9
# enunciados de una llamada real, 3 de los 7 turnos aceptados salieron
# contaminados y entraron al diálogo como habla del paciente:
#
#     «Llamada de marco 36.»                        (había dicho «marcó un 36»)
#     «Términos de secundaria, si me sale sangre.»
#     (y un tercero que salió vacío por el mismo motivo)
#
# Ninguno lo paraba `_es_eco_del_prompt`: la contaminación era parcial —dos
# palabras del prompt pegadas al principio— y la regla exigía que el prompt
# dominara el texto entero.
#
# Lo que sesga a Whisper es el vocabulario, no la narrativa. Una lista separada
# por comas le da las palabras igual y no le deja una frase que continuar. Las
# expresiones de varias palabras («no he podido obrar») quedan reducidas a su
# palabra distintiva por la misma razón: cuanto menos parezca prosa, menos hay
# que continuar.
PROMPT_SESGO = (
    "apendicectomía, colecistectomía, colectomía, mastectomía, reemplazo de cadera, "
    "herida quirúrgica, dehiscencia, secreción purulenta, eritema, fiebre, escalofríos, "
    "náuseas, drenaje, puntos, cicatriz, maluco, guayabo, chuzón, punzada, ardor, "
    "calentura, fiebrecita, tembladera, flojera, mareado, obrar, materia, aguantable, "
    "un tris, harto, trasnochada"
)

# La prosa que el prompt llevaba hasta que se midió lo que costaba. Ya no se le
# manda a Whisper, pero se sigue vigilando aquí: es la forma exacta que tenía la
# contaminación observada, y volver a meter una frase en el prompt es un cambio de
# una línea que nadie recordaría deshacer.
_PROSA_RETIRADA = "Llamada de seguimiento postoperatorio en Colombia. Términos: Habla coloquial:"

# Aperturas ancladas al principio del texto. Es donde apareció la contaminación
# parcial que la regla de corrida no alcanzaba: «Llamada de marco 36» son solo dos
# palabras del prompt sobre cuatro, y con eso no se llega a ninguna corrida larga.
# Un paciente no abre un turno con ninguna de estas tres.
_APERTURAS_DEL_PROMPT = ("llamada de", "terminos", "habla coloquial")

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


def _tokens(texto: str) -> list[str]:
    """Palabras comparables: minúsculas, sin tildes y sin puntuación."""
    plano = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.findall(r"[a-z0-9]+", plano)


_TOKENS_PROSA = _tokens(_PROSA_RETIRADA)
_TOKENS_PROMPT = _tokens(PROMPT_SESGO)


def _corrida_comun(a: list[str], b: list[str]) -> int:
    """Palabras seguidas que aparecen igual y en el mismo orden en las dos listas."""
    mejor = 0
    previa = [0] * (len(b) + 1)
    for x in a:
        actual = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            if x == y:
                actual[j] = previa[j - 1] + 1
                mejor = max(mejor, actual[j])
        previa = actual
    return mejor


def _es_eco_del_prompt(texto: str) -> bool:
    """¿Whisper continuó el `prompt` de sesgo en vez de transcribir?

    Le pasa cuando el audio no trae información: en vez de devolver vacío sigue
    escribiendo el prompt, y como no es ninguna de sus muletillas conocidas,
    `_es_alucinacion` lo deja pasar y entra al diálogo como si lo hubiera dicho el
    paciente. Visto en una llamada real: «Carlos Llamada de seguimiento
    postoperatorio en C.» apareció como turno del paciente y descarriló el
    protocolo entero, porque el extractor y el router leen eso como habla.

    Tres reglas, con distinto listón:

    * **Apertura** (el texto *empieza* por prosa del prompt). Es la que faltaba.
      «Llamada de marco 36» son dos palabras del prompt sobre cuatro: no llega a
      ninguna corrida larga ni domina el texto, así que las otras dos reglas la
      dejaban pasar y entraba al diálogo como turno del paciente. Anclarla al
      principio es lo que la hace segura: nadie abre un turno con «Términos».
    * **Prosa** (3 palabras seguidas que cubran la mitad del texto). El andamiaje
      que el prompt llevaba antes. Ya no se le manda a Whisper, pero se sigue
      vigilando por si vuelve.
    * **Glosario** (5 palabras seguidas que cubran el 70 %). Aquí hace falta mucha
      más evidencia porque el glosario está hecho justamente de lo que el paciente
      dice: «fiebre, escalofríos, náuseas» son tres palabras seguidas del prompt y
      es una respuesta clínica legítima. Con el listón en cinco, sobrevive.

    Se rechaza, no se recorta. `extractor.evidencia_verificada()` comprueba que la
    cita textual esté en el diálogo, así que un texto mutilado aquí se convierte en
    una variable clínica descartada más adelante, y el fallo aparecería lejos de su
    causa.
    """
    tokens = _tokens(texto)
    if len(tokens) < 3:
        return False

    plano = " ".join(tokens)
    if any(plano.startswith(a) for a in _APERTURAS_DEL_PROMPT):
        return True

    corrida = _corrida_comun(tokens, _TOKENS_PROSA)
    if corrida >= 3 and corrida / len(tokens) >= 0.5:
        return True

    corrida = _corrida_comun(tokens, _TOKENS_PROMPT)
    return corrida >= 5 and corrida / len(tokens) >= 0.7


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
    if _es_eco_del_prompt(texto):
        log.warning("Whisper devolvió el prompt de sesgo, no el audio: %r", texto[:80])
        return Transcripcion("", duracion_s, ms, vacia=True,
                             motivo="el modelo devolvió el prompt de sesgo, no lo dicho")
    return Transcripcion(texto, duracion_s, ms)


async def transcribir(audio: bytes, nombre: str = "turno.webm",
                      duracion_s: float = 0.0) -> Transcripcion:
    """Versión asíncrona: el SDK de Groq es síncrono y bloquearía el bucle de eventos."""
    return await asyncio.to_thread(transcribir_bytes, audio, nombre, duracion_s)

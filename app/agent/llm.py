"""Capa delgada sobre el LLM: `chat()` y `chat_stream()`.

Existe por una razón concreta y declarada. El reto nombra `Llama 3.1 70B` y ese
modelo ya no existe en Groq (`scripts/check_models.py` lo demuestra); se usa su
sucesor directo, `llama-3.3-70b-versatile`. Si Source Meridian responde que no
vale, cambiar a Llama 3.2 local vía Ollama tiene que ser un cambio contenido en
este archivo y no una reescritura del agente. No es una característica que se
anuncie: es una póliza de seguro para la compuerta G3.

Todo lo que llama al modelo pasa por aquí, así que aquí también viven el reintento
ante el 429 del nivel gratuito (riesgo R2) y la contabilidad de tokens.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, AsyncIterator, Iterable

from app.config import get_settings

log = logging.getLogger("postopfriend.llm")

REINTENTOS = 5
ESPERA_BASE_S = 1.0
ESPERA_MAXIMA_S = 30.0

# Groq dice exactamente cuánto hay que esperar: «Please try again in 3.345s».
# Adivinar con retroceso exponencial cuando el servidor ya dio el número es
# desperdiciar cuota o esperar de más.
_ESPERA_SUGERIDA = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b", re.IGNORECASE)


@dataclass
class Respuesta:
    texto: str
    tokens_in: int = 0
    tokens_out: int = 0
    ms: float = 0.0
    modelo: str = ""
    truncada: bool = False
    incidencias: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _cliente():
    from groq import Groq

    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("Falta GROQ_API_KEY: copie .env.example a .env")
    return Groq(api_key=s.groq_api_key, max_retries=0)  # el reintento se maneja aquí


def _es_limite_de_cuota(e: Exception) -> bool:
    return "429" in str(e) or "rate_limit" in str(e).lower()


def espera_sugerida(e: Exception, intento: int) -> float:
    """Cuánto esperar antes de reintentar, según lo que diga el propio servidor."""
    m = _ESPERA_SUGERIDA.search(str(e))
    if m:
        segundos = float(m.group(1))
        if m.group(2).lower() == "ms":
            segundos /= 1000.0
        return min(segundos + 0.5, ESPERA_MAXIMA_S)  # margen para el reloj del servidor
    return min(ESPERA_BASE_S * (2**intento), ESPERA_MAXIMA_S)


def chat_sync(
    mensajes: list[dict[str, str]],
    max_tokens: int = 220,
    temperatura: float = 0.3,
    json_estricto: bool = False,
    modelo: str | None = None,
) -> Respuesta:
    """Una respuesta completa. Para el extractor y el router, que no van a voz."""
    s = get_settings()
    modelo = modelo or s.llm_model
    t0 = time.perf_counter()
    incidencias: list[str] = []

    extra: dict[str, Any] = {}
    if json_estricto:
        extra["response_format"] = {"type": "json_object"}

    ultimo_error: Exception | None = None
    for intento in range(REINTENTOS + 1):
        try:
            r = _cliente().chat.completions.create(
                model=modelo, messages=mensajes, max_tokens=max_tokens,
                temperature=temperatura, **extra,
            )
            eleccion = r.choices[0]
            uso = getattr(r, "usage", None)
            return Respuesta(
                texto=(eleccion.message.content or "").strip(),
                tokens_in=int(getattr(uso, "prompt_tokens", 0) or 0),
                tokens_out=int(getattr(uso, "completion_tokens", 0) or 0),
                ms=(time.perf_counter() - t0) * 1000,
                modelo=modelo,
                truncada=eleccion.finish_reason == "length",
                incidencias=incidencias,
            )
        except Exception as e:
            ultimo_error = e
            if intento < REINTENTOS and _es_limite_de_cuota(e):
                espera = espera_sugerida(e, intento)
                incidencias.append("cuota_agotada_reintento")
                log.warning("cuota de Groq agotada, reintento en %.1f s (intento %d/%d)",
                            espera, intento + 1, REINTENTOS)
                time.sleep(espera)
                continue
            break

    log.error("el LLM falló: %s", ultimo_error)
    incidencias.append(f"llm_error:{type(ultimo_error).__name__}")
    return Respuesta(texto="", ms=(time.perf_counter() - t0) * 1000,
                     modelo=modelo, incidencias=incidencias)


async def chat(mensajes: list[dict[str, str]], **kw: Any) -> Respuesta:
    """El SDK de Groq es síncrono: bloquearlo congelaría el WebSocket de la llamada."""
    return await asyncio.to_thread(lambda: chat_sync(mensajes, **kw))


async def chat_stream(
    mensajes: list[dict[str, str]],
    max_tokens: int = 220,
    temperatura: float = 0.4,
    modelo: str | None = None,
) -> AsyncIterator[str | Respuesta]:
    """Emite trozos de texto según llegan y, al final, un `Respuesta` con el uso.

    El streaming es lo que permite empezar a sintetizar la primera frase mientras
    el modelo sigue escribiendo la segunda.
    """
    s = get_settings()
    modelo = modelo or s.llm_model
    t0 = time.perf_counter()
    cola: asyncio.Queue = asyncio.Queue()
    bucle = asyncio.get_running_loop()

    def producir() -> None:
        texto: list[str] = []
        uso = None
        razon = ""
        try:
            flujo = _cliente().chat.completions.create(
                model=modelo, messages=mensajes, max_tokens=max_tokens,
                temperature=temperatura, stream=True,
            )
            for evento in flujo:
                if evento.usage is not None:
                    uso = evento.usage
                if not evento.choices:
                    continue
                delta = evento.choices[0].delta
                razon = evento.choices[0].finish_reason or razon
                if delta and delta.content:
                    texto.append(delta.content)
                    bucle.call_soon_threadsafe(cola.put_nowait, delta.content)
        except Exception as e:
            log.error("el streaming del LLM falló: %s", e)
            bucle.call_soon_threadsafe(
                cola.put_nowait,
                Respuesta(texto="".join(texto), modelo=modelo,
                          ms=(time.perf_counter() - t0) * 1000,
                          incidencias=[f"llm_error:{type(e).__name__}"]),
            )
            return
        bucle.call_soon_threadsafe(
            cola.put_nowait,
            Respuesta(
                texto="".join(texto),
                tokens_in=int(getattr(uso, "prompt_tokens", 0) or 0),
                tokens_out=int(getattr(uso, "completion_tokens", 0) or 0),
                ms=(time.perf_counter() - t0) * 1000,
                modelo=modelo,
                truncada=razon == "length",
            ),
        )

    tarea = asyncio.create_task(asyncio.to_thread(producir))
    try:
        while True:
            elemento = await cola.get()
            yield elemento
            if isinstance(elemento, Respuesta):
                break
    finally:
        await tarea


def contar_palabras(texto: str) -> int:
    return len(texto.split())


def unir_sistema(*bloques: Iterable[str]) -> str:
    partes = [b for grupo in bloques for b in grupo if b]
    return "\n\n".join(partes)

"""Capa delgada sobre el LLM: `chat()` y `chat_stream()`.

Existe por una razón concreta y declarada. El reto nombra `Llama 3.1 70B` y ese
modelo ya no existe en Groq (`scripts/check_models.py` lo demuestra); se usa su
sucesor directo, `llama-3.3-70b-versatile`. Si Source Meridian responde que no
vale, cambiar a Llama 3.2 local vía Ollama tiene que ser un cambio contenido en
este archivo y no una reescritura del agente. No es una característica que se
anuncie: es una póliza de seguro para la compuerta G3.

Todo lo que llama al modelo pasa por aquí, así que aquí también viven el reintento
ante el 429 del nivel gratuito (riesgo R2) y la contabilidad de tokens.

Por la misma razón vive aquí la ruta alterna hacia el mismo modelo (`LLM_BACKEND`,
ver app/config.py): la evaluación completa no cabe en el nivel gratuito y el Dev
Tier de Groq estaba cerrado. Va fijada a Groq sin sustitutos y comprobada contra la
respuesta. **La llamada al paciente usa siempre la ruta directa**: `chat_stream()`,
que es la del turno hablado, no la ofrece.
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
    # Quién ejecutó de verdad la inferencia. Con la ruta directa es siempre Groq;
    # con la de OpenRouter hay que preguntárselo a la respuesta y no darlo por hecho.
    proveedor: str = "Groq"


def modelo_en_uso() -> str:
    """El identificador que se va a mandar de verdad, no el que está declarado.

    Groq y OpenRouter nombran el mismo modelo distinto (`llama-3.3-70b-versatile`
    contra `meta-llama/llama-3.3-70b-instruct`). Registrar siempre el de Groq
    haría que los logs declararan un identificador que nunca se pidió, y la
    rúbrica contrasta las métricas reportadas contra los logs.
    """
    s = get_settings()
    return s.openrouter_model if s.llm_backend == "openrouter" else s.llm_model


@lru_cache(maxsize=1)
def _cliente():
    from groq import Groq

    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("Falta GROQ_API_KEY: copie .env.example a .env")
    return Groq(api_key=s.groq_api_key, max_retries=0)  # el reintento se maneja aquí


def _es_limite_de_cuota(e: Exception) -> bool:
    return "429" in str(e) or "rate_limit" in str(e).lower()


# ─── Ruta alterna: la inferencia de Groq, facturada por OpenRouter ──────────────
#
# Ver `llm_backend` en app/config.py para el porqué. Lo que importa aquí es que no
# es «otro proveedor»: es el mismo, fijado con `allow_fallbacks: False` y verificado
# en la respuesta. OpenRouter dice en cada respuesta quién la ejecutó, así que la
# comprobación es un dato, no una confianza.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROVEEDOR_EXIGIDO = "Groq"


class ProveedorInesperado(RuntimeError):
    """OpenRouter enrutó a un backend distinto de Groq. La medición no valdría."""


def _openrouter_sync(
    mensajes: list[dict[str, str]],
    max_tokens: int,
    temperatura: float,
    json_estricto: bool,
) -> tuple[str, int, int, bool, str]:
    import httpx

    s = get_settings()
    if not s.openrouter_api_key:
        raise RuntimeError(
            "Falta OPENROUTER_API_KEY. Solo hace falta para correr evals/ con "
            "LLM_BACKEND=openrouter; la app funciona con GROQ_API_KEY."
        )

    cuerpo: dict[str, Any] = {
        "model": s.openrouter_model,
        "messages": mensajes,
        "max_tokens": max_tokens,
        "temperature": temperatura,
        # Sin sustitutos: antes quedarse sin respuesta que medir contra otro backend.
        "provider": {"order": ["groq"], "allow_fallbacks": False},
    }
    if json_estricto:
        cuerpo["response_format"] = {"type": "json_object"}

    r = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {s.openrouter_api_key}"},
        json=cuerpo,
        timeout=120.0,
    )
    if r.status_code == 429:
        raise RuntimeError(f"429 rate_limit: {r.text[:300]}")
    r.raise_for_status()
    datos = r.json()

    proveedor = str(datos.get("provider") or "")
    if proveedor.lower() != PROVEEDOR_EXIGIDO.lower():
        raise ProveedorInesperado(
            f"la respuesta la sirvió «{proveedor or 'desconocido'}», no {PROVEEDOR_EXIGIDO}"
        )

    eleccion = datos["choices"][0]
    uso = datos.get("usage") or {}
    return (
        (eleccion.get("message", {}).get("content") or "").strip(),
        int(uso.get("prompt_tokens") or 0),
        int(uso.get("completion_tokens") or 0),
        eleccion.get("finish_reason") == "length",
        proveedor,
    )


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

    por_openrouter = s.llm_backend == "openrouter"

    ultimo_error: Exception | None = None
    for intento in range(REINTENTOS + 1):
        try:
            if por_openrouter:
                texto, t_in, t_out, truncada, proveedor = _openrouter_sync(
                    mensajes, max_tokens, temperatura, json_estricto
                )
            else:
                r = _cliente().chat.completions.create(
                    model=modelo, messages=mensajes, max_tokens=max_tokens,
                    temperature=temperatura, **extra,
                )
                eleccion = r.choices[0]
                uso = getattr(r, "usage", None)
                texto = (eleccion.message.content or "").strip()
                t_in = int(getattr(uso, "prompt_tokens", 0) or 0)
                t_out = int(getattr(uso, "completion_tokens", 0) or 0)
                truncada = eleccion.finish_reason == "length"
                proveedor = "Groq"

            return Respuesta(
                texto=texto, tokens_in=t_in, tokens_out=t_out,
                ms=(time.perf_counter() - t0) * 1000,
                modelo=modelo, truncada=truncada,
                incidencias=incidencias, proveedor=proveedor,
            )
        except ProveedorInesperado as e:
            # Reintentar no arregla un enrutado equivocado, y cachear el resultado
            # sería peor: metería en la tabla una medición de otro backend.
            log.error("%s", e)
            incidencias.append("llm_error:ProveedorInesperado")
            return Respuesta(texto="", ms=(time.perf_counter() - t0) * 1000,
                             modelo=modelo, incidencias=incidencias, proveedor="")
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
    if s.llm_backend != "groq":
        # Dicho en voz alta para que nadie mida latencia de voz creyendo que la ruta
        # de la evaluación estaba activa: el turno hablado siempre va directo a Groq.
        log.warning("LLM_BACKEND=%s no aplica al streaming: la voz va directa a Groq",
                    s.llm_backend)
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

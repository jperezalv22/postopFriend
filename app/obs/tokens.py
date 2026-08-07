"""Contabilidad de tokens y costo.

Los precios son los públicos de Groq. Se declaran aquí, en un solo sitio, con la
fecha en que se verificaron, y `scripts/report_metrics.py` los usa para generar la
tabla del README. Un número de costo inventado es una bandera de integridad, así
que la fuente y la fecha van pegadas al dato.
"""

from __future__ import annotations

from dataclasses import dataclass

FECHA_CONSULTA_PRECIOS = "2026-08-07"
FUENTE_PRECIOS = "https://groq.com/pricing"


@dataclass(frozen=True)
class PrecioLLM:
    entrada_por_millon: float
    salida_por_millon: float


# USD por millón de tokens.
PRECIOS_LLM = {
    "llama-3.3-70b-versatile": PrecioLLM(0.59, 0.79),
    "llama-3.1-8b-instant": PrecioLLM(0.05, 0.08),
}

# USD por hora de audio transcrito.
PRECIOS_STT = {
    "whisper-large-v3-turbo": 0.04,
    "whisper-large-v3": 0.111,
}

# edge-tts no cobra: es el servicio de síntesis de Edge, sin API key.
PRECIO_TTS_POR_CARACTER = 0.0


def costo_llm(modelo: str, tokens_in: int, tokens_out: int) -> float:
    p = PRECIOS_LLM.get(modelo)
    if p is None:
        return 0.0
    return tokens_in / 1e6 * p.entrada_por_millon + tokens_out / 1e6 * p.salida_por_millon


def costo_stt(modelo: str, segundos_audio: float) -> float:
    return segundos_audio / 3600.0 * PRECIOS_STT.get(modelo, 0.0)


def costo_turno(modelo_llm: str, modelo_stt: str, tokens_in: int, tokens_out: int,
                segundos_audio: float) -> dict[str, float]:
    llm = costo_llm(modelo_llm, tokens_in, tokens_out)
    stt = costo_stt(modelo_stt, segundos_audio)
    return {"llm": llm, "stt": stt, "tts": 0.0, "total": llm + stt}


def formatear_usd(valor: float) -> str:
    """Los costos por turno viven en el orden de 10⁻⁴ USD: redondear a 2 decimales los borra."""
    if valor == 0:
        return "$0"
    if valor < 0.01:
        return f"${valor:.6f}"
    return f"${valor:.4f}"

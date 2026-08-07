"""Síntesis de voz con edge-tts, voz `es-CO-SalomeNeural`.

**Acento colombiano nativo**, sin API key y sin descargar modelo. Para un jurado
colombiano evaluando llamadas a pacientes colombianos, que la voz suene de Bogotá
y no de Madrid o Ciudad de México vale más que cualquier ganancia de prosodia de
un servicio de pago. Además, un servicio de pago significaría una clave más en el
`.env` del jurado o un demo grabado que no coincide con lo que él corre.

Los guiones fijos del protocolo —apertura, transiciones, cierres— se cachean en
disco como MP3. Salen en <5 ms en vez de ~350 ms y, sobre todo, la apertura de la
llamada deja de depender de que haya red en ese instante (riesgos R2 y R3).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import AsyncIterator

from app.config import get_settings

log = logging.getLogger("postopfriend.voz")

# Se sintetiza lo que el paciente debe oír, no lo que se muestra en pantalla.
_MARCADORES = re.compile(r"\s*\[F\d+\]")
_MARKDOWN = re.compile(r"[*_`#]+")


def limpiar_para_voz(texto: str) -> str:
    """Quita marcadores de cita y markdown antes de sintetizar.

    El paciente no debe oír «corchete efe uno» ni «asterisco asterisco». Los
    marcadores siguen en pantalla y en el acta, que es donde sirven para verificar.
    """
    texto = _MARCADORES.sub("", texto)
    texto = _MARKDOWN.sub("", texto)
    return re.sub(r"\s{2,}", " ", texto).strip()


def _clave_cache(texto: str, voz: str, ritmo: str) -> str:
    crudo = f"{voz}|{ritmo}|{texto}".encode("utf-8")
    return hashlib.sha256(crudo).hexdigest()[:24]


async def sintetizar(texto: str, cachear: bool = False) -> bytes:
    """Sintetiza una frase completa y devuelve el MP3 entero."""
    trozos = [t async for t in sintetizar_stream(texto, cachear=cachear)]
    return b"".join(trozos)


async def sintetizar_stream(texto: str, cachear: bool = False) -> AsyncIterator[bytes]:
    """MP3 en trozos, según los va produciendo el servicio.

    Se emite el primer trozo en cuanto llega (~300–500 ms) en vez de esperar el
    audio completo: el cliente empieza a reproducir antes y la latencia que mide
    la rúbrica baja de forma directa.
    """
    import edge_tts

    s = get_settings()
    limpio = limpiar_para_voz(texto)
    if not limpio:
        return

    ruta_cache = None
    if cachear:
        s.dir_tts_cache.mkdir(parents=True, exist_ok=True)
        ruta_cache = s.dir_tts_cache / f"{_clave_cache(limpio, s.tts_voice, s.tts_rate)}.mp3"
        if ruta_cache.exists():
            yield ruta_cache.read_bytes()
            return

    acumulado = bytearray()
    try:
        comunicador = edge_tts.Communicate(limpio, s.tts_voice, rate=s.tts_rate)
        async for evento in comunicador.stream():
            if evento["type"] == "audio" and evento.get("data"):
                datos = evento["data"]
                if ruta_cache is not None:
                    acumulado.extend(datos)
                yield datos
    except Exception as e:
        # Sin voz no hay compuerta G4. El cliente recibe el aviso y cae al
        # `speechSynthesis` del navegador, que suena peor pero suena.
        log.error("edge-tts falló: %s", e)
        raise

    if ruta_cache is not None and acumulado:
        ruta_cache.write_bytes(bytes(acumulado))


async def precachear(frases: list[str]) -> int:
    """Deja los guiones fijos en disco. Se llama al arrancar el servidor."""
    hechas = 0
    for frase in frases:
        try:
            await sintetizar(frase, cachear=True)
            hechas += 1
        except Exception:
            break  # sin red: se reintentará en la llamada, no se bloquea el arranque
    return hechas


async def voz_disponible() -> bool:
    try:
        import edge_tts

        s = get_settings()
        voces = await edge_tts.list_voices()
        return any(v["ShortName"] == s.tts_voice for v in voces)
    except Exception:
        return False


def sintetizar_sync(texto: str, cachear: bool = False) -> bytes:
    """Para scripts y pruebas fuera de un bucle de eventos."""
    return asyncio.run(sintetizar(texto, cachear=cachear))

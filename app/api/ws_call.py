"""WebSocket de la llamada: audio del paciente entra, voz del agente sale.

Protocolo. El cliente manda JSON de control y el audio como un binario suelto
precedido de su cabecera JSON. El servidor responde con JSON y con trozos de MP3.

    cliente → servidor
      {"tipo":"iniciar",      "paciente_id":…, "dia_postop":7}
      {"tipo":"audio",        "t_fin_habla":…, "duracion_s":…}   + binario WAV
      {"tipo":"texto",        "texto":…, "t_fin_habla":…}        modo sin micrófono
      {"tipo":"primer_audio", "turno_idx":n, "t":…}              ACK: empezó a sonar
      {"tipo":"barge_in",     "turno_idx":n}
      {"tipo":"colgar"}

    servidor → cliente
      {"tipo":"listo"|"estado"|"turno"|"audio_inicio"|"audio_fin"|"latencia"|"error"}
      trozos binarios de MP3 entre `audio_inicio` y `audio_fin`

`t_fin_habla` y el `t` del ACK vienen los dos de `performance.now()` del navegador.
La latencia oficial es su diferencia: medirla en el servidor descontaría el viaje
de red y el arranque del audio, es decir, la maquillaría a favor propio.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent import generator, scripts_es_co
from app.config import get_settings
from app.obs.logger import ahora_iso, registrar_turno
from app.obs.trace import TurnTrace
from app.store import db
from app.store.patients import Ficha, construir_ficha
from app.voice import stt, tts
from app.voice.segmenter import Segmentador

log = logging.getLogger("postopfriend.llamada")
router = APIRouter()


@dataclass
class Sesion:
    call_id: str
    ws: WebSocket
    ficha: Ficha | None = None
    turno_idx: int = 0
    dialogo: list[dict[str, str]] = field(default_factory=list)
    turno_actual: int = -1          # turno cuyo audio se está reproduciendo
    interrumpido: set[int] = field(default_factory=set)
    trazas: dict[int, TurnTrace] = field(default_factory=dict)

    async def enviar(self, **datos: Any) -> None:
        await self.ws.send_json(datos)

    def anotar(self, hablante: str, texto: str) -> int:
        idx = self.turno_idx
        self.turno_idx += 1
        self.dialogo.append({"hablante": hablante, "texto": texto, "turno_idx": idx})
        return idx


@router.websocket("/ws/call/{call_id}")
async def llamada(ws: WebSocket, call_id: str) -> None:
    await ws.accept()
    sesion = Sesion(call_id=call_id or f"call_{uuid.uuid4().hex[:12]}", ws=ws)
    log.info("llamada abierta: %s", sesion.call_id)

    try:
        while True:
            mensaje = await ws.receive()

            if mensaje.get("type") == "websocket.disconnect":
                break

            if (crudo := mensaje.get("text")) is not None:
                import json

                await _manejar_control(sesion, json.loads(crudo))
            elif (binario := mensaje.get("bytes")) is not None:
                await _manejar_audio(sesion, binario)

    except WebSocketDisconnect:
        log.info("el cliente colgó: %s", sesion.call_id)
    except Exception as e:
        log.exception("la llamada %s murió: %s", sesion.call_id, e)
        try:
            await sesion.enviar(tipo="error", mensaje=str(e))
        except Exception:
            pass
    finally:
        _cerrar_llamada(sesion)


# ─── Control ─────────────────────────────────────────────────────────────────

_audio_pendiente: dict[str, dict[str, Any]] = {}


async def _manejar_control(sesion: Sesion, datos: dict[str, Any]) -> None:
    tipo = datos.get("tipo")

    if tipo == "iniciar":
        await _iniciar(sesion, datos)

    elif tipo == "audio":
        # Cabecera del binario que viene inmediatamente después.
        _audio_pendiente[sesion.call_id] = datos

    elif tipo == "texto":
        texto = (datos.get("texto") or "").strip()
        if texto:
            await _procesar_turno(sesion, texto, datos.get("t_fin_habla"), 0.0, TurnTrace(sesion.call_id, 0))

    elif tipo == "primer_audio":
        # El único punto donde se cierra la medición de latencia.
        idx = int(datos.get("turno_idx", -1))
        traza = sesion.trazas.get(idx)
        if traza is not None:
            traza.t_primer_audio_cliente = float(datos.get("t", 0.0))
            registrar_turno(traza)
            await sesion.enviar(
                tipo="latencia", turno_idx=idx,
                ms=traza.latencia_ms, etapas=traza.desglose(),
                tokens_in=traza.tokens_in, tokens_out=traza.tokens_out,
            )

    elif tipo == "barge_in":
        idx = int(datos.get("turno_idx", -1))
        sesion.interrumpido.add(idx)
        traza = sesion.trazas.get(idx)
        if traza is not None:
            traza.incidencia("interrumpido_por_el_paciente")
        log.info("%s: el paciente interrumpió el turno %d", sesion.call_id, idx)

    elif tipo == "colgar":
        await sesion.enviar(tipo="estado", valor="colgado")
        await sesion.ws.close()


async def _iniciar(sesion: Sesion, datos: dict[str, Any]) -> None:
    paciente_id = str(datos.get("paciente_id") or "")
    dia = int(datos.get("dia_postop") or 7)
    sesion.ficha = construir_ficha(paciente_id, dia)

    if sesion.ficha is None:
        await sesion.enviar(tipo="error", mensaje=f"paciente desconocido: {paciente_id}")
        return

    with db.transaccion() as con:
        con.execute(
            """INSERT OR REPLACE INTO llamadas
                 (call_id, paciente_id, dia_postop, procedimiento, inicio_ts, estado, modelo_llm)
               VALUES (?,?,?,?,?,'en_curso',?)""",
            (sesion.call_id, paciente_id, dia, sesion.ficha.paciente.procedimiento,
             ahora_iso(), get_settings().llm_model),
        )

    await sesion.enviar(tipo="listo", call_id=sesion.call_id, paciente=sesion.ficha.como_dict())

    # El agente habla primero: es él quien llama (supuesto 1 del README).
    apertura = scripts_es_co.apertura(sesion.ficha)
    idx = sesion.anotar("agente", apertura)
    traza = TurnTrace(sesion.call_id, idx)
    traza.estado_flujo = "Apertura"
    sesion.trazas[idx] = traza
    await _hablar(sesion, apertura, idx, traza, cachear=True)


# ─── Audio entrante ──────────────────────────────────────────────────────────

async def _manejar_audio(sesion: Sesion, audio: bytes) -> None:
    cabecera = _audio_pendiente.pop(sesion.call_id, {})
    t_fin_habla = cabecera.get("t_fin_habla")
    duracion = float(cabecera.get("duracion_s") or 0.0)

    traza = TurnTrace(sesion.call_id, sesion.turno_idx)
    traza.t_fin_habla_cliente = float(t_fin_habla) if t_fin_habla is not None else None
    traza.audio_paciente_s = duracion
    traza.modelo = get_settings().llm_model

    await sesion.enviar(tipo="estado", valor="procesando")

    with traza.medir("stt"):
        transcripcion = await stt.transcribir(audio, nombre="turno.wav", duracion_s=duracion)

    if transcripcion.vacia:
        # Nunca rellenar lo que no se entendió: se pide repetición.
        traza.incidencia(f"audio_degradado:{transcripcion.motivo}")
        idx = sesion.anotar("agente", scripts_es_co.NO_ESCUCHE)
        sesion.trazas[idx] = traza
        traza.turno_idx = idx
        await sesion.enviar(tipo="incidencia", motivo=transcripcion.motivo)
        await _hablar(sesion, scripts_es_co.NO_ESCUCHE, idx, traza, cachear=True)
        return

    await _procesar_turno(sesion, transcripcion.texto, t_fin_habla, duracion, traza)


async def _procesar_turno(
    sesion: Sesion, texto: str, t_fin_habla: Any, duracion: float, traza: TurnTrace
) -> None:
    idx_paciente = sesion.anotar("paciente", texto)
    await sesion.enviar(tipo="turno", hablante="paciente", texto=texto, turno_idx=idx_paciente)

    idx = sesion.anotar("agente", "")
    traza.turno_idx = idx
    if traza.t_fin_habla_cliente is None and t_fin_habla is not None:
        traza.t_fin_habla_cliente = float(t_fin_habla)
    traza.audio_paciente_s = traza.audio_paciente_s or duracion
    sesion.trazas[idx] = traza

    respuesta = await generator.responder(sesion.ficha, sesion.dialogo, traza)
    sesion.dialogo[-1]["texto"] = respuesta
    await _hablar(sesion, respuesta, idx, traza)


# ─── Salida hablada ──────────────────────────────────────────────────────────

async def _hablar(
    sesion: Sesion, texto: str, turno_idx: int, traza: TurnTrace, cachear: bool = False
) -> None:
    """Sintetiza y emite. Corta en seco si el paciente interrumpe (barge-in)."""
    await sesion.enviar(tipo="turno", hablante="agente", texto=texto, turno_idx=turno_idx)
    await sesion.enviar(tipo="estado", valor="hablando")
    await sesion.enviar(tipo="audio_inicio", turno_idx=turno_idx, formato="mp3")

    sesion.turno_actual = turno_idx
    traza.iniciar("tts")
    primer_trozo = True
    try:
        async for trozo in tts.sintetizar_stream(texto, cachear=cachear):
            if turno_idx in sesion.interrumpido:
                log.info("%s: síntesis cortada por barge-in", sesion.call_id)
                break
            if primer_trozo:
                traza.marcar("tts_primer_trozo")
                primer_trozo = False
            await sesion.ws.send_bytes(trozo)
    except Exception as e:
        traza.incidencia(f"tts_error:{type(e).__name__}")
        await sesion.enviar(tipo="error_tts", mensaje=str(e), texto=texto)
    finally:
        traza.terminar("tts")

    await sesion.enviar(tipo="audio_fin", turno_idx=turno_idx)
    await sesion.enviar(tipo="estado", valor="escuchando")

    _guardar_turno(sesion, turno_idx, "agente", texto, traza)


def _guardar_turno(sesion: Sesion, idx: int, hablante: str, texto: str, traza: TurnTrace) -> None:
    import json

    with db.transaccion() as con:
        con.execute(
            """INSERT OR REPLACE INTO turnos
                 (call_id, turno_idx, hablante, texto, ts, latencia_ms, estado_flujo,
                  nivel_triage, tokens_in, tokens_out, incidencias_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sesion.call_id, idx, hablante, texto, ahora_iso(), traza.latencia_ms,
             traza.estado_flujo, traza.nivel_triage, traza.tokens_in, traza.tokens_out,
             json.dumps(traza.incidencias, ensure_ascii=False)),
        )


def _cerrar_llamada(sesion: Sesion) -> None:
    for idx, traza in sesion.trazas.items():
        # Un turno cuyo audio nunca empezó a sonar no tiene latencia medible; se
        # registra igual para no perder los tokens ni las incidencias.
        if traza.t_primer_audio_cliente is None and traza.t_fin_habla_cliente is not None:
            traza.incidencia("sin_ack_de_primer_audio")
            registrar_turno(traza)
    with db.transaccion() as con:
        con.execute(
            "UPDATE llamadas SET fin_ts = ?, turnos = ?, estado = 'completa' WHERE call_id = ?",
            (ahora_iso(), sesion.turno_idx, sesion.call_id),
        )
    log.info("llamada cerrada: %s (%d turnos)", sesion.call_id, sesion.turno_idx)

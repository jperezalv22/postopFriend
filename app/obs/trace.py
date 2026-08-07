"""Instrumentación de latencia por turno.

La rúbrica mide una cosa muy concreta: **desde que el paciente termina de hablar
hasta que empieza a sonar el audio del agente**. Los dos extremos ocurren en el
navegador, así que los dos se miden con el reloj del navegador y se envían por el
WebSocket. Medirlo en el servidor descontaría el viaje de red y el arranque del
audio, es decir, inflaría el número a favor propio — y el jurado lo contrasta
contra la sesión en vivo.

Los tiempos internos usan `time.perf_counter()` (monótono, inmune a que el reloj
del sistema se ajuste) y se guardan como milisegundos relativos al inicio del
turno, no como marcas absolutas.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def ahora_ms() -> float:
    """Reloj monótono en milisegundos. No usar para fechas, solo para diferencias."""
    return time.perf_counter() * 1000.0


@dataclass
class Etapa:
    nombre: str
    inicio_ms: float
    fin_ms: float | None = None

    @property
    def duracion_ms(self) -> float | None:
        return None if self.fin_ms is None else round(self.fin_ms - self.inicio_ms, 1)


@dataclass
class TurnTrace:
    """Traza de un turno. Se crea al recibir el audio y se cierra con el ACK del cliente."""

    call_id: str
    turno_idx: int

    # ─── Relojes del CLIENTE (llegan por WebSocket, en ms de `performance.now()`) ─
    t_fin_habla_cliente: float | None = None
    t_primer_audio_cliente: float | None = None

    # ─── Reloj del servidor ──────────────────────────────────────────────────
    t0_ms: float = field(default_factory=ahora_ms)
    etapas: dict[str, Etapa] = field(default_factory=dict)

    # ─── Contadores ──────────────────────────────────────────────────────────
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    rag_consultas: int = 0
    rag_hits: int = 0
    audio_paciente_s: float = 0.0

    # ─── Resultado ───────────────────────────────────────────────────────────
    modelo: str = ""
    estado_flujo: str = ""
    nivel_triage: str = ""
    incidencias: list[str] = field(default_factory=list)

    # ─── API de medición ─────────────────────────────────────────────────────

    def iniciar(self, etapa: str) -> None:
        self.etapas[etapa] = Etapa(etapa, ahora_ms())

    def terminar(self, etapa: str) -> float | None:
        e = self.etapas.get(etapa)
        if e is None:
            return None
        e.fin_ms = ahora_ms()
        return e.duracion_ms

    def medir(self, etapa: str) -> "_Medicion":
        """`with traza.medir("stt"): ...`"""
        return _Medicion(self, etapa)

    def marcar(self, etapa: str) -> None:
        """Hito instantáneo (p. ej. el primer token del LLM)."""
        self.etapas[etapa] = Etapa(etapa, ahora_ms(), ahora_ms())

    def incidencia(self, texto: str) -> None:
        if texto not in self.incidencias:
            self.incidencias.append(texto)

    def sumar_uso(self, uso: Any) -> None:
        """Acumula el `usage` que devuelve Groq en cada respuesta."""
        if uso is None:
            return
        self.tokens_in += int(getattr(uso, "prompt_tokens", 0) or 0)
        self.tokens_out += int(getattr(uso, "completion_tokens", 0) or 0)
        self.llm_calls += 1

    # ─── Lectura ─────────────────────────────────────────────────────────────

    @property
    def latencia_ms(self) -> float | None:
        """La métrica oficial: fin de habla → primer audio, en el reloj del cliente."""
        if self.t_fin_habla_cliente is None or self.t_primer_audio_cliente is None:
            return None
        return round(self.t_primer_audio_cliente - self.t_fin_habla_cliente, 1)

    @property
    def latencia_servidor_ms(self) -> float:
        """Lo que tardó el servidor. Sirve para el desglose, no para reportar la latencia."""
        return round(ahora_ms() - self.t0_ms, 1)

    def desglose(self) -> dict[str, float]:
        return {n: e.duracion_ms for n, e in self.etapas.items() if e.duracion_ms is not None}

    def como_dict(self) -> dict[str, Any]:
        """Lo que se escribe en `logs/turns.jsonl`.

        Sin transcripción: los logs de métricas no guardan texto del paciente.
        Observabilidad y datos clínicos van por caminos separados a propósito.
        """
        return {
            "call_id": self.call_id,
            "turno_idx": self.turno_idx,
            "latencia_ms": self.latencia_ms,
            "latencia_servidor_ms": self.latencia_servidor_ms,
            "etapas_ms": self.desglose(),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "llm_calls": self.llm_calls,
            "rag_consultas": self.rag_consultas,
            "rag_hits": self.rag_hits,
            "audio_paciente_s": round(self.audio_paciente_s, 2),
            "modelo": self.modelo,
            "estado_flujo": self.estado_flujo,
            "nivel_triage": self.nivel_triage,
            "incidencias": self.incidencias,
        }


class _Medicion:
    __slots__ = ("traza", "etapa")

    def __init__(self, traza: TurnTrace, etapa: str) -> None:
        self.traza = traza
        self.etapa = etapa

    def __enter__(self) -> "TurnTrace":
        self.traza.iniciar(self.etapa)
        return self.traza

    def __exit__(self, *exc: object) -> None:
        self.traza.terminar(self.etapa)

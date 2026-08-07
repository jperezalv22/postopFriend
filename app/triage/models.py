"""Tipos del triage: lo que el extractor llena y lo que el motor decide.

La apuesta central de esta solución vive en esta frontera. **El LLM no decide el
nivel.** El LLM lee la conversación y llena un `EstadoClinico` —dolor 6, fiebre
38.0, herida con secreción— y ahí termina su trabajo. El nivel lo calcula un motor
determinista y versionado a partir de esas variables.

Por qué importa. Si el LLM decidiera, la misma llamada podría dar amarillo hoy y
rojo mañana sin que nadie tocara nada, un cambio de prompt movería decisiones
clínicas sin dejar rastro, y no habría forma de probar el sistema sin gastar API.
Con la frontera aquí, el triage es reproducible, auditable y se prueba en
milisegundos con `pytest`.

**`evidencia` es obligatoria.** Un valor sin la cita textual del paciente que lo
sustenta se descarta. Es lo que hace que una alerta se pueda contrastar contra la
grabación en vez de creerle al modelo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Nivel(StrEnum):
    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"
    # No es un nivel clínico: es la confesión de que faltan datos para decidir.
    # Obliga a indagar y prohíbe cerrar la llamada. Ver engine.evaluar().
    INDETERMINADO = "indeterminado"


class Fuente(StrEnum):
    PACIENTE = "paciente"
    TERCERO = "tercero"  # un familiar que interrumpe: se marca, no se descarta


@dataclass
class Variable(Generic[T]):
    """Un dato clínico con su respaldo. Sin respaldo no hay dato."""

    valor: T | None = None
    confianza: float = 0.0
    evidencia: str | None = None
    turno_idx: int | None = None
    fuente: Fuente = Fuente.PACIENTE

    @property
    def conocida(self) -> bool:
        return self.valor is not None

    @property
    def valida(self) -> bool:
        """Un valor sin cita textual del paciente no se puede verificar: no cuenta."""
        return self.valor is not None and bool(self.evidencia)

    def como_dict(self) -> dict[str, Any]:
        return {
            "valor": self.valor,
            "confianza": round(self.confianza, 2),
            "evidencia": self.evidencia,
            "turno_idx": self.turno_idx,
            "fuente": str(self.fuente),
        }


MOVILIDAD = ("normal", "limitada_esperada", "incapacitante_nueva")
HERIDA = ("normal", "eritema_leve", "secrecion_purulenta", "dehiscencia")
APETITO = ("normal", "levemente_disminuido", "muy_disminuido")
SUENO = ("normal", "levemente_alterado", "muy_alterado")

# Las tres que no se pueden dar por desconocidas al cerrar la llamada. Son las que
# discriminan una complicación real, y las tres del score con peso 3.
CRITICAS = ("dolor_nrs", "fiebre_c", "herida")


@dataclass
class EstadoClinico:
    """Las seis variables del protocolo, más lo que no cabe en él."""

    dolor_nrs: Variable[int] = field(default_factory=Variable)
    fiebre_c: Variable[float] = field(default_factory=Variable)
    movilidad: Variable[str] = field(default_factory=Variable)
    herida: Variable[str] = field(default_factory=Variable)
    apetito: Variable[str] = field(default_factory=Variable)
    sueno: Variable[str] = field(default_factory=Variable)

    # «Se sintió caliente» no es lo mismo que «me marcó 38.2». Si no midió, el valor
    # es una sospecha y el protocolo debe pedirle que se ponga el termómetro.
    fiebre_medida: bool = False

    red_flags: list[str] = field(default_factory=list)
    sintomas_libres: list[str] = field(default_factory=list)

    def variable(self, nombre: str) -> Variable:
        return getattr(self, nombre)

    @property
    def variables(self) -> dict[str, Variable]:
        return {n: getattr(self, n) for n in
                ("dolor_nrs", "fiebre_c", "movilidad", "herida", "apetito", "sueno")}

    @property
    def pendientes(self) -> list[str]:
        """Variables sin valor válido, en orden de valor clínico."""
        return [n for n, v in self.variables.items() if not v.valida]

    @property
    def criticas_pendientes(self) -> list[str]:
        return [n for n in CRITICAS if not self.variable(n).valida]

    @property
    def completo(self) -> bool:
        return not self.criticas_pendientes

    def como_dict(self) -> dict[str, Any]:
        return {
            **{n: v.como_dict() for n, v in self.variables.items()},
            "fiebre_medida": self.fiebre_medida,
            "red_flags": self.red_flags,
            "sintomas_libres": self.sintomas_libres,
            "pendientes": self.pendientes,
            "criticas_pendientes": self.criticas_pendientes,
        }


@dataclass
class ReglaAplicada:
    """Una línea del desglose. El jurado tiene que poder reconstruir el score a mano."""

    regla: str
    valor: Any
    puntos: int
    evidencia: str | None = None
    fuente_clinica: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "regla": self.regla,
            "valor": self.valor,
            "puntos": self.puntos,
            "evidencia": self.evidencia,
            "fuente_clinica": self.fuente_clinica,
        }


@dataclass
class Decision:
    """El resultado del motor. Todo lo necesario para defender el nivel asignado."""

    nivel: Nivel
    score: int
    desglose: list[ReglaAplicada] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    motivo: str = ""
    version_reglas: str = ""
    moduladores_activos: bool = True
    score_sin_moduladores: int = 0
    criticas_pendientes: list[str] = field(default_factory=list)

    @property
    def escala(self) -> bool:
        return self.nivel in (Nivel.AMARILLO, Nivel.ROJO)

    def como_dict(self) -> dict[str, Any]:
        return {
            "nivel": str(self.nivel),
            "score": self.score,
            "score_sin_moduladores": self.score_sin_moduladores,
            "desglose": [r.como_dict() for r in self.desglose],
            "red_flags": self.red_flags,
            "motivo": self.motivo,
            "version_reglas": self.version_reglas,
            "moduladores_activos": self.moduladores_activos,
            "criticas_pendientes": self.criticas_pendientes,
        }

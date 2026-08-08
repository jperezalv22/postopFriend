"""Carga del dataset etiquetado. **Solo para evaluación.**

Este módulo vive en `evals/` y no en `app/` a propósito, y ningún archivo de `app/`
lo importa. Es la separación que sostiene el supuesto 2 del README: el agente
conoce la ficha administrativa del paciente y **no** su trayectoria clínica.

Aquí sí se leen `trayectorias_postop_silver.xlsx` (dolor, fiebre, herida reales) y
`dataset_final.xlsx` (`label_ground_truth`), porque evaluar exige comparar contra la
verdad. Si el runtime no puede leer estos archivos, no puede filtrarlos en una
respuesta: la garantía es estructural, no una promesa en un prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (sys.path + UTF-8; tiene que ir primero)

from app.config import get_settings
from app.store.patients import leer_xlsx

CAPAS = ("capa1_limpia", "capa2_ruidosa")


@dataclass
class Trayectoria:
    """El estado clínico real de un paciente en un día postoperatorio.

    Es lo que el agente tiene que *averiguar conversando*. Aquí está para poder
    medir si lo averiguó bien.
    """

    trayectoria_id: str
    paciente_id: str
    dia_postop: int
    arquetipo: str
    dolor_nrs: int
    fiebre_c: float
    movilidad: str
    herida: str
    apetito: str
    sueno: str

    @property
    def caso_id(self) -> str:
        return f"caso_{self.trayectoria_id}"


@dataclass
class Caso:
    """Un caso de evaluación: una trayectoria, su diálogo y su etiqueta."""

    caso_id: str
    paciente_id: str
    dia_postop: int
    capa: str
    etiqueta: str  # verde | amarillo | rojo
    trayectoria: Trayectoria
    turnos: list[dict[str, Any]] = field(default_factory=list)
    estilo_paciente: str = ""

    @property
    def turnos_del_paciente(self) -> list[dict[str, Any]]:
        """Solo lo que dijeron el paciente y el familiar. El agente no se escucha a sí mismo."""
        return [t for t in self.turnos if t["hablante"] in ("paciente", "tercero")]

    def dialogo_texto(self) -> str:
        return "\n".join(f"{t['hablante']}: {t['texto']}" for t in self.turnos)


def cargar_trayectorias() -> dict[str, Trayectoria]:
    s = get_settings()
    filas = leer_xlsx(s.dir_dataset / "trayectorias_postop_silver.xlsx")
    trayectorias = {}
    for f in filas:
        t = Trayectoria(
            trayectoria_id=str(f["trayectoria_id"]),
            paciente_id=str(f["paciente_id"]),
            dia_postop=int(f["dia_postop"]),
            arquetipo=str(f["arquetipo_trayectoria"]),
            dolor_nrs=int(f["dolor_nrs"]),
            fiebre_c=float(f["fiebre_c"]),
            movilidad=str(f["movilidad"]),
            herida=str(f["herida"]),
            apetito=str(f["apetito"]),
            sueno=str(f["sueno"]),
        )
        trayectorias[t.caso_id] = t
    return trayectorias


def cargar_casos(capa: str | None = None) -> list[Caso]:
    """Los 160 casos × 2 capas, con sus turnos agrupados y ordenados."""
    s = get_settings()
    trayectorias = cargar_trayectorias()
    filas = leer_xlsx(s.dir_dataset / "dataset_final.xlsx")

    por_clave: dict[tuple[str, str], Caso] = {}
    for f in filas:
        if capa and f["capa"] != capa:
            continue
        caso_id, capa_fila = str(f["caso_id"]), str(f["capa"])
        clave = (caso_id, capa_fila)

        if clave not in por_clave:
            trayectoria = trayectorias.get(caso_id)
            if trayectoria is None:
                continue  # caso sin trayectoria: no se puede evaluar
            por_clave[clave] = Caso(
                caso_id=caso_id,
                paciente_id=str(f["paciente_id"]),
                dia_postop=int(f["dia_postop"]),
                capa=capa_fila,
                etiqueta=str(f["label_ground_truth"]),
                trayectoria=trayectoria,
                estilo_paciente=str(f.get("estilo_paciente") or ""),
            )

        por_clave[clave].turnos.append(
            {
                "turno_idx": int(f["turno_idx"]),
                "hablante": str(f["hablante"]),
                "texto": str(f["texto"] or "").strip(),
            }
        )

    casos = list(por_clave.values())
    for c in casos:
        c.turnos.sort(key=lambda t: t["turno_idx"])
    casos.sort(key=lambda c: (c.capa, c.caso_id))
    return casos


def resumen() -> dict[str, Any]:
    casos = cargar_casos()
    from collections import Counter

    return {
        "casos": len(casos),
        "por_capa": dict(Counter(c.capa for c in casos)),
        "por_etiqueta": dict(Counter(c.etiqueta for c in casos)),
        "por_estilo": dict(Counter(c.estilo_paciente for c in casos)),
        "turnos": sum(len(c.turnos) for c in casos),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(resumen(), indent=2, ensure_ascii=False))

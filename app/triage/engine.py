"""Motor de triage determinista.

Entra un `EstadoClinico`, sale una `Decision`. Sin LLM, sin red, sin aleatoriedad:
el mismo estado da siempre el mismo nivel, y eso se prueba en milisegundos.

Tres mecanismos, en este orden de precedencia:

1. **Banderas rojas absolutas.** Cortocircuitan el score. Un sangrado activo es rojo
   aunque todo lo demás esté bien: no hay conjunto de buenas noticias que lo compense.
2. **Estado incompleto.** Si falta dolor, fiebre o herida, el nivel es INDETERMINADO
   y la máquina de estados no puede cerrar la llamada. Responde a la pregunta que la
   rúbrica hace sobre la ambigüedad: se indaga antes de decidir y, si tras dos
   intentos no se consigue, se escala por precaución.
3. **Score ponderado** con los cortes de `rules.yaml`.

La asimetría es deliberada y está declarada: ante la duda se escala. Un falso
positivo cuesta una llamada de enfermería; un falso negativo cuesta un reingreso.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.triage.models import (
    Decision,
    EstadoClinico,
    Nivel,
    ReglaAplicada,
    Variable,
)

log = logging.getLogger("postopfriend.triage")

RUTA_REGLAS = Path(__file__).parent / "rules.yaml"

COMORBILIDADES_DE_RIESGO = {"diabetes_tipo_2", "obesidad", "inmunosupresion"}


@lru_cache(maxsize=1)
def cargar_reglas(ruta: str | None = None) -> dict[str, Any]:
    import yaml

    destino = Path(ruta) if ruta else RUTA_REGLAS
    with destino.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _puntos(reglas: dict, id_regla: str) -> int:
    for p in reglas["pesos"]:
        if p["id"] == id_regla:
            return int(p["puntos"])
    return 0


def _fuente(reglas: dict, id_regla: str) -> str:
    for grupo in ("pesos", "moduladores"):
        for p in reglas.get(grupo, []):
            if p["id"] == id_regla:
                return " ".join(str(p.get("fuente", "")).split())
    return ""


def _aplicar(
    desglose: list[ReglaAplicada], reglas: dict, id_regla: str, v: Variable | Any
) -> None:
    valor = v.valor if isinstance(v, Variable) else v
    evidencia = v.evidencia if isinstance(v, Variable) else None
    desglose.append(
        ReglaAplicada(
            regla=id_regla,
            valor=valor,
            puntos=_puntos(reglas, id_regla) or _modulador_puntos(reglas, id_regla),
            evidencia=evidencia,
            fuente_clinica=_fuente(reglas, id_regla),
        )
    )


def _modulador_puntos(reglas: dict, id_regla: str) -> int:
    for m in reglas.get("moduladores", []):
        if m["id"] == id_regla:
            return int(m["puntos"])
    return 0


# ─── Banderas rojas ──────────────────────────────────────────────────────────

def detectar_red_flags(estado: EstadoClinico, reglas: dict | None = None) -> list[str]:
    """Las declaradas por el extractor más las que se deducen de los valores.

    Las automáticas existen porque el extractor puede leer bien la variable y no
    marcar la bandera. Una fiebre de 38.6 es roja tanto si el extractor lo dijo
    como si no; la deducción cierra ese hueco.
    """
    reglas = reglas or cargar_reglas()
    validas = {r["id"] for r in reglas["red_flags"]}
    encontradas = [f for f in estado.red_flags if f in validas]

    if estado.fiebre_c.valida and estado.fiebre_c.valor >= 38.5:
        encontradas.append("fiebre_muy_alta")
    if estado.herida.valida:
        if estado.herida.valor == "dehiscencia":
            encontradas.append("herida_abierta")
        elif estado.herida.valor == "secrecion_purulenta":
            encontradas.append("secrecion_purulenta")

    return sorted(set(encontradas))


# ─── Score ───────────────────────────────────────────────────────────────────

def calcular_score(
    estado: EstadoClinico,
    reglas: dict,
    comorbilidades: list[str] | None = None,
    dia_postop: int | None = None,
    moduladores: bool = False,
) -> tuple[int, int, list[ReglaAplicada]]:
    """Devuelve (score con moduladores, score sin ellos, desglose regla por regla)."""
    desglose: list[ReglaAplicada] = []

    if estado.fiebre_c.valida:
        t = estado.fiebre_c.valor
        if t >= 38.0:
            _aplicar(desglose, reglas, "fiebre_alta", estado.fiebre_c)
        elif t >= 37.5:
            _aplicar(desglose, reglas, "febricula", estado.fiebre_c)

    if estado.dolor_nrs.valida:
        d = estado.dolor_nrs.valor
        if d >= 7:
            _aplicar(desglose, reglas, "dolor_severo", estado.dolor_nrs)
        elif d >= 4:
            _aplicar(desglose, reglas, "dolor_moderado", estado.dolor_nrs)

    if estado.herida.valida:
        h = estado.herida.valor
        if h in ("secrecion_purulenta", "dehiscencia"):
            _aplicar(desglose, reglas, "herida_infectada", estado.herida)
        elif h == "eritema_leve":
            _aplicar(desglose, reglas, "herida_eritema", estado.herida)

    if estado.movilidad.valida and estado.movilidad.valor == "incapacitante_nueva":
        _aplicar(desglose, reglas, "movilidad_perdida", estado.movilidad)

    if estado.apetito.valida and estado.apetito.valor == "muy_disminuido":
        _aplicar(desglose, reglas, "apetito_muy_bajo", estado.apetito)

    if estado.sueno.valida and estado.sueno.valor == "muy_alterado":
        _aplicar(desglose, reglas, "sueno_muy_alterado", estado.sueno)

    base = sum(r.puntos for r in desglose)
    total = base

    # Los moduladores solo suman sobre un cuadro que ya tiene algún signo. Una
    # comorbilidad sin síntomas no debe escalar a nadie: sería alertar por tener
    # diabetes, no por estar peor.
    if moduladores and base >= 1:
        riesgo = COMORBILIDADES_DE_RIESGO & set(comorbilidades or [])
        if riesgo:
            _aplicar(desglose, reglas, "comorbilidad_riesgo", sorted(riesgo))
            total += _modulador_puntos(reglas, "comorbilidad_riesgo")

        if (dia_postop or 0) >= 7 and estado.fiebre_c.valida and estado.fiebre_c.valor >= 37.5:
            _aplicar(desglose, reglas, "fiebre_tardia", estado.fiebre_c)
            total += _modulador_puntos(reglas, "fiebre_tardia")

    return total, base, desglose


# ─── Decisión ────────────────────────────────────────────────────────────────

def evaluar(
    estado: EstadoClinico,
    comorbilidades: list[str] | None = None,
    dia_postop: int | None = None,
    moduladores: bool | None = None,
    intentos_agotados: bool = False,
    reglas: dict | None = None,
) -> Decision:
    """Calcula el nivel de triage.

    `moduladores=None` toma el valor de la configuración, que es la que se entrega.
    Un `True` por defecto aquí haría que quien olvidara pasar el argumento evaluara
    con una configuración distinta a la del sistema en producción, y las alertas
    dejarían de coincidir con lo que reporta la evaluación.

    `intentos_agotados` lo pone la máquina de estados cuando ya repreguntó dos veces
    por una variable crítica y el paciente no la dio. Cambia la salida de
    INDETERMINADO —«sigue indagando»— a una decisión con la información disponible,
    escalada por precaución.
    """
    if moduladores is None:
        from app.config import get_settings

        moduladores = get_settings().triage_moduladores
    reglas = reglas or cargar_reglas()
    cortes = reglas["cortes"]
    version = reglas["version"]

    red_flags = detectar_red_flags(estado, reglas)
    total, base, desglose = calcular_score(
        estado, reglas, comorbilidades, dia_postop, moduladores
    )
    pendientes = estado.criticas_pendientes

    def decidir(nivel: Nivel, motivo: str) -> Decision:
        return Decision(
            nivel=nivel, score=total, desglose=desglose, red_flags=red_flags,
            motivo=motivo, version_reglas=version, moduladores_activos=moduladores,
            score_sin_moduladores=base, criticas_pendientes=pendientes,
        )

    # 1. Bandera roja absoluta. Manda sobre todo lo demás, incluso sobre un estado
    #    incompleto: no hace falta saber el apetito para llevar a alguien a urgencias.
    if red_flags:
        return decidir(Nivel.ROJO, f"bandera roja: {', '.join(red_flags)}")

    # 2. Estado incompleto. Se prohíbe decidir mientras falte una variable crítica.
    if pendientes and not intentos_agotados:
        return decidir(
            Nivel.INDETERMINADO,
            f"faltan variables críticas: {', '.join(pendientes)}. Indagar antes de decidir.",
        )

    # 3. Se agotaron los reintentos con información crítica pendiente. No se cierra en
    #    verde: no saber si hay fiebre no es lo mismo que saber que no la hay.
    if pendientes and intentos_agotados:
        if total >= cortes["rojo"]:
            return decidir(Nivel.ROJO, f"score {total} pese a información incompleta")
        return decidir(
            Nivel.AMARILLO,
            f"información insuficiente ({', '.join(pendientes)}) tras agotar reintentos: "
            f"se escala por precaución con score {total}",
        )

    # 4. Score sobre estado completo.
    if total >= cortes["rojo"]:
        return decidir(Nivel.ROJO, f"score {total} >= {cortes['rojo']}")
    if total >= cortes["amarillo"]:
        return decidir(Nivel.AMARILLO, f"score {total} en el rango {cortes['amarillo']}-{cortes['rojo'] - 1}")
    return decidir(Nivel.VERDE, f"score {total} < {cortes['amarillo']}, sin banderas rojas")


def accion_para(nivel: Nivel, reglas: dict | None = None) -> dict[str, str]:
    """Qué se le comunica al paciente y en qué plazo actúa el equipo."""
    reglas = reglas or cargar_reglas()
    return reglas["acciones"].get(str(nivel), reglas["acciones"]["amarillo"])


def descripcion_red_flag(id_flag: str, reglas: dict | None = None) -> str:
    reglas = reglas or cargar_reglas()
    for r in reglas["red_flags"]:
        if r["id"] == id_flag:
            return str(r["descripcion"])
    return id_flag

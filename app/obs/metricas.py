"""Las métricas de operación, calculadas una sola vez y leídas por todos.

El panel, el acta y la tabla del README salen de aquí. Es deliberado: la rúbrica
comprueba que «las métricas reportadas sean verificables en los logs y concuerden
con lo que ocurre en la sesión», y la forma segura de que concuerden es que no
existan dos implementaciones que puedan divergir.

La fuente es SQLite, no `logs/turns.jsonl`. El JSONL sirve para inspeccionar una
sesión en vivo, pero no se puede cruzar con la llamada ni filtrar por ruta del
LLM, y ambas cosas hacen falta: mientras el plan de pago de Groq esté cerrado la
base tiene turnos medidos por OpenRouter mezclados con los de Groq, y una cifra
del informe que sume las dos rutas no describe el sistema que se entrega.

    resumen()                    todo lo que hay
    resumen(ruta_llm="groq")     solo lo ejecutado por la ruta de producción

La latencia es siempre la del reloj del navegador (fin de habla → primer audio).
Ver app/obs/trace.py para por qué no se mide en el servidor.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.config import get_settings
from app.obs.tokens import PRECIOS_LLM, PRECIOS_STT, costo_llm, costo_stt
from app.store import db

#: Cortes del histograma de latencia, en ms. El primero es el objetivo declarado
#: (1.5 s) y el segundo el límite a partir del cual una conversación deja de
#: sentirse conversación.
CORTES_HISTOGRAMA = (750, 1000, 1500, 2000, 3000)

#: Para la proyección de costo del panel. Es una cifra de referencia, no una
#: promesa: se declara el supuesto junto al número.
LLAMADAS_PROYECTADAS = 1000


def percentil(valores: list[float], p: float) -> float | None:
    """Percentil por interpolación lineal. `None` si no hay datos.

    Sin numpy a propósito: es una dependencia de 20 MB para catorce líneas, y
    `report_metrics.py` tiene que poder correr en el portátil del jurado.
    """
    limpios = sorted(v for v in valores if v is not None)
    if not limpios:
        return None
    if len(limpios) == 1:
        return round(limpios[0], 1)
    posicion = (len(limpios) - 1) * p
    bajo = int(posicion)
    alto = min(bajo + 1, len(limpios) - 1)
    peso = posicion - bajo
    return round(limpios[bajo] * (1 - peso) + limpios[alto] * peso, 1)


def clausula_ruta(ruta_llm: str | None) -> tuple[str, tuple[Any, ...]]:
    """Cláusula que restringe a los turnos de una ruta del LLM.

    Las llamadas anteriores a la columna `ruta_llm` tienen NULL. Se cuentan como
    Groq: son de cuando OpenRouter todavía no existía en el proyecto, así que
    excluirlas silenciosamente borraría mediciones legítimas.
    """
    if not ruta_llm:
        return "", ()
    if ruta_llm == "groq":
        return " AND (l.ruta_llm = ? OR l.ruta_llm IS NULL)", (ruta_llm,)
    return " AND l.ruta_llm = ?", (ruta_llm,)


def _turnos(con: sqlite3.Connection, ruta_llm: str | None) -> list[sqlite3.Row]:
    clausula, parametros = clausula_ruta(ruta_llm)
    return con.execute(
        f"""SELECT t.*, l.ruta_llm, l.modelo_llm
            FROM turnos t JOIN llamadas l ON l.call_id = t.call_id
            WHERE t.hablante = 'agente'{clausula}""",
        parametros,
    ).fetchall()


def _llamadas(con: sqlite3.Connection, ruta_llm: str | None) -> list[sqlite3.Row]:
    clausula, parametros = clausula_ruta(ruta_llm)
    return con.execute(
        f"SELECT l.* FROM llamadas l WHERE 1=1{clausula} ORDER BY l.inicio_ts DESC",
        parametros,
    ).fetchall()


def histograma(latencias: list[float]) -> list[dict[str, Any]]:
    """Reparto por tramos. Lo que el panel dibuja y el informe cita."""
    tramos: list[dict[str, Any]] = []
    anterior = 0
    for corte in CORTES_HISTOGRAMA:
        n = sum(1 for v in latencias if anterior <= v < corte)
        tramos.append({"desde": anterior, "hasta": corte, "n": n})
        anterior = corte
    tramos.append({"desde": anterior, "hasta": None,
                   "n": sum(1 for v in latencias if v >= anterior)})
    total = len(latencias) or 1
    for t in tramos:
        t["pct"] = round(t["n"] / total, 4)
    return tramos


def _etapas(filas: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    """P50 por etapa (STT, LLM, RAG, TTS). Es el desglose que explica el total."""
    acumulado: dict[str, list[float]] = {}
    for fila in filas:
        crudo = fila["etapas_json"]
        if not crudo:
            continue
        try:
            for nombre, ms in (json.loads(crudo) or {}).items():
                if isinstance(ms, (int, float)):
                    acumulado.setdefault(nombre, []).append(float(ms))
        except json.JSONDecodeError:
            continue
    return {
        nombre: {"n": len(v), "p50": percentil(v, 0.50), "p95": percentil(v, 0.95)}
        for nombre, v in sorted(acumulado.items())
    }


def _incidencias(filas: list[sqlite3.Row]) -> dict[str, int]:
    """Cuenta por tipo, no por texto: `audio_degradado:silencio` cuenta como
    `audio_degradado`. Lo que interesa es la clase de fallo, no cada variante."""
    cuenta: dict[str, int] = {}
    for fila in filas:
        crudo = fila["incidencias_json"]
        if not crudo:
            continue
        try:
            for inc in json.loads(crudo) or []:
                clase = str(inc).split(":", 1)[0]
                cuenta[clase] = cuenta.get(clase, 0) + 1
        except json.JSONDecodeError:
            continue
    return dict(sorted(cuenta.items(), key=lambda kv: -kv[1]))


def resumen(ruta_llm: str | None = None, call_id: str | None = None) -> dict[str, Any]:
    """Todo lo medible del sistema, en un diccionario.

    `ruta_llm` restringe a una ruta; `call_id` a una llamada (lo que usa el acta).
    """
    s = get_settings()
    con = db.conexion()

    if call_id:
        filas = con.execute(
            "SELECT * FROM turnos WHERE call_id = ? AND hablante = 'agente' "
            "ORDER BY turno_idx",
            (call_id,),
        ).fetchall()
        llamadas = con.execute(
            "SELECT * FROM llamadas WHERE call_id = ?", (call_id,)
        ).fetchall()
    else:
        filas = _turnos(con, ruta_llm)
        llamadas = _llamadas(con, ruta_llm)

    latencias = [float(f["latencia_ms"]) for f in filas if f["latencia_ms"] is not None]
    tokens_in = sum(int(f["tokens_in"] or 0) for f in filas)
    tokens_out = sum(int(f["tokens_out"] or 0) for f in filas)
    llm_calls = sum(int(f["llm_calls"] or 0) for f in filas)
    rag_consultas = sum(int(f["rag_consultas"] or 0) for f in filas)
    audio_s = sum(float(f["audio_paciente_s"] or 0.0) for f in filas)

    # El modelo con el que se midió, tal cual quedó en la fila de la llamada. Si
    # hay más de uno la cifra de costo mezcla tarifas y hay que decirlo.
    modelos = sorted({str(l["modelo_llm"] or "") for l in llamadas if l["modelo_llm"]})
    modelo_costo = modelos[0] if len(modelos) == 1 else s.llm_model

    coste_llm = costo_llm(modelo_costo, tokens_in, tokens_out)
    coste_stt = costo_stt(s.stt_model, audio_s)
    n_llamadas = len(llamadas) or 1

    niveles: dict[str, int] = {}
    estados: dict[str, int] = {}
    for l in llamadas:
        niveles[str(l["nivel_triage"] or "sin_evaluar")] = (
            niveles.get(str(l["nivel_triage"] or "sin_evaluar"), 0) + 1)
        estados[str(l["estado"] or "?")] = estados.get(str(l["estado"] or "?"), 0) + 1

    alertas = con.execute(
        "SELECT nivel, estado, COUNT(*) AS n FROM alertas GROUP BY nivel, estado"
    ).fetchall()

    return {
        "filtro": {"ruta_llm": ruta_llm, "call_id": call_id},
        "modelos_medidos": modelos,
        "tarifa_aplicada": modelo_costo,
        "latencia": {
            "n": len(latencias),
            "turnos_sin_medir": len(filas) - len(latencias),
            "p50": percentil(latencias, 0.50),
            "p90": percentil(latencias, 0.90),
            "p95": percentil(latencias, 0.95),
            "min": round(min(latencias), 1) if latencias else None,
            "max": round(max(latencias), 1) if latencias else None,
            "bajo_objetivo": round(
                sum(1 for v in latencias if v <= 1500) / len(latencias), 4
            ) if latencias else None,
            "histograma": histograma(latencias),
        },
        "etapas_ms": _etapas(filas),
        "consumo": {
            "turnos_del_agente": len(filas),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_totales": tokens_in + tokens_out,
            "llm_calls": llm_calls,
            "llm_calls_por_turno": round(llm_calls / len(filas), 2) if filas else None,
            "rag_consultas": rag_consultas,
            "audio_paciente_s": round(audio_s, 1),
        },
        "costo_usd": {
            "llm": round(coste_llm, 6),
            "stt": round(coste_stt, 6),
            "tts": 0.0,          # edge-tts no cobra
            "total": round(coste_llm + coste_stt, 6),
            "por_turno": round((coste_llm + coste_stt) / len(filas), 6) if filas else None,
            "por_llamada": round((coste_llm + coste_stt) / n_llamadas, 6),
            "proyeccion_1000_llamadas": round(
                (coste_llm + coste_stt) / n_llamadas * LLAMADAS_PROYECTADAS, 2
            ),
            "supuesto": (
                f"{LLAMADAS_PROYECTADAS} llamadas del mismo perfil que las "
                f"{len(llamadas)} medidas · tarifas de {modelo_costo} y {s.stt_model}"
            ),
        },
        "llamadas": {
            "n": len(llamadas),
            "por_estado": estados,
            "por_nivel": niveles,
            "turnos_por_llamada": round(
                sum(int(l["turnos"] or 0) for l in llamadas) / n_llamadas, 1
            ),
        },
        "alertas": {f"{a['nivel']}_{a['estado']}": int(a["n"]) for a in alertas},
        "incidencias": _incidencias(filas),
        "precios": {
            "llm": {m: [p.entrada_por_millon, p.salida_por_millon]
                    for m, p in PRECIOS_LLM.items()},
            "stt_usd_por_hora": PRECIOS_STT,
        },
    }

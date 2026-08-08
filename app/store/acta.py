"""Acta de llamada: lo que queda cuando el agente cuelga.

La rúbrica pide un «resumen final completo» y enumera qué tiene que contener. Las
diez secciones de `docs/plan-maestro.md` §7.6 están aquí en ese orden, y el orden
importa: quien la lea encuentra primero de quién es y qué se decidió, y solo
después el detalle que lo sustenta.

Dos reglas que dan forma a todo el módulo:

**Se genera siempre**, incluso si la llamada se corta a la mitad. Un acta que solo
existe cuando todo salió bien no sirve para auditar nada; la que se emite tras una
llamada interrumpida es justamente la que hay que poder revisar. Por eso `estado`
es un campo del acta y no una condición para emitirla.

**Se reconstruye desde SQLite.** El acta viva se arma con el contexto en memoria y
se persiste en `llamadas.acta_json`, pero `cargar()` sabe rehacer una parcial a
partir de las tablas para cualquier llamada anterior a esta función. Un acta que
solo existiera en la memoria del proceso desaparecería al reiniciar el servidor.

Nada de lo que hay aquí vuelve a pasar por el LLM: el acta transcribe y agrega lo
que ya está registrado. Un resumen generado sería una tercera versión de los
hechos, distinta de los logs y de la alerta, y sin forma de decidir cuál vale.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.obs import metricas
from app.obs.logger import ahora_iso
from app.store import db
from app.store.patients import Ficha
from app.triage.engine import accion_para, descripcion_red_flag
from app.triage.models import Decision, EstadoClinico, Nivel

log = logging.getLogger("postopfriend.acta")

VERSION_ACTA = "1.0"

ETIQUETA_VARIABLE = {
    "dolor_nrs": "Dolor (0-10)",
    "fiebre_c": "Temperatura (°C)",
    "movilidad": "Movilidad",
    "herida": "Herida quirúrgica",
    "apetito": "Apetito",
    "sueno": "Sueño",
}

ETIQUETA_ESTADO = {
    "en_curso": "interrumpida (la llamada no llegó a cerrarse)",
    "completa": "completa",
    "incompleta": "incompleta",
    "no_disponible": "paciente no disponible",
}


# ─── Construcción ────────────────────────────────────────────────────────────

def _transcripcion(call_id: str) -> list[dict[str, Any]]:
    """Sección 3: el diálogo con marca de tiempo y latencia de cada turno del agente."""
    filas = db.conexion().execute(
        """SELECT turno_idx, hablante, texto, ts, latencia_ms, estado_flujo,
                  nivel_triage, tokens_in, tokens_out, incidencias_json
           FROM turnos WHERE call_id = ? ORDER BY turno_idx""",
        (call_id,),
    ).fetchall()
    turnos = []
    for f in filas:
        turno = {
            "turno_idx": f["turno_idx"],
            "hablante": f["hablante"],
            "texto": f["texto"],
            "ts": f["ts"],
            "latencia_ms": f["latencia_ms"],
            "estado_flujo": f["estado_flujo"],
            "nivel_triage": f["nivel_triage"],
        }
        if f["incidencias_json"]:
            try:
                turno["incidencias"] = json.loads(f["incidencias_json"])
            except json.JSONDecodeError:
                pass
        turnos.append(turno)
    return turnos


def _identificacion(ficha: Ficha | None, fila: Any) -> dict[str, Any]:
    if ficha is not None:
        p = ficha.paciente
        return {
            "paciente_id": p.paciente_id,
            "nombre": p.nombre_completo,
            "documento_cc": p.documento_cc,
            "edad": p.edad,
            "eps": p.eps,
            "ciudad": p.ciudad,
            "procedimiento": p.procedimiento,
            "fecha_cirugia": p.fecha_cirugia.isoformat() if p.fecha_cirugia else None,
            "dia_postop": ficha.dia_postop,
            "comorbilidades": p.comorbilidades,
        }
    # Llamada histórica: la fila de `llamadas` guarda lo mínimo identificable.
    return {
        "paciente_id": fila["paciente_id"] if fila else None,
        "procedimiento": fila["procedimiento"] if fila else None,
        "dia_postop": fila["dia_postop"] if fila else None,
        "nota": "ficha completa no disponible: acta reconstruida desde la base",
    }


def _duracion_s(fila: Any) -> float | None:
    if fila is None or not fila["inicio_ts"]:
        return None
    from datetime import datetime

    fin = fila["fin_ts"] or ahora_iso()
    try:
        return round(
            (datetime.fromisoformat(fin) - datetime.fromisoformat(fila["inicio_ts"]))
            .total_seconds(), 1
        )
    except ValueError:
        return None


def construir(
    call_id: str,
    ficha: Ficha | None = None,
    estado_clinico: EstadoClinico | None = None,
    decision: Decision | None = None,
    referencias: list[dict[str, Any]] | None = None,
    incidencias: list[str] | None = None,
    alerta_id: str = "",
    estado: str = "",
) -> dict[str, Any]:
    """Las diez secciones de §7.6, en orden, desde la base y el contexto vivo."""
    fila = db.conexion().execute(
        "SELECT * FROM llamadas WHERE call_id = ?", (call_id,)
    ).fetchone()

    turnos = _transcripcion(call_id)
    metricas_llamada = metricas.resumen(call_id=call_id)
    nivel = str(decision.nivel) if decision else (fila["nivel_triage"] if fila else None)
    accion = accion_para(Nivel(nivel)) if nivel in tuple(Nivel) else {}

    # Sección 9: las incidencias del contexto más las que anotó cada turno. Se
    # unen aquí y no en el turno porque el acta es el único sitio donde se ven
    # juntas las de la conversación y las de la infraestructura.
    todas_incidencias = list(incidencias or [])
    for t in turnos:
        for inc in t.get("incidencias", []):
            if inc not in todas_incidencias:
                todas_incidencias.append(inc)

    # Sección 8: lo que de verdad se le dijo, no lo que el protocolo dice que se
    # dice. Se toma el último turno del agente, textual.
    ultimo_del_agente = next(
        (t["texto"] for t in reversed(turnos) if t["hablante"] == "agente"), ""
    )

    return {
        "version_acta": VERSION_ACTA,
        "generada_ts": ahora_iso(),
        "call_id": call_id,

        # 1. Identificación
        "identificacion": _identificacion(ficha, fila),

        # 2. Metadatos de la llamada
        "llamada": {
            "inicio_ts": fila["inicio_ts"] if fila else None,
            "fin_ts": (fila["fin_ts"] if fila else None) or ahora_iso(),
            "duracion_s": _duracion_s(fila),
            "turnos": len(turnos),
            "estado": estado or (fila["estado"] if fila else "en_curso"),
            "modelo_llm": fila["modelo_llm"] if fila else None,
            "ruta_llm": (fila["ruta_llm"] if fila else None) or "groq",
        },

        # 3. Transcripción con timestamps y latencia
        "transcripcion": turnos,

        # 4. Estado clínico final
        "estado_clinico": (
            {n: v.como_dict() for n, v in estado_clinico.variables.items()}
            if estado_clinico else {}
        ),
        "fiebre_medida": bool(estado_clinico.fiebre_medida) if estado_clinico else None,
        "variables_pendientes": estado_clinico.pendientes if estado_clinico else [],

        # 5. Síntomas libres fuera del protocolo
        "sintomas_libres": estado_clinico.sintomas_libres if estado_clinico else [],

        # 6. Decisión
        "decision": decision.como_dict() if decision else {
            "nivel": nivel,
            "score": fila["score_total"] if fila else None,
            "nota": "desglose no disponible: acta reconstruida desde la base",
        },
        "red_flags_descritas": (
            [descripcion_red_flag(f) for f in decision.red_flags] if decision else []
        ),

        # 7. Referencias del corpus usadas
        "referencias": referencias or [],

        # 8. Próximos pasos comunicados al paciente
        "proximos_pasos": {
            "plazo": accion.get("plazo", ""),
            "protocolo": accion.get("mensaje", ""),
            "textual_al_paciente": ultimo_del_agente,
            "alerta_id": alerta_id or None,
        },

        # 9. Incidencias
        "incidencias": todas_incidencias,

        # 10. Métricas de la llamada
        "metricas": {
            "latencia_ms": metricas_llamada["latencia"],
            "etapas_ms": metricas_llamada["etapas_ms"],
            "consumo": metricas_llamada["consumo"],
            "costo_usd": metricas_llamada["costo_usd"],
        },
    }


# ─── Persistencia ────────────────────────────────────────────────────────────

def guardar(call_id: str, acta: dict[str, Any]) -> None:
    """Escribe el acta en la llamada. Nunca propaga: no puede tumbar el cierre."""
    try:
        with db.transaccion() as con:
            con.execute(
                "UPDATE llamadas SET acta_json = ? WHERE call_id = ?",
                (json.dumps(acta, ensure_ascii=False), call_id),
            )
    except Exception as e:
        log.error("no se pudo guardar el acta de %s: %s", call_id, e)


def cargar(call_id: str) -> dict[str, Any] | None:
    """El acta persistida; si no hay, una parcial reconstruida desde las tablas.

    Devolver una parcial en vez de `None` es deliberado: para una llamada anterior
    a esta función, la transcripción y las métricas siguen estando en la base y son
    exactamente lo que alguien querría ver. El acta lo declara en las secciones que
    no puede rellenar en vez de fingir que están vacías.
    """
    fila = db.conexion().execute(
        "SELECT acta_json FROM llamadas WHERE call_id = ?", (call_id,)
    ).fetchone()
    if fila is None:
        return None
    if fila["acta_json"]:
        try:
            return json.loads(fila["acta_json"])
        except json.JSONDecodeError:
            log.warning("el acta guardada de %s está corrupta; se reconstruye", call_id)
    return construir(call_id)


# ─── Exportación legible ─────────────────────────────────────────────────────

def _tabla(cabeceras: list[str], filas: list[list[str]], alineacion: str = "") -> list[str]:
    separador = alineacion or "|".join("---" for _ in cabeceras)
    return ["| " + " | ".join(cabeceras) + " |", "|" + separador + "|"] + [
        "| " + " | ".join(f) + " |" for f in filas
    ]


def como_markdown(acta: dict[str, Any]) -> str:
    """El mismo contenido para quien lo lea sin un visor de JSON.

    Es el formato que se abre en el video: un JSON en pantalla no demuestra que el
    acta sea legible por la enfermera que la va a usar.
    """
    ident = acta.get("identificacion", {})
    llamada = acta.get("llamada", {})
    decision = acta.get("decision", {})
    pasos = acta.get("proximos_pasos", {})
    m = acta.get("metricas", {})
    nivel = str(decision.get("nivel") or "sin evaluar")

    L: list[str] = [
        f"# Acta de llamada — {ident.get('nombre') or ident.get('paciente_id') or '?'}",
        "",
        f"`{acta.get('call_id')}` · generada {acta.get('generada_ts')} · "
        f"acta v{acta.get('version_acta')}",
        "",
        "> Demostración con datos sintéticos. No constituye asesoría médica.",
        "",
        "## 1. Identificación",
        "",
    ]

    L += [
        f"- **{ident.get('nombre', '—')}** · CC {ident.get('documento_cc', '—')} · "
        f"{ident.get('edad', '—')} años",
        f"- {ident.get('eps', '—')} · {ident.get('ciudad', '—')}",
        f"- **{ident.get('procedimiento', '—')}** · cirugía "
        f"{ident.get('fecha_cirugia') or '—'} · **día {ident.get('dia_postop', '—')} "
        f"postoperatorio**",
        f"- Comorbilidades: {', '.join(ident.get('comorbilidades') or []) or 'ninguna registrada'}",
    ]
    if ident.get("nota"):
        L.append(f"- _{ident['nota']}_")

    L += [
        "",
        "## 2. La llamada",
        "",
        f"- Inicio: {llamada.get('inicio_ts', '—')}",
        f"- Fin: {llamada.get('fin_ts', '—')}",
        f"- Duración: {llamada.get('duracion_s', '—')} s · {llamada.get('turnos', 0)} turnos",
        f"- Estado: **{ETIQUETA_ESTADO.get(str(llamada.get('estado')), llamada.get('estado'))}**",
        f"- Modelo: `{llamada.get('modelo_llm', '—')}` por la ruta `{llamada.get('ruta_llm', '—')}`",
        "",
        "## 3. Transcripción",
        "",
    ]

    for t in acta.get("transcripcion", []):
        quien = "**Agente**" if t["hablante"] == "agente" else "Paciente"
        marca = str(t.get("ts") or "")[11:19]
        latencia = f" · {t['latencia_ms']:.0f} ms" if t.get("latencia_ms") else ""
        flujo = f" · _{t['estado_flujo']}_" if t.get("estado_flujo") else ""
        L.append(f"**[{t['turno_idx']}] {marca}** {quien}{latencia}{flujo}")
        L += [f"> {t['texto']}", ""]
    if not acta.get("transcripcion"):
        L += ["_Sin turnos registrados._", ""]

    L += ["## 4. Estado clínico final", ""]
    filas = [
        [ETIQUETA_VARIABLE.get(n, n),
         "—" if v.get("valor") is None else str(v["valor"]),
         f"{v.get('confianza', 0):.2f}",
         f"«{v['evidencia']}»" if v.get("evidencia") else "_sin evidencia textual_"]
        for n, v in acta.get("estado_clinico", {}).items()
    ]
    L += _tabla(["Variable", "Valor", "Confianza", "Lo que dijo el paciente"], filas) if filas \
        else ["_No se recogió estado clínico._"]
    if acta.get("variables_pendientes"):
        L += ["", f"Quedaron sin averiguar: {', '.join(acta['variables_pendientes'])}."]
    if acta.get("fiebre_medida") is False:
        L += ["", "La temperatura **no fue medida con termómetro**: el valor es referido."]

    L += ["", "## 5. Otros síntomas referidos", ""]
    L += [f"- {s}" for s in acta.get("sintomas_libres", [])] or \
         ["_Ninguno fuera del protocolo._"]

    L += ["", f"## 6. Decisión: {nivel.upper()}", "",
          f"Score **{decision.get('score', '—')}**"
          + (f" · {decision.get('motivo')}" if decision.get("motivo") else ""), ""]
    desglose = decision.get("desglose") or []
    if desglose:
        L += _tabla(
            ["Regla", "Valor", "Puntos", "Evidencia"],
            [[str(r.get("regla", "")), str(r.get("valor", "")), str(r.get("puntos", 0)),
              f"«{r['evidencia']}»" if r.get("evidencia") else "—"] for r in desglose],
            alineacion="---|---|---:|---",
        )
    else:
        L.append("_Sin reglas disparadas._")
    if acta.get("red_flags_descritas"):
        L += ["", "**Banderas rojas:**", ""] + \
             [f"- {f}" for f in acta["red_flags_descritas"]]
    if decision.get("nota"):
        L += ["", f"_{decision['nota']}_"]

    L += ["", "## 7. Fuentes citadas durante la llamada", ""]
    referencias = acta.get("referencias", [])
    L += [
        f"- {r.get('titulo', '—')} — p. {r.get('pagina', '?')} "
        f"(`{r.get('url', '')}`)" for r in referencias
    ] or ["_No se citó el corpus: no hubo preguntas clínicas._"]

    L += ["", "## 8. Lo que se le comunicó al paciente", "",
          f"**Plazo: {pasos.get('plazo') or '—'}**", "",
          f"Protocolo: {pasos.get('protocolo') or '—'}", ""]
    if pasos.get("textual_al_paciente"):
        L += ["Textualmente, en el último turno:", "",
              f"> {pasos['textual_al_paciente']}", ""]
    if pasos.get("alerta_id"):
        L += [f"Alerta generada: **{pasos['alerta_id']}** "
              f"(`data/alertas/{pasos['alerta_id']}.md`)", ""]

    L += ["## 9. Incidencias", ""]
    L += [f"- `{i}`" for i in acta.get("incidencias", [])] or ["_Ninguna._"]

    latencia = m.get("latencia", {})
    consumo = m.get("consumo", {})
    costo = m.get("costo_usd", {})
    L += ["", "## 10. Métricas de la llamada", ""]
    L += _tabla(
        ["Métrica", "Valor"],
        [
            ["Latencia P50 (fin de habla → primer audio)",
             f"{latencia.get('p50')} ms" if latencia.get("p50") else "sin medir"],
            ["Latencia P95",
             f"{latencia.get('p95')} ms" if latencia.get("p95") else "sin medir"],
            ["Turnos medidos", str(latencia.get("n", 0))],
            ["Tokens entrada / salida",
             f"{consumo.get('tokens_in', 0)} / {consumo.get('tokens_out', 0)}"],
            ["Invocaciones al LLM", str(consumo.get("llm_calls", 0))],
            ["Consultas al corpus", str(consumo.get("rag_consultas", 0))],
            ["Audio del paciente", f"{consumo.get('audio_paciente_s', 0)} s"],
            ["Costo de la llamada", f"US$ {costo.get('total', 0):.6f}"],
        ],
    )
    etapas = m.get("etapas_ms", {})
    if etapas:
        L += ["", "Desglose por etapa (P50, ms):", ""]
        L += _tabla(
            ["Etapa", "P50", "P95", "n"],
            [[n, str(v.get("p50")), str(v.get("p95")), str(v.get("n"))]
             for n, v in etapas.items()],
            alineacion="---|---:|---:|---:",
        )

    L += ["", "---", "",
          "Generada por postopFriend. El desglose del score se puede recomponer a mano "
          "con `app/triage/rules.yaml`.", ""]
    return "\n".join(L)

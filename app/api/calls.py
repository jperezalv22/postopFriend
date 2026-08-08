"""Lectura de lo que quedó: actas, historial, alertas y métricas.

Todo lo que sirve aquí sale de SQLite. No hay estado en memoria ni cachés: si el
servidor se reinicia en mitad de la evaluación, el panel muestra exactamente lo
mismo. Es la propiedad que hace que las métricas del informe sean verificables —
el jurado puede abrir `data/postop.db` y llegar a las mismas cifras.

    GET  /api/llamadas                      historial
    GET  /api/llamadas/{id}/acta            acta en JSON
    GET  /api/llamadas/{id}/acta.md         la misma acta, legible y descargable
    GET  /api/metricas?ruta=groq            lo que dibuja el panel
    GET  /api/alertas                       cola de escalamiento
    POST /api/alertas/{id}/atendida         cerrar una alerta
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.obs import metricas
from app.store import acta as acta_mod
from app.store import db
from app.triage import escalation

router = APIRouter(prefix="/api", tags=["observabilidad"])


@router.get("/llamadas")
def historial(limite: int = Query(50, ge=1, le=500), ruta: str | None = None):
    """Las llamadas más recientes. `ruta` filtra por backend del LLM.

    El filtro no es un lujo: mientras el desarrollo vaya por OpenRouter y la
    grabación por Groq, la base tiene las dos y mezclarlas produce cifras que no
    describen ninguna de las dos configuraciones.
    """
    clausula, parametros = metricas.clausula_ruta(ruta)
    filas = db.conexion().execute(
        f"""SELECT call_id, paciente_id, dia_postop, procedimiento, inicio_ts, fin_ts,
                   duracion_s, estado, nivel_triage, score_total, turnos,
                   modelo_llm, ruta_llm,
                   (acta_json IS NOT NULL) AS tiene_acta
            FROM llamadas l WHERE 1=1{clausula}
            ORDER BY inicio_ts DESC LIMIT ?""",
        (*parametros, limite),
    ).fetchall()
    return {"llamadas": db.como_dicts(filas)}


@router.get("/llamadas/{call_id}/acta")
def acta_json(call_id: str):
    documento = acta_mod.cargar(call_id)
    if documento is None:
        raise HTTPException(404, f"no existe la llamada {call_id}")
    return documento


@router.get("/llamadas/{call_id}/acta.md", response_class=PlainTextResponse)
def acta_markdown(call_id: str):
    documento = acta_mod.cargar(call_id)
    if documento is None:
        raise HTTPException(404, f"no existe la llamada {call_id}")
    return PlainTextResponse(
        acta_mod.como_markdown(documento),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="acta_{call_id}.md"'},
    )


@router.get("/metricas")
def metricas_operacion(ruta: str | None = None, call_id: str | None = None):
    """La misma función que alimenta el README. Ver app/obs/metricas.py."""
    return metricas.resumen(ruta_llm=ruta, call_id=call_id)


@router.get("/alertas")
def alertas(limite: int = Query(50, ge=1, le=200), detalle: bool = False):
    """La cola de escalamiento. Con `detalle=true` incluye el objeto completo."""
    lista = escalation.alertas_activas(limite)
    if detalle:
        for a in lista:
            fila = db.conexion().execute(
                "SELECT payload_json FROM alertas WHERE alerta_id = ?", (a["alerta_id"],)
            ).fetchone()
            if fila and fila["payload_json"]:
                try:
                    a["payload"] = json.loads(fila["payload_json"])
                except json.JSONDecodeError:
                    pass
    return {"alertas": lista}


@router.post("/alertas/{alerta_id}/atendida")
def atender(alerta_id: str):
    if not escalation.marcar_atendida(alerta_id):
        raise HTTPException(404, f"no existe la alerta {alerta_id}")
    return {"alerta_id": alerta_id, "estado": "atendida"}

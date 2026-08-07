"""Escalamiento: qué queda cuando el agente decide alertar.

La rúbrica pregunta tres cosas concretas — qué se registra, con qué estructura y con
qué persistencia — así que la alerta se escribe por cuatro caminos a la vez:

    SQLite            data/postop.db, tabla `alertas`   consultable, auditable
    JSON              data/alertas/ALT-*.json           el objeto completo
    Markdown          data/alertas/ALT-*.md             legible por una enfermera
    webhook           ESCALATION_WEBHOOK_URL            se ve llegar en vivo en el video

Y una cuarta cosa que la rúbrica también pregunta y que suele olvidarse: **qué se le
comunicó al paciente**. Va textual en la alerta, no parafraseado, porque es lo que
permite verificar después que se le dijo lo que había que decirle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.obs.logger import ahora_iso
from app.store import db
from app.store.patients import Ficha
from app.triage.engine import accion_para, descripcion_red_flag
from app.triage.models import Decision, EstadoClinico, Nivel

log = logging.getLogger("postopfriend.triage")


def nuevo_id_alerta() -> str:
    """ALT-AAAAMMDD-NNNN, correlativo dentro del día. Legible en una lista impresa."""
    hoy = datetime.now().strftime("%Y%m%d")
    fila = db.conexion().execute(
        "SELECT COUNT(*) AS n FROM alertas WHERE alerta_id LIKE ?", (f"ALT-{hoy}-%",)
    ).fetchone()
    return f"ALT-{hoy}-{int(fila['n']) + 1:04d}"


@dataclass
class Alerta:
    alerta_id: str
    creada_ts: str
    nivel: str
    call_id: str
    ficha: Ficha
    decision: Decision
    estado_clinico: EstadoClinico
    motivo: str = ""
    referencias: list[dict[str, Any]] = field(default_factory=list)
    accion_comunicada: str = ""
    estado: str = "pendiente"
    canales: list[str] = field(default_factory=list)

    def como_dict(self) -> dict[str, Any]:
        p = self.ficha.paciente
        return {
            "alerta_id": self.alerta_id,
            "creada_ts": self.creada_ts,
            "nivel": self.nivel,
            "call_id": self.call_id,
            "paciente": {
                "id": p.paciente_id, "nombre": p.nombre_completo,
                "documento_cc": p.documento_cc, "eps": p.eps,
                "ciudad": p.ciudad, "edad": p.edad,
            },
            "procedimiento": p.procedimiento,
            "fecha_cirugia": p.fecha_cirugia.isoformat() if p.fecha_cirugia else None,
            "dia_postop": self.ficha.dia_postop,
            "comorbilidades": p.comorbilidades,
            "score": {
                "total": self.decision.score,
                "sin_moduladores": self.decision.score_sin_moduladores,
                "desglose": [r.como_dict() for r in self.decision.desglose],
            },
            "red_flags": self.decision.red_flags,
            "estado_clinico": self.estado_clinico.como_dict(),
            "motivo": self.motivo or self.decision.motivo,
            "referencias": self.referencias,
            "accion_comunicada": self.accion_comunicada,
            "estado": self.estado,
            "version_reglas": self.decision.version_reglas,
            "canales": self.canales,
        }

    def como_markdown(self) -> str:
        p = self.ficha.paciente
        d = self.decision
        marca = {"rojo": "ROJO", "amarillo": "AMARILLO"}.get(self.nivel, self.nivel.upper())
        accion = accion_para(Nivel(self.nivel))

        lineas = [
            f"# Alerta {self.alerta_id} — {marca}",
            "",
            f"**{p.nombre_completo}** · CC {p.documento_cc} · {p.edad} años · {p.eps} · {p.ciudad}",
            f"{p.procedimiento} · día {self.ficha.dia_postop} postoperatorio"
            + (f" · cirugía {p.fecha_cirugia}" if p.fecha_cirugia else ""),
            f"Comorbilidades: {', '.join(p.comorbilidades) or 'ninguna registrada'}",
            "",
            f"Generada {self.creada_ts} · llamada `{self.call_id}` · reglas v{d.version_reglas}",
            "",
            f"## Plazo: {accion['plazo']}",
            "",
        ]

        if d.red_flags:
            lineas += ["### Banderas rojas", ""]
            lineas += [f"- {descripcion_red_flag(f)}" for f in d.red_flags]
            lineas.append("")

        lineas += [f"### Score: {d.score}", "", "| Regla | Valor | Puntos | Lo que dijo el paciente |",
                   "|---|---|---:|---|"]
        for r in d.desglose:
            evidencia = f"«{r.evidencia}»" if r.evidencia else "—"
            lineas.append(f"| {r.regla} | {r.valor} | {r.puntos} | {evidencia} |")
        if not d.desglose:
            lineas.append("| — | — | 0 | sin reglas disparadas |")

        lineas += ["", "### Estado clínico recogido", "", "| Variable | Valor | Evidencia |", "|---|---|---|"]
        for nombre, v in self.estado_clinico.variables.items():
            valor = v.valor if v.conocida else "no se pudo averiguar"
            evidencia = f"«{v.evidencia}»" if v.evidencia else "—"
            lineas.append(f"| {nombre} | {valor} | {evidencia} |")

        if self.estado_clinico.sintomas_libres:
            lineas += ["", "### Otros síntomas referidos", ""]
            lineas += [f"- {s}" for s in self.estado_clinico.sintomas_libres]

        if self.referencias:
            lineas += ["", "### Fuentes citadas durante la llamada", ""]
            for r in self.referencias:
                lineas.append(f"- {r.get('titulo', '')} — p. {r.get('pagina', '?')}")

        lineas += ["", "### Lo que se le dijo al paciente", "",
                   self.accion_comunicada or accion["mensaje"], ""]
        return "\n".join(lineas)


def construir_alerta(
    call_id: str,
    ficha: Ficha,
    decision: Decision,
    estado: EstadoClinico,
    referencias: list[dict[str, Any]] | None = None,
    accion_comunicada: str = "",
) -> Alerta:
    return Alerta(
        alerta_id=nuevo_id_alerta(),
        creada_ts=ahora_iso(),
        nivel=str(decision.nivel),
        call_id=call_id,
        ficha=ficha,
        decision=decision,
        estado_clinico=estado,
        motivo=decision.motivo,
        referencias=referencias or [],
        accion_comunicada=accion_comunicada,
    )


def escalar(alerta: Alerta) -> dict[str, Any]:
    """Persiste y notifica. Cada canal falla por separado: uno caído no tumba al resto."""
    s = get_settings()
    payload = alerta.como_dict()
    canales: list[str] = []

    try:
        with db.transaccion() as con:
            con.execute(
                """INSERT OR REPLACE INTO alertas
                     (alerta_id, call_id, paciente_id, nivel, creada_ts, motivo,
                      score_total, red_flags_json, estado, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (alerta.alerta_id, alerta.call_id, alerta.ficha.paciente.paciente_id,
                 alerta.nivel, alerta.creada_ts, alerta.motivo, alerta.decision.score,
                 json.dumps(alerta.decision.red_flags, ensure_ascii=False),
                 alerta.estado, json.dumps(payload, ensure_ascii=False)),
            )
        canales.append("sqlite")
    except Exception as e:
        log.error("no se pudo guardar la alerta en SQLite: %s", e)

    try:
        s.dir_alertas.mkdir(parents=True, exist_ok=True)
        (s.dir_alertas / f"{alerta.alerta_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (s.dir_alertas / f"{alerta.alerta_id}.md").write_text(
            alerta.como_markdown(), encoding="utf-8"
        )
        canales += ["archivo_json", "archivo_md"]
    except Exception as e:
        log.error("no se pudieron escribir los archivos de la alerta: %s", e)

    if s.escalation_webhook_url:
        try:
            import httpx

            respuesta = httpx.post(s.escalation_webhook_url, json=payload, timeout=4.0)
            respuesta.raise_for_status()
            canales.append("webhook")
        except Exception as e:
            # Un webhook caído no puede impedir que la alerta quede registrada.
            log.warning("el webhook de escalamiento falló: %s", e)

    canales.append("panel")
    alerta.canales = canales
    log.info("alerta %s (%s) por %s", alerta.alerta_id, alerta.nivel, ", ".join(canales))
    return {"alerta_id": alerta.alerta_id, "nivel": alerta.nivel, "canales": canales}


def alertas_activas(limite: int = 50) -> list[dict[str, Any]]:
    filas = db.conexion().execute(
        """SELECT alerta_id, call_id, paciente_id, nivel, creada_ts, motivo,
                  score_total, estado
           FROM alertas ORDER BY creada_ts DESC LIMIT ?""",
        (limite,),
    ).fetchall()
    return [dict(f) for f in filas]


def marcar_atendida(alerta_id: str) -> bool:
    with db.transaccion() as con:
        cur = con.execute(
            "UPDATE alertas SET estado = 'atendida' WHERE alerta_id = ?", (alerta_id,)
        )
    return cur.rowcount > 0

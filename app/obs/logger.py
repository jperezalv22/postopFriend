"""Escritura de trazas a JSONL y a SQLite.

`logs/turns.jsonl` y `logs/calls.jsonl` son la fuente cruda de la que
`scripts/report_metrics.py` genera la tabla de métricas del README. Nadie
transcribe números a mano: el README no puede contradecir a los logs.

JSONL en vez de una tabla: se puede abrir con `type`, `cat` o cualquier editor
durante la sesión de evaluación, y una escritura corrupta no invalida el resto.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.obs.trace import TurnTrace

_candado = threading.Lock()
log = logging.getLogger("postopfriend")


def ahora_iso() -> str:
    """Marca de tiempo local con zona horaria. Para fechas, no para medir latencias."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _anexar(ruta: Path, registro: dict[str, Any]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    linea = json.dumps({"ts": ahora_iso(), **registro}, ensure_ascii=False)
    with _candado:  # varias llamadas concurrentes escriben el mismo archivo
        with ruta.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")


def registrar_turno(traza: TurnTrace) -> None:
    s = get_settings()
    _anexar(s.dir_logs / "turns.jsonl", traza.como_dict())


def registrar_llamada(resumen: dict[str, Any]) -> None:
    s = get_settings()
    _anexar(s.dir_logs / "calls.jsonl", resumen)


def registrar_kb(evento: str, detalle: dict[str, Any]) -> None:
    """Auditoría del conocimiento vivo: quién, qué, cuándo, cuántos chunks, hash."""
    s = get_settings()
    _anexar(s.ruta_kb_audit, {"evento": evento, **detalle})


def leer_jsonl(ruta: Path) -> list[dict[str, Any]]:
    """Lee un JSONL saltándose líneas corruptas en vez de reventar."""
    if not ruta.exists():
        return []
    registros: list[dict[str, Any]] = []
    with ruta.open("r", encoding="utf-8") as f:
        for n, linea in enumerate(f, start=1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                registros.append(json.loads(linea))
            except json.JSONDecodeError:
                log.warning("línea %d de %s ilegible, se omite", n, ruta.name)
    return registros

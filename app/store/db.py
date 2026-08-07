"""SQLite: llamadas, turnos, alertas, documentos y auditoría del conocimiento.

Cero servicios externos. El jurado puede abrir `data/postop.db` con cualquier
visor y verificar que lo que muestra el panel es lo que quedó persistido.

El esquema se crea solo al arrancar. `PRAGMA user_version` hace de número de
migración: si algún día cambia el esquema, se sabe contra qué versión se está.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import get_settings

VERSION_ESQUEMA = 1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS llamadas (
    call_id           TEXT PRIMARY KEY,
    paciente_id       TEXT NOT NULL,
    dia_postop        INTEGER,
    procedimiento     TEXT,
    inicio_ts         TEXT NOT NULL,
    fin_ts            TEXT,
    duracion_s        REAL,
    estado            TEXT NOT NULL DEFAULT 'en_curso',  -- en_curso|completa|incompleta|no_disponible
    nivel_triage      TEXT,                              -- verde|amarillo|rojo|indeterminado
    score_total       INTEGER,
    turnos            INTEGER DEFAULT 0,
    modelo_llm        TEXT,
    acta_json         TEXT
);

CREATE TABLE IF NOT EXISTS turnos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id           TEXT NOT NULL REFERENCES llamadas(call_id) ON DELETE CASCADE,
    turno_idx         INTEGER NOT NULL,
    hablante          TEXT NOT NULL,                     -- agente|paciente|tercero|sistema
    texto             TEXT NOT NULL,
    ts                TEXT NOT NULL,
    latencia_ms       REAL,                              -- solo turnos del agente
    estado_flujo      TEXT,
    nivel_triage      TEXT,
    tokens_in         INTEGER DEFAULT 0,
    tokens_out        INTEGER DEFAULT 0,
    referencias_json  TEXT,
    incidencias_json  TEXT,
    UNIQUE(call_id, turno_idx)
);

CREATE TABLE IF NOT EXISTS alertas (
    alerta_id         TEXT PRIMARY KEY,
    call_id           TEXT REFERENCES llamadas(call_id) ON DELETE SET NULL,
    paciente_id       TEXT NOT NULL,
    nivel             TEXT NOT NULL,
    creada_ts         TEXT NOT NULL,
    motivo            TEXT,
    score_total       INTEGER,
    red_flags_json    TEXT,
    estado            TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente|atendida
    payload_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    doc_id            TEXT PRIMARY KEY,
    titulo            TEXT NOT NULL,
    archivo           TEXT NOT NULL,
    procedimiento     TEXT,
    idioma            TEXT,
    paginas           INTEGER,
    chunks            INTEGER,
    sha256            TEXT NOT NULL,
    origen            TEXT NOT NULL,                     -- base|subido
    estado            TEXT NOT NULL,                     -- disponible|sin_texto|error
    detalle           TEXT,
    ingesta_ts        TEXT NOT NULL,
    kb_version        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_meta (
    clave             TEXT PRIMARY KEY,
    valor             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turnos_call   ON turnos(call_id);
CREATE INDEX IF NOT EXISTS idx_alertas_nivel ON alertas(nivel, estado);
CREATE INDEX IF NOT EXISTS idx_docs_sha      ON documentos(sha256);
CREATE INDEX IF NOT EXISTS idx_docs_origen   ON documentos(origen);
"""

_local = threading.local()


def _conectar() -> sqlite3.Connection:
    s = get_settings()
    s.dir_data.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(s.ruta_db, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")     # lecturas del panel sin bloquear la llamada
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def conexion() -> sqlite3.Connection:
    """Una conexión por hilo. SQLite no admite compartirlas alegremente."""
    con = getattr(_local, "con", None)
    if con is None:
        con = _local.con = _conectar()
    return con


@contextmanager
def transaccion() -> Iterator[sqlite3.Connection]:
    con = conexion()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def inicializar() -> None:
    """Crea el esquema si falta. Idempotente; se llama en el arranque de FastAPI."""
    with transaccion() as con:
        con.executescript(ESQUEMA)
        con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")
        con.execute(
            "INSERT OR IGNORE INTO kb_meta (clave, valor) VALUES ('kb_version', '1')"
        )


# ─── Conocimiento: versión del índice ────────────────────────────────────────
# Toda clave de caché del RAG lleva este número. Es lo que impide que el agente
# «recuerde» un documento borrado, que es exactamente cómo se falla la compuerta G5.

def kb_version() -> int:
    fila = conexion().execute("SELECT valor FROM kb_meta WHERE clave='kb_version'").fetchone()
    return int(fila["valor"]) if fila else 1


def incrementar_kb_version() -> int:
    """Se llama en toda alta y toda baja de documento. `inicializar()` garantiza la fila."""
    with transaccion() as con:
        con.execute(
            "UPDATE kb_meta SET valor = CAST(CAST(valor AS INTEGER) + 1 AS TEXT) "
            "WHERE clave = 'kb_version'"
        )
    return kb_version()


# ─── Utilidades ──────────────────────────────────────────────────────────────

def como_dicts(filas: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(f) for f in filas]


def json_o_none(valor: Any) -> str | None:
    return None if valor is None else json.dumps(valor, ensure_ascii=False)

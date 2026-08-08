"""Lo que queda registrado tiene que ser lo que de verdad se llamó.

Mientras el plan de pago de Groq esté cerrado, el desarrollo va por OpenRouter y
la grabación final por Groq. La base de datos va a tener llamadas de las dos
rutas mezcladas, así que cada una tiene que decir por dónde salió: sin eso no hay
manera de saber qué cifra del informe vino de qué sitio, y la rúbrica contrasta
las métricas reportadas contra los logs.

El riesgo concreto que cierran estas pruebas: Groq y OpenRouter nombran el mismo
modelo distinto, y registrar siempre el nombre de Groq deja logs que declaran un
identificador que nunca se pidió.
"""

from __future__ import annotations

import pytest

from app.agent.llm import modelo_en_uso
from app.config import get_settings
from app.store import db


@pytest.fixture(autouse=True)
def _sin_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_el_modelo_registrado_es_el_de_la_ruta_activa(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "groq")
    get_settings.cache_clear()
    assert modelo_en_uso() == get_settings().llm_model

    monkeypatch.setenv("LLM_BACKEND", "openrouter")
    get_settings.cache_clear()
    s = get_settings()
    assert modelo_en_uso() == s.openrouter_model
    assert modelo_en_uso() != s.llm_model, (
        "si ambos nombres coinciden esta prueba no comprueba nada"
    )


def test_la_tabla_de_llamadas_guarda_la_ruta():
    """Sin `ruta_llm` no se puede separar lo medido en Groq de lo medido fuera."""
    db.inicializar()
    columnas = {fila[1] for fila in db.conexion().execute("PRAGMA table_info(llamadas)")}
    assert "ruta_llm" in columnas
    assert "modelo_llm" in columnas


def test_la_columna_se_agrega_a_una_base_que_ya_existia():
    """`CREATE TABLE IF NOT EXISTS` no añade columnas a una tabla ya creada.

    Como la base de desarrollo ya tiene llamadas dentro, la migración tiene que
    aplicarse aparte y ser idempotente: se llama en cada arranque.
    """
    db.inicializar()
    db.inicializar()  # dos veces: el `duplicate column` no puede propagarse
    columnas = {fila[1] for fila in db.conexion().execute("PRAGMA table_info(llamadas)")}
    assert "ruta_llm" in columnas

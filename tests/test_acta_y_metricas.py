"""El acta y las métricas, contra una base real.

Lo que estas pruebas protegen no es el formato sino dos propiedades que la rúbrica
comprueba de frente:

**Que el acta exista siempre.** Una llamada que se corta a la mitad es justamente
la que hay que poder auditar, así que el acta no puede depender de que el flujo
haya llegado al final. Aquí se genera una sin decisión y sin ficha y se exige que
salgan las diez secciones.

**Que las métricas del informe salgan del mismo sitio que las del panel.** Si
`report_metrics.py` calculara sus propios percentiles, el README y la pantalla
podrían divergir sin que nadie lo notara hasta la sesión de evaluación.

La base se crea en un directorio temporal: estas pruebas no leen `data/postop.db`,
que en la máquina de desarrollo tiene llamadas reales y haría que el resultado
dependiera de cuántas veces se probó el micrófono.
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.obs import metricas
from app.store import acta as acta_mod
from app.store import db
from app.store.patients import construir_ficha
from app.triage.engine import evaluar
from app.triage.models import EstadoClinico, Variable

CALL = "call_de_prueba"


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Una base vacía por prueba. `db` cachea la conexión por hilo: hay que soltarla."""
    monkeypatch.setenv("POSTOP_TEST_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(type(get_settings()), "ruta_db",
                        property(lambda self: tmp_path / "prueba.db"))
    if hasattr(db._local, "con"):
        db._local.con.close()
        del db._local.con
    db.inicializar()
    yield
    if hasattr(db._local, "con"):
        db._local.con.close()
        del db._local.con
    get_settings.cache_clear()


def _llamada(con, call_id=CALL, ruta="groq", modelo="llama-3.3-70b-versatile"):
    con.execute(
        """INSERT INTO llamadas (call_id, paciente_id, dia_postop, procedimiento,
                                 inicio_ts, estado, modelo_llm, ruta_llm)
           VALUES (?,?,?,?,?,?,?,?)""",
        (call_id, "pac_42_00026", 7, "Apendicectomía",
         "2026-08-08T10:00:00-05:00", "en_curso", modelo, ruta),
    )


def _turno(con, idx, hablante, texto, latencia=None, call_id=CALL, etapas=None,
           tokens=(0, 0), incidencias=None):
    con.execute(
        """INSERT INTO turnos (call_id, turno_idx, hablante, texto, ts, latencia_ms,
                               tokens_in, tokens_out, llm_calls, rag_consultas,
                               audio_paciente_s, etapas_json, incidencias_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (call_id, idx, hablante, texto, "2026-08-08T10:00:05-05:00", latencia,
         tokens[0], tokens[1], 2 if hablante == "agente" else 0, 1, 3.0,
         json.dumps(etapas or {}), json.dumps(incidencias or [])),
    )


# ─── Métricas ────────────────────────────────────────────────────────────────

class TestPercentil:
    def test_sin_datos_no_inventa_un_cero(self):
        """`None` y 0 ms significan cosas opuestas: «no medido» y «instantáneo»."""
        assert metricas.percentil([], 0.5) is None

    def test_interpola_entre_los_dos_valores_vecinos(self):
        assert metricas.percentil([100, 200], 0.5) == 150.0

    def test_p95_de_una_muestra_corta_no_se_sale_del_rango(self):
        valores = [100, 200, 300, 400]
        assert metricas.percentil(valores, 0.95) <= 400


class TestFiltroDeRuta:
    def test_groq_incluye_las_llamadas_sin_ruta_registrada(self, base):
        """Las llamadas anteriores a la columna son de cuando OpenRouter no existía.

        Excluirlas silenciosamente borraría mediciones legítimas del informe.
        """
        with db.transaccion() as con:
            _llamada(con, "vieja", ruta=None)
            _turno(con, 0, "agente", "hola", latencia=900.0, call_id="vieja")
        assert metricas.resumen(ruta_llm="groq")["latencia"]["n"] == 1

    def test_openrouter_no_arrastra_las_de_groq(self, base):
        with db.transaccion() as con:
            _llamada(con, "a", ruta="groq")
            _turno(con, 0, "agente", "x", latencia=800.0, call_id="a")
            _llamada(con, "b", ruta="openrouter")
            _turno(con, 0, "agente", "y", latencia=2000.0, call_id="b")

        solo_groq = metricas.resumen(ruta_llm="groq")
        assert solo_groq["latencia"]["n"] == 1
        assert solo_groq["latencia"]["p50"] == 800.0
        assert metricas.resumen(ruta_llm="openrouter")["latencia"]["p50"] == 2000.0
        assert metricas.resumen()["latencia"]["n"] == 2


class TestResumen:
    def test_los_turnos_del_paciente_no_cuentan_como_latencia(self, base):
        """Solo el agente tiene latencia: es el tiempo que el paciente espera."""
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "¿cómo va el dolor?", latencia=1200.0)
            _turno(con, 1, "paciente", "en cinco")
        r = metricas.resumen(call_id=CALL)
        assert r["latencia"]["n"] == 1
        assert r["consumo"]["turnos_del_agente"] == 1

    def test_el_desglose_por_etapa_sale_del_json_del_turno(self, base):
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "a", latencia=1000.0,
                   etapas={"stt": 300.0, "tts": 200.0})
            _turno(con, 2, "agente", "b", latencia=1400.0,
                   etapas={"stt": 500.0, "tts": 400.0})
        etapas = metricas.resumen(call_id=CALL)["etapas_ms"]
        assert etapas["stt"]["p50"] == 400.0
        assert etapas["stt"]["n"] == 2

    def test_las_incidencias_se_agrupan_por_clase_no_por_texto(self, base):
        """Interesa cuántos audios llegaron mal, no cuántas variantes del motivo."""
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "a", incidencias=["audio_degradado:silencio"])
            _turno(con, 2, "agente", "b", incidencias=["audio_degradado:ruido"])
        assert metricas.resumen(call_id=CALL)["incidencias"]["audio_degradado"] == 2

    def test_el_costo_usa_la_tarifa_del_modelo_que_se_midio(self, base):
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "a", latencia=1000.0, tokens=(1_000_000, 0))
        r = metricas.resumen(call_id=CALL)
        # 1 M de tokens de entrada de llama-3.3-70b: US$ 0.59 (app/obs/tokens.py).
        assert r["costo_usd"]["llm"] == pytest.approx(0.59, abs=1e-6)
        assert r["tarifa_aplicada"] == "llama-3.3-70b-versatile"

    def test_una_base_vacia_no_revienta_ni_divide_por_cero(self, base):
        r = metricas.resumen()
        assert r["latencia"]["p50"] is None
        assert r["costo_usd"]["por_turno"] is None
        assert r["llamadas"]["n"] == 0


# ─── Acta ────────────────────────────────────────────────────────────────────

SECCIONES = (
    "identificacion", "llamada", "transcripcion", "estado_clinico",
    "sintomas_libres", "decision", "referencias", "proximos_pasos",
    "incidencias", "metricas",
)


class TestActa:
    def test_tiene_las_diez_secciones_del_plan(self, base):
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "hola", latencia=900.0)
        documento = acta_mod.construir(CALL)
        for seccion in SECCIONES:
            assert seccion in documento, f"falta la sección «{seccion}» (§7.6)"

    def test_se_genera_aunque_la_llamada_se_haya_cortado_sin_decidir(self, base):
        """Es el caso que hay que poder auditar, no la excepción a la regla."""
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "buenos días", latencia=800.0)
        documento = acta_mod.construir(CALL, estado="incompleta")
        assert documento["llamada"]["estado"] == "incompleta"
        assert documento["decision"]["nivel"] is None
        assert acta_mod.como_markdown(documento)  # y sigue siendo legible

    def test_la_transcripcion_trae_los_dos_lados(self, base):
        """Un acta con solo los turnos del agente no es una transcripción."""
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "¿cómo va el dolor?", latencia=900.0)
            _turno(con, 1, "paciente", "va en seis")
        hablantes = [t["hablante"] for t in acta_mod.construir(CALL)["transcripcion"]]
        assert hablantes == ["agente", "paciente"]

    def test_recoge_la_decision_y_la_evidencia_textual(self, base):
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "¿y la herida?", latencia=900.0)

        estado = EstadoClinico(
            dolor_nrs=Variable(valor=6, confianza=0.9, evidencia="como un 6"),
            fiebre_c=Variable(valor=38.0, confianza=0.9, evidencia="como 38"),
            herida=Variable(valor="secrecion_purulenta", confianza=0.9,
                            evidencia="un líquido amarillo"),
        )
        decision = evaluar(estado, comorbilidades=[], dia_postop=7)
        documento = acta_mod.construir(
            CALL, ficha=construir_ficha("pac_42_00026", 7),
            estado_clinico=estado, decision=decision,
        )

        assert documento["decision"]["nivel"] == "rojo"
        assert documento["estado_clinico"]["herida"]["evidencia"] == "un líquido amarillo"
        # La evidencia tiene que sobrevivir a la exportación: es lo que permite
        # contrastar la alerta contra la grabación.
        assert "un líquido amarillo" in acta_mod.como_markdown(documento)

    def test_el_markdown_lleva_el_aviso_de_datos_sinteticos(self, base):
        with db.transaccion() as con:
            _llamada(con)
        assert "sintéticos" in acta_mod.como_markdown(acta_mod.construir(CALL))

    def test_guardar_y_cargar_devuelven_lo_mismo(self, base):
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "hola", latencia=900.0)
        documento = acta_mod.construir(CALL)
        acta_mod.guardar(CALL, documento)
        assert acta_mod.cargar(CALL) == documento

    def test_una_llamada_sin_acta_guardada_se_reconstruye_desde_las_tablas(self, base):
        """Devolver una parcial es mejor que `None`: la transcripción sigue ahí."""
        with db.transaccion() as con:
            _llamada(con)
            _turno(con, 0, "agente", "hola", latencia=900.0)
        recuperada = acta_mod.cargar(CALL)
        assert recuperada is not None
        assert len(recuperada["transcripcion"]) == 1
        assert "reconstruida" in recuperada["identificacion"]["nota"]

    def test_una_llamada_inexistente_devuelve_none(self, base):
        assert acta_mod.cargar("no_existe") is None

    def test_las_metricas_del_acta_son_las_de_esa_llamada_y_no_las_globales(self, base):
        with db.transaccion() as con:
            _llamada(con, "a")
            _turno(con, 0, "agente", "x", latencia=500.0, call_id="a")
            _llamada(con, "b")
            _turno(con, 0, "agente", "y", latencia=3000.0, call_id="b")
        assert acta_mod.construir("a")["metricas"]["latencia_ms"]["p50"] == 500.0

"""El motor de triage. Es la parte de la solución donde un error cuesta un paciente.

Todo corre sin API y sin red: el triage tiene que ser probable en milisegundos,
y esa es justamente la razón de que el LLM no decida el nivel.
"""

import pytest

from app.triage.engine import (
    accion_para,
    calcular_score,
    cargar_reglas,
    detectar_red_flags,
    evaluar,
)
from app.triage.models import EstadoClinico, Nivel, Variable


def var(valor, evidencia="lo dijo el paciente", confianza=0.9):
    return Variable(valor=valor, evidencia=evidencia, confianza=confianza)


def estado(**kw) -> EstadoClinico:
    """Estado con las tres críticas resueltas en normal, salvo lo que se pise."""
    base = {
        "dolor_nrs": var(1),
        "fiebre_c": var(36.8),
        "herida": var("normal"),
    }
    base.update({k: (v if isinstance(v, Variable) else var(v)) for k, v in kw.items()})
    return EstadoClinico(**base)


@pytest.fixture(scope="module")
def reglas():
    return cargar_reglas()


class TestCortes:
    """Los tres cortes: >=6 rojo, 2-5 amarillo, 0-1 verde."""

    def test_todo_normal_es_verde(self):
        assert evaluar(estado()).nivel == Nivel.VERDE

    def test_un_solo_signo_leve_sigue_siendo_verde(self):
        # score 1: febrícula sola no escala a nadie.
        d = evaluar(estado(fiebre_c=var(37.6)))
        assert d.score == 1
        assert d.nivel == Nivel.VERDE

    def test_score_2_es_amarillo(self):
        # febrícula (1) + dolor moderado (1)
        d = evaluar(estado(fiebre_c=var(37.6), dolor_nrs=var(5)), moduladores=False)
        assert d.score == 2
        assert d.nivel == Nivel.AMARILLO

    def test_score_5_sigue_siendo_amarillo(self):
        # fiebre alta (3) + dolor moderado (1) + apetito muy bajo (1)
        d = evaluar(
            estado(fiebre_c=var(38.2), dolor_nrs=var(5), apetito=var("muy_disminuido")),
            moduladores=False,
        )
        assert d.score == 5
        assert d.nivel == Nivel.AMARILLO

    def test_score_6_cruza_a_rojo(self):
        # fiebre alta (3) + dolor severo (3), sin banderas rojas automáticas
        d = evaluar(estado(fiebre_c=var(38.2), dolor_nrs=var(8)), moduladores=False)
        assert d.score == 6
        assert d.nivel == Nivel.ROJO

    def test_la_frontera_exacta_esta_donde_dice_el_yaml(self, reglas):
        assert reglas["cortes"] == {"rojo": 6, "amarillo": 2}


class TestBanderasRojas:
    """Cortocircuitan el score: no se compensan con buenas noticias."""

    def test_sangrado_activo_es_rojo_con_todo_lo_demas_normal(self):
        e = estado()
        e.red_flags = ["sangrado_activo"]
        d = evaluar(e)
        assert d.nivel == Nivel.ROJO
        assert d.score <= 1, "el score sigue siendo bajo: manda la bandera, no el score"
        assert "sangrado_activo" in d.red_flags

    def test_la_fiebre_muy_alta_dispara_bandera_aunque_el_extractor_no_la_marque(self):
        d = evaluar(estado(fiebre_c=var(38.6)))
        assert d.nivel == Nivel.ROJO
        assert "fiebre_muy_alta" in d.red_flags

    def test_la_secrecion_purulenta_dispara_bandera_por_si_sola(self):
        d = evaluar(estado(herida=var("secrecion_purulenta")))
        assert d.nivel == Nivel.ROJO
        assert "secrecion_purulenta" in d.red_flags

    def test_la_dehiscencia_dispara_bandera(self):
        d = evaluar(estado(herida=var("dehiscencia")))
        assert d.nivel == Nivel.ROJO
        assert "herida_abierta" in d.red_flags

    def test_manda_sobre_el_estado_incompleto(self):
        # No hace falta saber el apetito para mandar a alguien a urgencias.
        e = EstadoClinico(dolor_nrs=var(2))
        e.red_flags = ["dificultad_respiratoria"]
        assert evaluar(e).nivel == Nivel.ROJO

    def test_se_ignora_una_bandera_inventada(self):
        e = estado()
        e.red_flags = ["el_paciente_esta_de_mal_genio"]
        d = evaluar(e)
        assert d.red_flags == []
        assert d.nivel == Nivel.VERDE


class TestEstadoIncompleto:
    """La regla que responde al sub-criterio de ambigüedad de la rúbrica."""

    def test_sin_fiebre_conocida_no_se_puede_decidir(self):
        e = EstadoClinico(dolor_nrs=var(2), herida=var("normal"))
        d = evaluar(e)
        assert d.nivel == Nivel.INDETERMINADO
        assert "fiebre_c" in d.criticas_pendientes

    def test_sin_herida_conocida_tampoco(self):
        e = EstadoClinico(dolor_nrs=var(2), fiebre_c=var(36.9))
        assert evaluar(e).nivel == Nivel.INDETERMINADO

    def test_un_valor_sin_evidencia_textual_no_cuenta(self):
        # El extractor no puede afirmar sin citar al paciente.
        e = EstadoClinico(
            dolor_nrs=Variable(valor=3, evidencia=None),
            fiebre_c=var(36.8),
            herida=var("normal"),
        )
        d = evaluar(e)
        assert d.nivel == Nivel.INDETERMINADO
        assert "dolor_nrs" in d.criticas_pendientes

    def test_agotados_los_reintentos_se_escala_por_precaucion(self):
        # No saber si hay fiebre no es lo mismo que saber que no la hay.
        e = EstadoClinico(dolor_nrs=var(2), herida=var("normal"))
        d = evaluar(e, intentos_agotados=True)
        assert d.nivel == Nivel.AMARILLO
        assert "información insuficiente" in d.motivo

    def test_agotados_los_reintentos_un_score_alto_sigue_siendo_rojo(self):
        e = EstadoClinico(dolor_nrs=var(8), movilidad=var("incapacitante_nueva"))
        d = evaluar(e, intentos_agotados=True, moduladores=False)
        assert d.score == 6
        assert d.nivel == Nivel.ROJO

    def test_nunca_cierra_en_verde_con_informacion_critica_faltante(self):
        e = EstadoClinico(dolor_nrs=var(0))
        for agotados in (False, True):
            assert evaluar(e, intentos_agotados=agotados).nivel != Nivel.VERDE


class TestModuladores:
    """Implementados y documentados, pero APAGADOS por defecto: ver app/config.py.
    Estas pruebas los fuerzan a mano para verificar que la regla funciona."""

    def test_la_comorbilidad_suma_solo_si_ya_hay_algun_signo(self):
        d = evaluar(estado(), comorbilidades=["diabetes_tipo_2"], moduladores=True)
        assert d.score == 0, "tener diabetes sin síntomas no puede escalar a nadie"
        assert d.nivel == Nivel.VERDE

    def test_la_comorbilidad_suma_sobre_un_cuadro_con_signos(self):
        d = evaluar(estado(dolor_nrs=var(5)), comorbilidades=["diabetes_tipo_2"], moduladores=True)
        assert d.score == 2
        assert d.score_sin_moduladores == 1
        assert d.nivel == Nivel.AMARILLO

    def test_la_fiebre_tardia_suma_desde_el_dia_7(self):
        con = evaluar(estado(fiebre_c=var(37.7)), dia_postop=7, moduladores=True)
        sin = evaluar(estado(fiebre_c=var(37.7)), dia_postop=3, moduladores=True)
        assert con.score == 2 and sin.score == 1

    def test_se_pueden_apagar_y_el_score_base_queda_registrado(self):
        d = evaluar(
            estado(dolor_nrs=var(5)), comorbilidades=["obesidad"], moduladores=False
        )
        assert d.score == d.score_sin_moduladores == 1
        assert d.moduladores_activos is False


class TestTrazabilidad:
    """El jurado tiene que poder reconstruir el score a mano desde el desglose."""

    def test_el_desglose_suma_exactamente_el_score(self):
        d = evaluar(
            estado(fiebre_c=var(38.1), dolor_nrs=var(5), apetito=var("muy_disminuido")),
            comorbilidades=["diabetes_tipo_2"],
            dia_postop=7,
            moduladores=True,
        )
        assert sum(r.puntos for r in d.desglose) == d.score

    def test_cada_regla_arrastra_la_cita_del_paciente(self):
        d = evaluar(estado(dolor_nrs=var(8, "el dolor está como en ocho")))
        regla = next(r for r in d.desglose if r.regla == "dolor_severo")
        assert regla.evidencia == "el dolor está como en ocho"

    def test_cada_regla_declara_su_sustento_clinico(self):
        d = evaluar(estado(fiebre_c=var(38.2)), moduladores=False)
        regla = next(r for r in d.desglose if r.regla == "fiebre_alta")
        assert "38" in regla.fuente_clinica

    def test_la_decision_declara_la_version_de_las_reglas(self, reglas):
        assert evaluar(estado()).version_reglas == reglas["version"]

    def test_es_determinista(self):
        e = estado(fiebre_c=var(37.8), dolor_nrs=var(6), sueno=var("muy_alterado"))
        resultados = {evaluar(e, comorbilidades=["obesidad"], dia_postop=7, moduladores=True).score for _ in range(20)}
        assert len(resultados) == 1


class TestCasoRealDelDataset:
    """pac_42_00026, día 7 postapendicectomía: el caso rojo del video."""

    def test_reproduce_el_rojo_con_su_desglose(self):
        e = EstadoClinico(
            fiebre_c=var(38.0, "me sentí afiebrada, como 38"),
            dolor_nrs=var(6, "el dolor sí está fu- como un 6"),
            herida=var("secrecion_purulenta", "la he visto como con un líquido, amarillo creo"),
            apetito=var("muy_disminuido", "no me ha dado hambre"),
            sueno=var("muy_alterado", "casi no he dormido"),
        )
        d = evaluar(e, comorbilidades=["diabetes_tipo_2"], dia_postop=7, moduladores=True)
        assert d.nivel == Nivel.ROJO
        assert "secrecion_purulenta" in d.red_flags
        assert d.score >= 9
        assert accion_para(d.nivel)["plazo"] == "inmediato"


class TestAcciones:
    def test_cada_nivel_tiene_plazo_y_mensaje(self):
        for nivel in (Nivel.VERDE, Nivel.AMARILLO, Nivel.ROJO, Nivel.INDETERMINADO):
            accion = accion_para(nivel)
            assert accion["plazo"] and accion["mensaje"]


class TestScoreDirecto:
    def test_calcular_score_devuelve_total_base_y_desglose(self, reglas):
        total, base, desglose = calcular_score(
            estado(dolor_nrs=var(7)), reglas, comorbilidades=["obesidad"], dia_postop=3,
            moduladores=True,
        )
        assert base == 3 and total == 4
        assert {r.regla for r in desglose} == {"dolor_severo", "comorbilidad_riesgo"}

    def test_detectar_red_flags_no_inventa(self):
        assert detectar_red_flags(estado()) == []

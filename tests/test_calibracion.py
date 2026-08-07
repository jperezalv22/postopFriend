"""Fija la calibración del motor contra los 160 casos etiquetados del dataset.

Existe para que las cifras del README no puedan desviarse en silencio. Si alguien
toca un peso de `rules.yaml` y con eso pierde un rojo, esta prueba se pone roja
antes de que el número llegue al informe.

Corre sin API y sin red: solo lee los xlsx y llama al motor.
"""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "evals"))

from dataset import cargar_casos  # noqa: E402
from metricas import Resultado, resumir  # noqa: E402
from run_engine_eval import estado_desde_trayectoria  # noqa: E402

from app.store.patients import obtener_paciente  # noqa: E402
from app.triage.engine import evaluar  # noqa: E402


def _evaluar_todo(moduladores: bool) -> dict:
    casos = [c for c in cargar_casos() if c.capa == "capa1_limpia"]
    resultados = [
        Resultado(
            caso_id=c.caso_id, capa="trayectoria", esperado=c.etiqueta,
            obtenido=str(
                evaluar(
                    estado_desde_trayectoria(c),
                    comorbilidades=(obtener_paciente(c.paciente_id).comorbilidades
                                    if obtener_paciente(c.paciente_id) else []),
                    dia_postop=c.dia_postop,
                    moduladores=moduladores,
                ).nivel
            ),
        )
        for c in casos
    ]
    return resumir(resultados)


@pytest.fixture(scope="module")
def sin_moduladores():
    return _evaluar_todo(moduladores=False)


class TestCalibracion:
    def test_el_dataset_trae_los_160_casos_esperados(self, sin_moduladores):
        assert sin_moduladores["n"] == 160
        assert sin_moduladores["n_verde"] == 123
        assert sin_moduladores["n_amarillo"] == 25
        assert sin_moduladores["n_rojo"] == 12

    def test_no_se_pierde_ni_un_rojo(self, sin_moduladores):
        # El número que decide el reto. Si esto falla, no se entrega.
        assert sin_moduladores["rojos_perdidos"] == 0
        assert sin_moduladores["recall_rojo"] == 1.0

    def test_cero_falsos_negativos_en_cualquier_nivel(self, sin_moduladores):
        # Ningún caso se clasifica como menos grave de lo que es.
        assert sin_moduladores["falsos_negativos"] == 0

    def test_ningun_amarillo_se_escapa(self, sin_moduladores):
        assert sin_moduladores["recall_amarillo"] == 1.0

    def test_la_exactitud_no_baja_de_lo_reportado(self, sin_moduladores):
        # 92.5 % es lo que dice el README. Se admite mejora, no retroceso.
        assert sin_moduladores["exactitud"] >= 0.925

    def test_las_falsas_alarmas_no_se_disparan(self, sin_moduladores):
        # 12 verdes escalados a amarillo es el precio aceptado de no perder ninguno.
        assert sin_moduladores["sobre_escalados"] <= 12


class TestModuladores:
    def test_encenderlos_empeora_la_precision_sin_ganar_sensibilidad(self):
        """La medición que justifica que estén apagados por defecto.

        Si algún día dejara de ser cierta, la decisión habría que revisarla — y esta
        prueba es la que avisaría.
        """
        con = _evaluar_todo(moduladores=True)
        sin = _evaluar_todo(moduladores=False)
        assert con["recall_rojo"] == sin["recall_rojo"] == 1.0
        assert con["sobre_escalados"] > sin["sobre_escalados"]
        assert con["exactitud"] < sin["exactitud"]

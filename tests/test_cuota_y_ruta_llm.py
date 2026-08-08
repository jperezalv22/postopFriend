"""La evaluación sobrevive a la cuota, y no mide contra el backend equivocado.

Estas pruebas existen por un problema real del 7 de agosto de 2026. El nivel
gratuito de Groq tiene un tope de 100 000 tokens **por día** que no aparece en las
cabeceras `x-ratelimit-*`: solo se ve al chocar con él. La evaluación completa
necesita ~896 000, el Dev Tier estaba cerrado a nuevas altas, y se resolvió pidiendo
la misma inferencia de Groq a través de OpenRouter.

Eso abre dos formas nuevas de publicar una cifra falsa, y las dos se prueban aquí:

1. que una corrida cortada por cuota se lea como si fuera del dataset completo;
2. que OpenRouter enrute a otro backend y la medición describa un modelo que no es
   el que se entrega.

Ninguna prueba toca la red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
for extra in (RAIZ, RAIZ / "evals", RAIZ / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.agent import llm  # noqa: E402
from evals.dataset import Caso, Trayectoria  # noqa: E402
from evals.metricas import Resultado  # noqa: E402
from evals.run_triage_eval import cobertura, ordenar_por_valor  # noqa: E402


def _caso(caso_id: str, etiqueta: str, capa: str) -> Caso:
    t = Trayectoria(trayectoria_id=caso_id, paciente_id="p", dia_postop=7, arquetipo="a",
                    dolor_nrs=5, fiebre_c=37.0, movilidad="m", herida="h",
                    apetito="a", sueno="s")
    return Caso(caso_id=caso_id, paciente_id="p", dia_postop=7, capa=capa,
                etiqueta=etiqueta, trayectoria=t)


# ─── 1. Una corrida cortada por cuota ──────────────────────────────────────────

def test_los_rojos_se_piden_antes_que_los_verdes():
    """Con cuota para 2 de 6 casos, esas 2 llamadas tienen que comprar recall de rojo.

    El orden no cambia ninguna métrica cuando la corrida termina; decide cuál se
    puede publicar cuando no termina, que es el caso normal en el nivel gratuito.
    """
    casos = [_caso("c1", "verde", "capa1_limpia"), _caso("c2", "rojo", "capa1_limpia"),
             _caso("c3", "amarillo", "capa1_limpia"), _caso("c4", "rojo", "capa1_limpia")]
    orden = [c.etiqueta for c in ordenar_por_valor(casos)]
    assert orden == ["rojo", "rojo", "amarillo", "verde"]


def test_las_dos_capas_del_mismo_caso_van_pegadas():
    """La comparación limpia/ruidosa es lo que mide al extractor, no al motor.

    Si un corte por cuota se llevara todas las capa2, quedaría medido el motor y no
    el extractor, que es justo lo que esta evaluación existe para medir.
    """
    casos = [_caso("c1", "rojo", "capa2_ruidosa"), _caso("c2", "rojo", "capa1_limpia"),
             _caso("c1", "rojo", "capa1_limpia"), _caso("c2", "rojo", "capa2_ruidosa")]
    orden = [(c.caso_id, c.capa) for c in ordenar_por_valor(casos)]
    assert orden == [("c1", "capa1_limpia"), ("c1", "capa2_ruidosa"),
                     ("c2", "capa1_limpia"), ("c2", "capa2_ruidosa")]


def test_la_cobertura_se_mide_contra_el_dataset_y_no_contra_la_muestra():
    """El denominador es el dataset completo, incluso si solo se pidió un trozo.

    Es la diferencia entre «recall de rojo 100 %» y «recall de rojo 100 % sobre 2
    de 24 rojos». La segunda es una frase honesta; la primera, en una corrida
    parcial, no lo es.
    """
    universo = ([_caso(f"r{i}", "rojo", "capa1_limpia") for i in range(24)]
                + [_caso(f"v{i}", "verde", "capa1_limpia") for i in range(246)])
    medidos = [Resultado(caso_id=f"r{i}", capa="capa1_limpia", esperado="rojo",
                         obtenido="rojo") for i in range(2)]

    cob = cobertura(medidos, universo)
    assert cob["rojo"] == (2, 24)
    assert cob["verde"] == (0, 246)
    assert list(cob) == ["rojo", "verde"], "se lista por gravedad, no alfabéticamente"


# ─── 2. La ruta alterna no puede medir contra otro backend ─────────────────────

class _RespuestaFalsa:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)


def _payload(proveedor: str) -> dict:
    return {
        "provider": proveedor,
        "choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }


@pytest.fixture
def por_openrouter(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_BACKEND", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-de-prueba")
    yield
    get_settings.cache_clear()


def test_se_exige_groq_sin_sustitutos(por_openrouter, monkeypatch):
    """La petición fija el proveedor. Un fallback silencioso mediría otro modelo."""
    capturado: dict = {}

    def falso_post(url, headers=None, json=None, timeout=None):
        capturado.update(json or {})
        return _RespuestaFalsa(_payload("Groq"))

    import httpx
    monkeypatch.setattr(httpx, "post", falso_post)

    r = llm.chat_sync([{"role": "user", "content": "hola"}])
    assert capturado["provider"] == {"order": ["groq"], "allow_fallbacks": False}
    assert r.proveedor == "Groq"
    assert r.texto == '{"ok": 1}'


def test_si_lo_sirve_otro_proveedor_no_hay_medicion(por_openrouter, monkeypatch):
    """Preferible quedarse sin dato que con un dato de otro backend.

    La respuesta vuelve vacía y con incidencia `llm_error:`, que es el prefijo que
    `run_triage_eval.hubo_fallo_de_api` reconoce: no se cachea y no entra en la
    tabla. Es la misma lección de §8.1 del traspaso —un resultado silenciosamente
    degradado es peor que un error—, aplicada al enrutado.
    """
    import httpx
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _RespuestaFalsa(_payload("DeepInfra")),
    )

    r = llm.chat_sync([{"role": "user", "content": "hola"}])
    assert r.texto == ""
    assert any(i.startswith("llm_error") for i in r.incidencias)

    from evals.run_triage_eval import hubo_fallo_de_api
    assert hubo_fallo_de_api(r.incidencias), "tiene que contar como fallo de API"


def test_la_ruta_directa_no_toca_openrouter(monkeypatch):
    """El valor por defecto es Groq directo: el jurado clona y funciona sin más."""
    from app.config import Settings, get_settings

    # Se comprueba el valor *declarado*, no el resuelto: el .env de desarrollo
    # puede apuntar a OpenRouter, y eso no debe hacer fallar una prueba sobre lo
    # que recibe quien clona el repo. Leer el resuelto invierte el sentido de la
    # prueba: pasaría en un clon limpio y fallaría en la máquina de trabajo.
    assert Settings.model_fields["llm_backend"].default == "groq"

    monkeypatch.setenv("LLM_BACKEND", "groq")
    get_settings.cache_clear()
    assert get_settings().llm_backend == "groq"

    import httpx
    def prohibido(*a, **k):
        raise AssertionError("la ruta por defecto no debe salir por OpenRouter")
    monkeypatch.setattr(httpx, "post", prohibido)

    llamado: dict = {}

    class _Completions:
        def create(self, **kw):
            llamado.update(kw)
            raise RuntimeError("corte a propósito: basta con saber que fue por aquí")

    class _Chat:
        completions = _Completions()

    class _Cliente:
        chat = _Chat()

    llm._cliente.cache_clear()
    monkeypatch.setattr(llm, "_cliente", lambda: _Cliente())

    llm.chat_sync([{"role": "user", "content": "hola"}])
    assert llamado["model"] == get_settings().llm_model

    # monkeypatch deshace la variable de entorno, pero no el caché de get_settings.
    # Sin esto, las pruebas que corran después verían un backend que ya no es el
    # configurado y el resultado dependería del orden.
    get_settings.cache_clear()


# ─── 3. La sonda de cuota deduce la ventana, no la adivina ─────────────────────

def test_la_ventana_del_limite_sale_de_una_regla_de_tres():
    """Groq no nombra la ventana; el tiempo de reposición la determina.

    Los números son los que devolvió la API el 7 de agosto de 2026: 1 petición de
    1 000 con reposición de 86.4 s son 86 400 s, un día exacto; 37 tokens de 12 000
    con 0.185 s son 60 s, un minuto exacto.
    """
    from cuota_groq import segundos, ventana

    assert segundos("1m26.4s") == pytest.approx(86.4)
    assert segundos("185ms") == pytest.approx(0.185)

    assert ventana(86.4, 1000, 1)[0] == "por día"
    assert ventana(0.185, 12000, 37)[0] == "por minuto"
    assert ventana(None, 1000, 1)[0] == "?"
    assert ventana(86.4, 1000, 0)[0] == "?", "sin consumo no se puede deducir nada"

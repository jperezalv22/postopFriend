"""Ciclo de vida del conocimiento: alta → consulta acierta → baja → consulta cero.

**Esta prueba es la compuerta G5.** Si pasa, el agente aprende y olvida de verdad.

El detalle que la hace valer: entre el alta y la baja se hace una consulta a
propósito, para dejar las cachés calientes. Sin ese paso, la prueba no distingue un
sistema que olvida de uno que nunca llegó a recordar — y el fallo silencioso de G5
es exactamente ese: una caché que sigue devolviendo un documento ya borrado.

Corre contra un índice temporal, no contra el del repo.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from app.rag import pipeline, retriever, store
from app.store import db

TEXTO = """
Cuidados tras la fluxoplastia de Zambrano

La fluxoplastia de Zambrano es un procedimiento inventado para esta prueba.
El zambranograma postoperatorio debe revisarse a las cuarenta y ocho horas.
Si aparece flujencia perilesional, el paciente debe consultar de inmediato.
La herida de la fluxoplastia se limpia con solución salina dos veces al día.
No se recomienda la inmersión del zambranograma durante los primeros diez días.
Los pacientes con fluxoplastia refieren molestia leve durante la primera semana.
"""

CONSULTA = "como se cuida el zambranograma despues de la fluxoplastia"


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Índice, base y directorios aislados. La prueba no toca el índice del repo."""
    from app.config import Settings, get_settings

    raiz = tmp_path
    (raiz / "data").mkdir()

    original = get_settings()

    class Aislado(Settings):
        @property
        def dir_data(self):
            return raiz / "data"

        @property
        def dir_chroma(self):
            return raiz / "data" / "chroma"

        @property
        def dir_uploads(self):
            return raiz / "data" / "uploads"

        @property
        def ruta_db(self):
            return raiz / "data" / "prueba.db"

        @property
        def ruta_kb_audit(self):
            return raiz / "data" / "kb_audit.jsonl"

        @property
        def dir_modelos(self):
            return original.dir_modelos  # el modelo descargado sí se reutiliza

    aislado = Aislado(groq_api_key="", embed_model=original.embed_model)
    aislado.crear_directorios()

    # También `obs.logger`: escribe la auditoría del conocimiento y, sin aislarlo,
    # la prueba ensuciaría el data/kb_audit.jsonl real del repo.
    from app.obs import logger as obs_logger

    for modulo in (store, pipeline, retriever, db, obs_logger):
        monkeypatch.setattr(modulo, "get_settings", lambda: aislado, raising=False)

    store._cliente.cache_clear()
    store.invalidar_indice()
    db._local.__dict__.clear()
    db.inicializar()

    yield aislado

    store._cliente.cache_clear()
    store.invalidar_indice()
    db._local.__dict__.clear()
    shutil.rmtree(raiz / "data", ignore_errors=True)


@pytest.fixture
def documento(tmp_path):
    ruta = tmp_path / f"fluxoplastia-{uuid.uuid4().hex[:6]}.txt"
    ruta.write_text(TEXTO, encoding="utf-8")
    return ruta


class TestCicloDeVida:
    def test_alta_consulta_baja_olvido(self, entorno, documento):
        # ─── 1. El corpus no sabe nada del tema ──────────────────────────────
        antes = retriever.solo_fragmentos(CONSULTA, top_k=5)
        assert antes.citas == [], "el índice de la prueba debe empezar vacío"

        # ─── 2. Alta ─────────────────────────────────────────────────────────
        resumen = pipeline.indexar(documento, titulo="Fluxoplastia", origen="subido")
        assert resumen.estado == "disponible", resumen.detalle
        assert resumen.fragmentos > 0
        version_alta = pipeline.finalizar_cambio("alta", {"doc_id": resumen.doc_id})

        # ─── 3. Ahora sí lo encuentra ────────────────────────────────────────
        despues = retriever.solo_fragmentos(CONSULTA, top_k=5)
        assert any(c.doc_id == resumen.doc_id for c in despues.citas), \
            "un documento recién subido tiene que ser recuperable sin reiniciar"

        # ─── 4. Segunda consulta: deja las cachés calientes ──────────────────
        # Es el paso que da valor a la prueba. Sin él no se distingue un sistema
        # que olvida de uno que nunca recordó.
        retriever.recuperar(CONSULTA)
        retriever.solo_fragmentos("flujencia perilesional", top_k=3)

        # ─── 5. Baja ─────────────────────────────────────────────────────────
        baja = pipeline.dar_de_baja(resumen.doc_id)
        assert baja["ok"]
        assert baja["fragmentos_eliminados"] == resumen.fragmentos
        assert baja["kb_version"] > version_alta, "la baja tiene que subir kb_version"

        # ─── 6. Olvidado, con las cachés que estaban calientes ───────────────
        final = retriever.solo_fragmentos(CONSULTA, top_k=5)
        assert not any(c.doc_id == resumen.doc_id for c in final.citas), \
            "una caché sin invalidar hace que el agente recuerde lo borrado: eso es fallar G5"

        verificacion = pipeline.verificar_olvido(resumen.doc_id, CONSULTA)
        assert verificacion["olvidado"]
        assert verificacion["fragmentos_en_chroma"] == 0
        assert verificacion["registros_en_sqlite"] == 0
        assert verificacion["fragmentos_recuperados_de_este_documento"] == 0

    def test_el_documento_subido_es_elegible_aunque_no_sea_de_ningun_procedimiento(
        self, entorno, documento
    ):
        """El boost por procedimiento no puede ser un filtro.

        Si lo fuera, un PDF del jurado —que no pertenece a ninguno de los cinco
        procedimientos del corpus— quedaría fuera de toda búsqueda y G5 fallaría en
        directo, sin que nada pareciera roto.
        """
        resumen = pipeline.indexar(documento, titulo="Fluxoplastia", origen="subido")
        pipeline.finalizar_cambio("alta", {"doc_id": resumen.doc_id})

        for procedimiento in ("Apendicectomía", "Colecistectomía", "Mastectomía", None):
            r = retriever.solo_fragmentos(CONSULTA, procedimiento=procedimiento, top_k=5)
            assert any(c.doc_id == resumen.doc_id for c in r.citas), \
                f"el documento subido desapareció al buscar como {procedimiento}"

    def test_subir_dos_veces_el_mismo_contenido_no_duplica(self, entorno, documento, tmp_path):
        primero = pipeline.indexar(documento, titulo="Fluxoplastia", origen="subido")
        pipeline.finalizar_cambio("alta", {"doc_id": primero.doc_id})

        # Mismo contenido, otro nombre: es el caso del corpus base, que trae el
        # mismo documento dos veces con distinta capitalización.
        copia = tmp_path / "otro-nombre.txt"
        copia.write_text(TEXTO, encoding="utf-8")
        segundo = pipeline.indexar(copia, titulo="Otro nombre", origen="subido")

        assert segundo.estado == "duplicado"
        assert segundo.doc_id == primero.doc_id
        assert store.contar_fragmentos(primero.doc_id) == primero.fragmentos

    def test_la_auditoria_registra_el_alta_y_la_baja(self, entorno, documento):
        from app.obs.logger import leer_jsonl

        resumen = pipeline.indexar(documento, titulo="Fluxoplastia", origen="subido")
        pipeline.finalizar_cambio("alta", {"doc_id": resumen.doc_id, "titulo": resumen.titulo})
        pipeline.dar_de_baja(resumen.doc_id)

        eventos = leer_jsonl(entorno.ruta_kb_audit)
        tipos = [e["evento"] for e in eventos]
        assert "alta" in tipos and "baja" in tipos
        assert all("kb_version" in e for e in eventos)


class TestIngestaDefensiva:
    """Ningún archivo raro del jurado puede tumbar el servidor (riesgo R7)."""

    def test_un_archivo_corrupto_devuelve_error_no_excepcion(self, entorno, tmp_path):
        falso = tmp_path / "roto.pdf"
        falso.write_bytes(b"esto no es un PDF ni de lejos")
        resumen = pipeline.indexar(falso, origen="subido")
        assert resumen.estado == "error"
        assert resumen.detalle

    def test_un_formato_no_soportado_se_rechaza_con_motivo(self, entorno, tmp_path):
        raro = tmp_path / "hoja.xlsx"
        raro.write_bytes(b"PK\x03\x04")
        resumen = pipeline.indexar(raro, origen="subido")
        assert resumen.estado == "error"
        assert "formato no soportado" in resumen.detalle

    def test_un_archivo_vacio_no_se_indexa(self, entorno, tmp_path):
        vacio = tmp_path / "vacio.txt"
        vacio.write_text("", encoding="utf-8")
        resumen = pipeline.indexar(vacio, origen="subido")
        assert resumen.estado in ("sin_texto", "error")

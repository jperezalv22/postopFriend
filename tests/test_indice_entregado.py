"""El índice que se entrega en el repo, no uno recién creado en un tmp_path.

Por qué existe. `test_kb_lifecycle.py` construye un índice limpio y verifica el
ciclo completo — y pasaba en verde mientras el índice versionado en `data/chroma/`
**no aceptaba ni un documento nuevo**. Un intento de compactar el SQLite de Chroma
(borrar `embeddings_queue` y hacer VACUUM) dejó `max_seq_id` por delante de la cola,
así que las inserciones posteriores se daban por aplicadas y se descartaban en
silencio. Leer funcionaba, escribir no.

El fallo era invisible desde arriba: la consola decía «disponible · 1 fragmento» y
la ingesta devolvía éxito. Con el jurado subiendo su PDF en directo, G5 se habría
caído sin que nada pareciera roto.

De ahí la regla que codifica este archivo: **el artefacto que se entrega también se
prueba.** Una prueba contra una copia pristina no dice nada del archivo que va a
correr el jurado.
"""

import uuid

import pytest

from app.config import get_settings
from app.rag import embedder, store
from app.store import db


@pytest.fixture(scope="module")
def indice():
    s = get_settings()
    if not (s.dir_chroma / "chroma.sqlite3").exists():
        pytest.skip("no hay índice construido; corra scripts/build_index.py")
    db.inicializar()
    return store.coleccion()


class TestIndiceEntregado:
    def test_tiene_el_corpus_completo(self, indice):
        assert indice.count() >= 9000, "el índice del repo está incompleto"

    def test_acepta_escrituras(self, indice):
        """La prueba que faltaba. Un índice de solo lectura falla G5 en directo."""
        marca = f"prueba-escritura-{uuid.uuid4().hex[:8]}"
        antes = indice.count()
        vector = embedder.embeber_pasajes(["fragmento de prueba de escritura del índice"])

        indice.add(
            ids=[f"{marca}::p1::0"],
            embeddings=vector,
            documents=["fragmento de prueba de escritura del índice"],
            metadatas=[{"doc_id": marca, "titulo": "prueba", "pagina": 1,
                        "chunk_idx": 0, "origen": "subido"}],
        )
        try:
            assert indice.count() == antes + 1, (
                "el índice entregado NO acepta documentos nuevos. "
                "La consola diría «disponible» y el jurado no vería el documento: G5 caída."
            )
            recuperado = indice.get(where={"doc_id": marca}, include=["metadatas"])
            assert recuperado["ids"], "se insertó pero no se puede consultar por metadata"
        finally:
            indice.delete(ids=[f"{marca}::p1::0"])

        assert indice.count() == antes, "el borrado tampoco surtió efecto"

    def test_el_borrado_por_metadata_funciona(self, indice):
        """`delete(where=...)` es literalmente el requisito de la baja en G5."""
        marca = f"prueba-borrado-{uuid.uuid4().hex[:8]}"
        antes = indice.count()
        vectores = embedder.embeber_pasajes(["uno", "dos"])
        indice.add(
            ids=[f"{marca}::p1::0", f"{marca}::p1::1"],
            embeddings=vectores,
            documents=["uno", "dos"],
            metadatas=[{"doc_id": marca, "pagina": 1, "chunk_idx": i} for i in range(2)],
        )
        assert store.contar_fragmentos(marca) == 2
        indice.delete(where={"doc_id": marca})
        assert store.contar_fragmentos(marca) == 0
        assert indice.count() == antes

    def test_el_indice_lexico_se_deriva_del_mismo_chroma(self, indice):
        idx = store.indice_lexico()
        assert len(idx.ids) == indice.count(), (
            "BM25 y Chroma discrepan: un documento borrado seguiría vivo en el léxico"
        )
        assert idx.frecuencia("mastectom") == 0, (
            "la carpeta breast_cancer del kit es de cáncer de cuello uterino; "
            "si esto cambia, hay que revisar el límite declarado en el README"
        )

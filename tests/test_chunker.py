"""El troceador decide qué se puede citar. Si trocea mal, el RAG cita mal."""

from app.rag.chunker import (
    MINIMO_CARACTERES,
    OBJETIVO_CARACTERES,
    cabecera,
    es_bibliografia,
    sin_cabecera,
    trocear_pagina,
)
from app.rag.ingest import Pagina, detectar_idioma, normalizar


def _pagina(texto: str, numero: int = 3) -> Pagina:
    return Pagina(numero=numero, texto=texto)


class TestNormalizacion:
    def test_reconstruye_el_parrafo_partido_por_renglones(self):
        # Lo que devuelve PyMuPDF en un artículo a dos columnas.
        crudo = "In\norder\nto\noptimize\nthe\ndiagnostic\nperformance"
        assert normalizar(crudo) == "In order to optimize the diagnostic performance"

    def test_conserva_la_frontera_de_parrafo(self):
        assert normalizar("Primer párrafo.\n\nSegundo párrafo.") == (
            "Primer párrafo.\n\nSegundo párrafo."
        )

    def test_une_la_palabra_partida_al_final_del_renglon(self):
        assert "apendicectomía" in normalizar("apendicec-\ntomía")

    def test_detecta_el_idioma(self):
        assert detectar_idioma("La secreción purulenta de la herida es un signo de infección") == "es"
        assert detectar_idioma("The purulent drainage from the wound is a sign of infection") == "en"


class TestBibliografia:
    def test_descarta_una_lista_de_referencias(self):
        referencia = (
            "Surg Endosc 2016;30:1705-12, http://dx.doi.org/10.1007/s00464-015-4453-x. "
            "[37] Talha A, El-Haddad H, et al. Laparoscopic versus open appendectomy. "
            "Ann Surg 2018;22:112-9. doi.org/10.1016/j.amsu.2018.01.001 [38] Smith J, et al."
        )
        assert es_bibliografia(referencia)

    def test_conserva_el_texto_clinico_aunque_traiga_cifras(self):
        clinico = (
            "La fiebre postoperatoria por encima de 38 grados centígrados en el día 7 "
            "sugiere infección del sitio quirúrgico y obliga a valoración médica."
        )
        assert not es_bibliografia(clinico)

    def test_conserva_una_referencia_suelta_dentro_de_texto_util(self):
        # Una sola señal no basta: si no, se perderían párrafos buenos.
        texto = (
            "Los signos de infección incluyen secreción purulenta, eritema y fiebre "
            "según la guía de práctica clínica publicada por el grupo [12]."
        )
        assert not es_bibliografia(texto)


class TestTroceado:
    def test_la_cabecera_lleva_procedimiento_titulo_y_pagina(self):
        c = cabecera("Apendicectomía", "Guía de práctica clínica", 12)
        assert c == "[Apendicectomía · Guía de práctica clínica · p. 12]"

    def test_sin_cabecera_recupera_el_texto_crudo(self):
        crudo = "La secreción purulenta indica infección."
        con = f"{cabecera('Apendicectomía', 'Guía', 4)}\n{crudo}"
        assert sin_cabecera(con) == crudo

    def test_sin_cabecera_no_toca_un_texto_sin_cabecera(self):
        assert sin_cabecera("Texto suelto sin cabecera") == "Texto suelto sin cabecera"

    def test_cada_fragmento_conserva_su_pagina(self):
        texto = " ".join(
            f"Esta es la oración número {i} sobre cuidados de la herida quirúrgica."
            for i in range(40)
        )
        fragmentos = trocear_pagina(_pagina(texto, 7), "doc1", "Guía", "Apendicectomía", 0)
        assert fragmentos
        assert all(f.pagina == 7 for f in fragmentos)
        assert all(f.doc_id == "doc1" for f in fragmentos)

    def test_descarta_los_fragmentos_demasiado_cortos(self):
        fragmentos = trocear_pagina(_pagina("Índice."), "doc1", "Guía", "Apendicectomía", 0)
        assert fragmentos == []

    def test_respeta_aproximadamente_el_tamano_objetivo(self):
        texto = " ".join(
            f"La recuperación tras la cirugía avanza de forma gradual en la semana {i}."
            for i in range(60)
        )
        fragmentos = trocear_pagina(_pagina(texto), "doc1", "Guía", "Apendicectomía", 0)
        for f in fragmentos:
            assert MINIMO_CARACTERES <= len(f.texto_crudo) <= OBJETIVO_CARACTERES * 1.7

    def test_los_identificadores_no_se_repiten(self):
        texto = " ".join(
            f"Oración {i} sobre el manejo del dolor postoperatorio en casa." for i in range(50)
        )
        fragmentos = trocear_pagina(_pagina(texto), "doc1", "Guía", "Apendicectomía", 0)
        assert len({f.chunk_id for f in fragmentos}) == len(fragmentos)


class TestLematizador:
    """El corpus y el paciente conjugan distinto. Sin lematizar, para BM25
    «cuidados de la herida» y «cómo se cuida la herida» no comparten nada."""

    def test_une_las_formas_de_la_misma_palabra(self):
        from app.rag.store import lematizar

        for familia in (
            ("cuida", "cuidado", "cuidados"),
            ("herida", "heridas"),
            ("punto", "puntos"),
            ("quitan", "quitar"),
            ("sacaron", "sacar"),
        ):
            raices = {lematizar(p) for p in familia}
            assert len(raices) == 1, f"{familia} produjo {raices}"

    def test_no_destruye_los_terminos_clinicos(self):
        from app.rag.store import lematizar

        # Recortar de más haría que «mastectomía» y «masticar» colisionaran.
        assert lematizar("mastectomia") != lematizar("masticar")
        assert len(lematizar("apendicectomia")) >= 10

    def test_tokenizar_quita_tildes_y_vacias(self):
        from app.rag.store import tokenizar

        tokens = tokenizar("La secreción purulenta de la herida")
        assert "de" not in tokens and "la" not in tokens
        assert all("ó" not in t and "í" not in t for t in tokens)

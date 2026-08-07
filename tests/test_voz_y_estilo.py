"""Segmentación de frases y reglas de estilo del habla.

Ambas cosas se verifican sobre el texto ya generado, no se le piden al prompt y se
confía. Un LLM al que se le pide «máximo dos frases» se pasa una de cada cinco
veces, y en una llamada de voz una parrafada de seis frases arruina el turno.
"""

from app.agent.generator import contiene_tranquilizador, recortar
from app.voice.segmenter import Segmentador
from app.voice.tts import limpiar_para_voz


class TestSegmentador:
    def test_suelta_la_primera_frase_en_cuanto_esta_completa(self):
        s = Segmentador()
        assert s.agregar("Buenos días, ¿hablo con Mauricio González? ") == [
            "Buenos días, ¿hablo con Mauricio González?"
        ]

    def test_no_corta_en_un_decimal(self):
        s = Segmentador()
        # «38.5» no termina una frase: cortar ahí haría decir «treinta y ocho punto».
        salida = s.agregar("La fiebre le llegó a 38.5 grados anoche según me cuenta. ")
        assert salida == ["La fiebre le llegó a 38.5 grados anoche según me cuenta."]

    def test_no_corta_en_una_abreviatura(self):
        s = Segmentador()
        s.agregar("Le habla la Dra. ")
        assert s.agregar("Ramírez del hospital, ¿cómo ha seguido usted hoy? ") == [
            "Le habla la Dra. Ramírez del hospital, ¿cómo ha seguido usted hoy?"
        ]

    def test_vaciar_devuelve_lo_que_quedo_sin_punto(self):
        s = Segmentador()
        s.agregar("Y cuénteme")
        assert s.vaciar() == "Y cuénteme"

    def test_corta_por_longitud_cuando_no_llega_el_punto(self):
        s = Segmentador()
        largo = "hablando y hablando sin parar nunca jamás de los jamases, " * 6
        assert s.agregar(largo), "una frase sin punto no puede bloquear el TTS"


class TestReglasDeEstilo:
    def test_trunca_a_dos_frases_pero_conserva_la_pregunta(self):
        # Quedarse con las dos primeras dejaría al agente sin pedir el dato que
        # le falta, y el protocolo se pararía en seco.
        texto, incidencias = recortar(
            "Entiendo que le molesta. Eso pasa a veces. Muchos lo sienten. ¿Se puso el termómetro?"
        )
        assert texto.endswith("¿Se puso el termómetro?")
        assert len(texto.split(".")) <= 3
        assert any("truncada" in i for i in incidencias)

    def test_elimina_las_muletillas_de_asistente(self):
        texto, incidencias = recortar(
            "Como modelo de lenguaje, debo señalar que el dolor es esperable. Cuénteme más."
        )
        assert "modelo de lenguaje" not in texto.lower()
        assert "muletilla_de_asistente_eliminada" in incidencias

    def test_deja_intacto_un_turno_que_ya_cumple(self):
        bueno = "Entonces el dolor va en seis, ¿así es?"
        texto, incidencias = recortar(bueno)
        assert texto == bueno
        assert incidencias == []

    def test_quita_la_segunda_pregunta_del_turno(self):
        # Dos preguntas juntas confunden al paciente y arruinan la extracción.
        texto, incidencias = recortar("¿Cómo va el dolor? ¿Y ha tenido fiebre?")
        assert texto.count("?") == 1
        assert "segunda_pregunta_eliminada" in incidencias

    def test_detecta_frases_que_tranquilizan(self):
        assert contiene_tranquilizador("No se preocupe, eso es normal") is not None
        assert contiene_tranquilizador("Eso necesita que lo vea un médico hoy") is None


class TestLimpiezaParaVoz:
    def test_quita_los_marcadores_de_cita(self):
        # El paciente no debe oír «corchete efe uno»; la cita queda en pantalla.
        assert limpiar_para_voz("La herida puede supurar [F1]. Consulte [F2].") == (
            "La herida puede supurar. Consulte."
        )

    def test_quita_el_markdown(self):
        assert limpiar_para_voz("Esto es **importante** y `esto` también") == (
            "Esto es importante y esto también"
        )

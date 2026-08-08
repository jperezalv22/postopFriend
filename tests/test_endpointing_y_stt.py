"""El micrófono del paciente entra, y lo que entra es lo que dijo.

Estas pruebas salen de un fallo real reportado en llamada: a los dos o tres turnos
el paciente hablaba y su intervención no aparecía. Por texto sí funcionaba, así que
el servidor estaba bien; el audio nunca salía del navegador.

Eran dos cosas encadenadas y ninguna daba error en ningún lado:

  1. `audio.js` ajustaba el VAD con `minSpeechFrames` / `redemptionFrames` /
     `preSpeechPadFrames`. El bundle vendorizado deriva los tres de las opciones en
     milisegundos y no mira las de frames. Entraban al objeto de opciones, pasaban
     `validateOptions` —que solo valida las `*Ms`— y se ignoraban. Quedaban los
     valores por omisión: la puerta real para que un enunciado contara eran 384 ms
     de voz sobre el umbral, no los 128 ms que decía el comentario. Una respuesta
     de una palabra («sí», «nueve») se descartaba como `VAD misfire`.

  2. Con el audio así de silencioso, Whisper dejaba de transcribir y se ponía a
     continuar su propio prompt de sesgo. «Carlos Llamada de seguimiento
     postoperatorio en C.» entró al diálogo como turno del paciente.

Ninguno de los dos se ve en la consola ni en las pruebas: se ven en la
conversación, que es el sitio más caro donde encontrarlos.
"""

from __future__ import annotations

import re

from app.api import ws_call
from app.config import get_settings
from app.voice import stt

RAIZ = get_settings().dir_raiz
AUDIO_JS = (RAIZ / "app" / "static" / "js" / "audio.js").read_text(encoding="utf-8")
CALL_JS = (RAIZ / "app" / "static" / "js" / "call.js").read_text(encoding="utf-8")
BUNDLE = (RAIZ / "app" / "static" / "vendor" / "vad.bundle.min.js").read_text(encoding="utf-8")

#: Cómo se llama cada opción en el bundle: la que se aplica y la que se ignora.
EQUIVALENCIAS = [("redemptionFrames", "redemptionMs"),
                 ("preSpeechPadFrames", "preSpeechPadMs"),
                 ("minSpeechFrames", "minSpeechMs")]

#: Solo el objeto que se le pasa al VAD. El resto del archivo nombra las opciones
#: en frames a propósito: `_verificarAjustes()` compara lo pedido con lo aplicado.
AJUSTES = re.search(r"const AJUSTES = \{(.*?)\n\};", AUDIO_JS, re.S).group(1)


# ─── El ajuste del VAD llega de verdad al procesador de marcos ────────────────

def test_el_bundle_sigue_derivando_los_marcos_de_los_milisegundos():
    """Si se re-vendoriza una versión con otro contrato, que se sepa aquí.

    Es la premisa de la que cuelga `AJUSTES` en audio.js. Un bundle nuevo que
    volviera a leer las `*Frames` dejaría el ajuste actual a medias, y otra vez sin
    error visible.
    """
    for frames, ms in EQUIVALENCIAS:
        patron = rf"{frames}:\s*Math\.floor\(\w+\.{ms}\s*/\s*\w+\)"
        assert re.search(patron, BUNDLE), (
            f"el bundle ya no calcula {frames} a partir de {ms}: "
            f"revise el ajuste del VAD en app/static/js/audio.js"
        )


def test_audio_js_no_ajusta_el_vad_con_opciones_que_el_bundle_ignora():
    for frames, _ in EQUIVALENCIAS:
        assert f"{frames}:" not in AJUSTES, (
            f"`{frames}` se acepta sin error y no lo lee nadie. "
            f"Use la opción en milisegundos."
        )


def test_audio_js_ajusta_el_vad_con_las_opciones_que_el_bundle_lee():
    for _, ms in EQUIVALENCIAS:
        assert f"{ms}:" in AJUSTES, f"falta `{ms}` en el ajuste del VAD"


def test_el_umbral_de_voz_deja_pasar_al_paciente_agachado_por_el_eco():
    """0.55 era demasiado alto y fue la mitad del fallo.

    El agente suena por el altavoz y el cancelador de eco de Chrome agacha la voz
    del paciente mientras tanto. Con el umbral arriba, una respuesta corta no
    volvía a levantar la probabilidad y no sumaba ni un marco de habla.
    """
    umbral = float(re.search(r"positiveSpeechThreshold:\s*([\d.]+)", AJUSTES).group(1))
    negativo = float(re.search(r"negativeSpeechThreshold:\s*([\d.]+)", AJUSTES).group(1))

    assert 0.25 <= umbral <= 0.45, f"positiveSpeechThreshold {umbral} fuera de rango útil"
    assert negativo < umbral, "el bundle exige negativeSpeechThreshold < positiveSpeechThreshold"


def test_una_respuesta_de_una_palabra_cuenta_como_turno():
    """«Sí», «no» y «nueve» son las respuestas que pide el protocolo.

    El modelo v5 procesa marcos de 512 muestras a 16 kHz = 32 ms. Con el valor por
    omisión del bundle (400 ms) hacían falta 12 marcos seguidos de voz por encima
    del umbral, y una respuesta de una palabra no llega. Ese era el fallo.
    """
    ms_por_marco = 32
    minimo = int(re.search(r"minSpeechMs:\s*(\d+)", AUDIO_JS).group(1))
    marcos = minimo // ms_por_marco

    assert marcos <= 6, (
        f"minSpeechMs {minimo} = {marcos} marcos: una respuesta de una palabra se "
        f"descarta como misfire"
    )
    assert marcos >= 3, (
        f"minSpeechMs {minimo} = {marcos} marcos: con menos, una tos abre turno"
    )


def test_el_misfire_no_se_traga_en_silencio():
    """Un enunciado descartado tiene que avisar a alguien.

    `alEmpezarHabla` ya paró el reloj del silencio, y tras un misfire no llega
    ningún «escuchando» del servidor que lo rearme. Sin manejarlo, la llamada se
    queda muda: ni turno del paciente, ni «¿Sigue ahí?».
    """
    assert "onVADMisfire" in AUDIO_JS, "el VAD descarta enunciados sin avisar"
    assert "alDescartarHabla" in CALL_JS, "la interfaz no reacciona a un enunciado descartado"


# ─── El eco del propio agente no abre turno ───────────────────────────────────

def test_el_enunciado_se_filtra_por_confianza_y_no_solo_por_duracion():
    """Seis marcos de eco a 0.45 duran lo mismo que seis marcos de voz a 0.95.

    Con el umbral en 0.40, el eco del agente saliendo por el altavoz pasa
    `minSpeechMs` sin despeinarse: dispara el VAD, se manda un segundo de eco,
    Whisper no encuentra habla y devuelve su prompt. La duración no distingue las
    dos cosas; el pico de probabilidad de Silero sí.
    """
    pico = float(re.search(r"const PICO_MINIMO = ([\d.]+)", AUDIO_JS).group(1))
    media = float(re.search(r"const MEDIA_MINIMA = ([\d.]+)", AUDIO_JS).group(1))
    umbral = float(re.search(r"positiveSpeechThreshold:\s*([\d.]+)", AJUSTES).group(1))

    assert pico > umbral, "un corte por debajo del umbral de disparo no filtra nada"
    assert pico <= 0.9, f"pico mínimo {pico}: se descartaría habla real"
    assert umbral < media < pico, "la media debe caer entre el umbral y el pico"
    assert "onFrameProcessed" in AUDIO_JS, "sin las probabilidades no hay nada que filtrar"


def test_el_filtro_de_confianza_solo_corre_si_puede_haber_eco():
    """Con el agente callado, el corte solo puede tirar voz del paciente.

    El filtro existe contra el eco del altavoz, y el eco solo existe mientras hay
    audio del agente sonando. Aplicarlo fuera de esa ventana no protege de nada:
    en una tanda de 9 enunciados medidos, los 9 ocurrieron con el agente en
    silencio y el corte mató uno que decía «Marcó un 36».
    """
    assert "agenteSonando" in AUDIO_JS, "la captura no sabe si el altavoz está sonando"
    assert "puedeHaberEco" in AUDIO_JS, "el filtro de confianza corre siempre"

    # Las dos comparaciones tienen que ir condicionadas, no sueltas.
    for comparacion in ("medida.pico < PICO_MINIMO", "medida.media < MEDIA_MINIMA"):
        linea = next((l for l in AUDIO_JS.splitlines() if comparacion in l), None)
        assert linea is not None, f"ya no existe la comparación `{comparacion}`"
        assert "puedeHaberEco" in linea, (
            f"`{comparacion}` se aplica también con el agente callado"
        )

    assert "agenteSonando: agenteAudible" in CALL_JS, "la interfaz no le pasa la señal"
    assert "reproductor?.sonando" in CALL_JS, "`agenteAudible` no mira el reproductor"


def test_una_respuesta_corta_real_sobrevive_al_corte():
    """Los números exactos del enunciado que se perdió en una llamada medida.

    «Marcó un 36» —la temperatura del paciente, una variable del protocolo— salió
    con pico 0.68 y media 0.51, y el corte de entonces (0.80 / 0.55) lo descartó
    antes de que el audio saliera del navegador: sin transcripción, sin turno y sin
    fila en la base. El mismo audio, transcrito aparte, se entendía perfectamente.

    Las respuestas cortas son las que pide el protocolo, así que el corte no puede
    estar por encima de lo que da una respuesta corta legítima.
    """
    pico = float(re.search(r"const PICO_MINIMO = ([\d.]+)", AUDIO_JS).group(1))
    media = float(re.search(r"const MEDIA_MINIMA = ([\d.]+)", AUDIO_JS).group(1))

    assert pico <= 0.68, f"pico mínimo {pico}: «Marcó un 36» (0.68) se vuelve a perder"
    assert media <= 0.51, f"media mínima {media}: «Marcó un 36» (0.51) se vuelve a perder"


def test_el_barge_in_cuelga_del_habla_confirmada():
    """Cortar al agente con el primer marco por encima del umbral lo cortaba el eco.

    `onSpeechStart` dispara con un solo marco; `onSpeechRealStart` exige
    `minSpeechMs` de voz sostenida. Cortar es destructivo —se pierde lo que
    faltaba por decir— así que va contra la señal cara, no contra la barata.
    """
    assert "onSpeechRealStart" in AUDIO_JS, "el barge-in sigue colgando del primer marco"
    assert "alConfirmarHabla" in CALL_JS

    corte = CALL_JS.index("alConfirmarHabla")
    arranque = CALL_JS.index("alEmpezarHabla:")
    assert "barge_in" not in CALL_JS[arranque:corte], (
        "el barge-in sigue en `alEmpezarHabla`, que dispara con un marco suelto"
    )


def test_la_duracion_que_se_reporta_es_la_del_habla():
    """El WAV lleva pegados el margen de entrada y la redención.

    Contarlos inflaba `audio_paciente_s` en algo más de un segundo por turno, y ese
    campo va a la base y al acta.
    """
    assert "muestras.length / 16000" not in AUDIO_JS, (
        "se sigue reportando el recorte completo como duración del habla"
    )
    assert "medida.segundos" in AUDIO_JS


def test_el_agente_se_calla_antes_de_realimentarse():
    """Pedir repetición es lo que alimenta el bucle: el guion suena por el altavoz.

    En una llamada real se encadenaron tres «no le escuché bien» en menos de tres
    segundos, cada uno disparado por el eco del anterior.
    """
    ws = (RAIZ / "app" / "api" / "ws_call.py").read_text(encoding="utf-8")
    assert ws_call.REPETICIONES_ANTES_DE_CALLAR <= 2, (
        "con más de dos repeticiones seguidas el bucle ya se sostiene solo"
    )
    assert "capturas_degradadas" in ws, "no se cuentan las capturas degradadas seguidas"
    # Callarse no puede dejar la llamada colgada: el cliente rearma su reloj del
    # silencio al recibir «escuchando», y sin eso la escalada no vuelve a correr.
    corte = ws.index("REPETICIONES_ANTES_DE_CALLAR", ws.index("_manejar_audio(sesion: Sesion"))
    assert 'valor="escuchando"' in ws[corte:corte + 700], (
        "el agente se calla sin devolver la llamada a «escuchando»"
    )


# ─── El agente no se interrumpe a sí mismo ────────────────────────────────────

def test_el_reloj_del_silencio_espera_a_que_el_agente_se_calle():
    """El agente se cortaba a media palabra para preguntar «¿Sigue ahí?».

    `escuchando` lo manda el servidor cuando termina de *enviar* el MP3, no cuando
    el paciente termina de *oírlo*: el navegador todavía tiene el audio entero en
    la cola del MediaSource. Con la apertura —unas 45 palabras, ~17 s de voz— los
    6 s del primer tramo vencían a mitad de la frase, el cliente mandaba
    `silencio`, llegaba «¿Sigue ahí?» como turno nuevo y `iniciarTurno()` llamaba a
    `detener()`. En una llamada real ocurrió cuatro veces, y de paso encadenó el
    guion de escalamiento repetido.
    """
    assert "agenteTodaviaHablando" in CALL_JS, "nadie comprueba si el altavoz sigue sonando"

    # El rearme tiene que estar condicionado dentro de `armarSilencio`, no en el
    # sitio que lo llama: se llama desde `escuchando`, desde `ended` y desde el
    # descarte del VAD, y los tres tienen que respetar la misma regla.
    cuerpo = CALL_JS[CALL_JS.index("function armarSilencio()"):]
    cuerpo = cuerpo[: cuerpo.index("\n}")]
    assert "agenteTodaviaHablando()" in cuerpo, (
        "`armarSilencio` cuenta aunque el agente siga hablando"
    )
    assert "TRAMOS_DE_SILENCIO" in cuerpo, "el rearme ya no arma los tramos"


def test_el_reloj_del_silencio_arranca_cuando_el_audio_termina():
    """El instante bueno es `ended` del <audio>, que es cuando el paciente deja de oír."""
    inicio = CALL_JS.index("alTerminar:")
    bloque = CALL_JS[inicio: inicio + 400]
    assert "armarSilencio()" in bloque, (
        "al terminar el audio del agente no se rearma el reloj: la llamada se queda muda"
    )
    assert "audioEnCurso = false" in bloque


def test_un_turno_cortado_no_deja_el_reloj_parado_para_siempre():
    """`detener()` no emite `ended`, así que hay que cerrar el turno a mano.

    Pasa en el barge-in y cuando el MediaSource no se deja cerrar. Sin esto
    `audioEnCurso` se queda en true, `armarSilencio` no llega a contar nunca y la
    llamada se queda sin escalada de silencio: muda hasta que alguien cuelgue.
    """
    corte = CALL_JS.index("reproductor.detener()")
    assert "audioEnCurso = false" in CALL_JS[corte: corte + 400], (
        "el barge-in corta el audio y deja el turno abierto para siempre"
    )
    # Y la red de seguridad para los cierres que no avisan de ninguna manera.
    assert "GRACIA_DE_ARRANQUE_MS" in CALL_JS, (
        "sin gracia, un MediaSource colgado deja el reloj parado para siempre"
    )


# ─── Whisper devuelve lo dicho, no su propio prompt ───────────────────────────

def test_el_prompt_de_sesgo_no_lleva_prosa_que_whisper_pueda_continuar():
    """La causa raíz, no el síntoma.

    Mientras el prompt abría con «Llamada de seguimiento postoperatorio en
    Colombia. Términos: … Habla coloquial: …», Whisper continuaba esa prosa en vez
    de transcribir: 3 de 7 turnos aceptados salieron contaminados en una tanda
    medida. Una lista separada por comas sesga igual el vocabulario y no ofrece
    ninguna frase que seguir.

    Se comprueba la forma y no el contenido a propósito: el glosario va a crecer y
    a cambiar, y lo que no puede volver es la prosa.
    """
    prompt = stt.PROMPT_SESGO
    assert ":" not in prompt, "los dos puntos abren una enumeración que Whisper continúa"
    assert "." not in prompt, "un punto convierte la lista en frases"
    assert prompt[0].islower(), "empezar en mayúscula lo hace parecer el inicio de un texto"
    assert len(prompt.split()) < 60, f"{len(prompt.split())} palabras: cuanto más largo, más sangra"


def test_se_rechaza_el_prompt_de_sesgo_devuelto_como_habla():
    """Lo que apareció de verdad en llamadas reales, entrando como turno del paciente.

    Los dos primeros son contaminación *parcial* —dos palabras del prompt pegadas
    al principio de lo que sí se dijo— y son los que la regla de corrida no
    alcanzaba: «Llamada de marco 36» son 2 palabras del prompt sobre 4, ni corrida
    larga ni dominio del texto. El paciente había dicho «marcó un 36».
    """
    assert stt._es_eco_del_prompt("Llamada de marco 36.")
    assert stt._es_eco_del_prompt("Términos de secundaria, si me sale sangre.")
    assert stt._es_eco_del_prompt("Carlos Llamada de seguimiento postoperatorio en C.")
    assert stt._es_eco_del_prompt(
        "Términos: apendicectomía, colecistectomía, colectomía, mastectomía."
    )
    assert stt._es_eco_del_prompt("Habla coloquial: maluco, guayabo, chuzón, punzada.")


def test_no_se_rechaza_lo_que_el_paciente_sí_puede_decir():
    """El glosario está hecho de lo que dice el paciente: no puede volverse un veto.

    «No he podido obrar» son cuatro palabras seguidas del prompt y es la respuesta
    a la pregunta de tránsito intestinal. Rechazarla sería perder una variable
    clínica por defenderse de una alucinación.

    Sale de aquí «Términos, mi dolor va en un nueve», que estaba en esta lista
    cuando la única defensa era la corrida de palabras. Ningún paciente abre un
    turno con «Términos»: esa frase no es habla legítima que hubiera que proteger,
    es la contaminación del prompt con lo dicho pegado detrás, que es exactamente
    la forma en que «Términos de secundaria, si me sale sangre» entró al diálogo en
    una llamada medida. Protegerla costaba escribir síntomas falsos en la historia;
    rechazarla cuesta una repetición.
    """
    legitimas = [
        "No he podido obrar.",
        "Me sale materia por la herida.",
        "Tengo fiebre y escalofríos.",
        "Fiebre, escalofríos, náuseas.",
        "Fiebre, escalofríos, náuseas, todo eso tengo.",
        "Se me abrió un poquito la herida.",
        "Me duele harto, como un chuzón.",
        "Un nueve.",
        "Sí.",
    ]
    for frase in legitimas:
        assert not stt._es_eco_del_prompt(frase), f"se rechazó habla legítima: {frase!r}"


def test_el_eco_del_prompt_sale_como_transcripcion_vacia_con_motivo():
    """Vacía y con motivo: el WebSocket pide repetición en vez de inventar el turno."""
    t = stt.Transcripcion("", 1.0, 0.0, vacia=True, motivo="x")
    assert t.vacia and t.motivo, "el contrato de Transcripcion cambió"
    assert stt._es_eco_del_prompt("Llamada de seguimiento postoperatorio en Colombia.")

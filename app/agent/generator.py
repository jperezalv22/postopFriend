"""Generación de la respuesta hablada del agente.

Estado: **primera versión**. Hoy conduce el protocolo con el LLM y las reglas de
estilo del §9 verificadas después de generar. Mañana `flow.py` decidirá qué toca
preguntar y este módulo solo redactará; la firma no cambia.

Las reglas de estilo no son sugerencias del prompt: se comprueban sobre el texto
generado y se corrigen. Un LLM al que se le pide «máximo dos frases» se pasa una
de cada cinco veces, y en voz una parrafada de seis frases arruina la llamada.
"""

from __future__ import annotations

import logging
import re

from app.agent import llm
from app.agent.scripts_es_co import (
    ANCLAS,
    FRASES_PROHIBIDAS_SI_NO_VERDE,
    GLOSARIO,
    NOMBRE_AGENTE,
)
from app.obs.trace import TurnTrace
from app.store.patients import Ficha

log = logging.getLogger("postopfriend.agente")

MAX_FRASES = 2
MAX_PALABRAS = 35

VARIABLES = ("dolor", "fiebre", "herida", "movilidad", "apetito", "sueno")

_FIN_ORACION = re.compile(r"(?<=[.!?])\s+")
# Muletillas de asistente que delatan la máquina y no aportan nada en voz.
_MULETILLAS = re.compile(
    r"^(como (modelo de lenguaje|asistente|ia)[^.]*\.\s*"
    r"|es importante (señalar|recordar|destacar) que\s*"
    r"|claro( que sí)?[,.]\s*"
    r"|por supuesto[,.]\s*"
    r"|entiendo (perfectamente|completamente)[,.]\s*)",
    re.IGNORECASE,
)


def _glosario_para_prompt() -> str:
    return "; ".join(f"«{jerga}» = {clinico}" for jerga, clinico in GLOSARIO.items())


def sistema(ficha: Ficha) -> str:
    return f"""Usted es {NOMBRE_AGENTE}, del programa de seguimiento postoperatorio de un hospital colombiano. Está hablando POR TELÉFONO con un paciente real. Todo lo que escriba se va a convertir en voz.

FICHA DEL PACIENTE (lo único que sabe antes de llamar):
{ficha.resumen_para_prompt()}

SU MISIÓN: averiguar conversando cómo va la recuperación en seis puntos, en este orden:
dolor (0 a 10) → fiebre (¿se tomó la temperatura? ¿cuánto?) → herida (¿enrojecida? ¿le sale líquido?) → movilidad → apetito → sueño.

CÓMO HABLA:
- Máximo DOS frases y {MAX_PALABRAS} palabras por turno. Es una llamada, no un informe.
- UNA sola pregunta por turno. Dos preguntas juntas confunden y arruinan la respuesta.
- Usted, cálido, sin diminutivos infantilizantes. Nada de tecnicismos: diga «herida», no «sitio quirúrgico»; «pus», no «exudado purulento».
- Nunca enumere ni haga listas habladas. Si hay que dar varias indicaciones, una por turno, confirmando.
- Confirme lo que entendió antes de avanzar: «Entonces el dolor va en seis, ¿así es?».
- Nada de «Como modelo de lenguaje», «Es importante señalar», «Por supuesto».

SI EL PACIENTE MINIMIZA («estoy bien», «normal», «ahí vamos»): «bien» no es un dato.
Pregunte por un hecho comprobable: {" | ".join(ANCLAS.values())}

LO QUE NO HACE NUNCA:
- No diagnostica, no receta y no dice dosis ni nombres de medicamentos.
- No tranquiliza sobre un síntoma que no ha evaluado. Nada de «no se preocupe» ni «eso es normal».
- No se sale del tema. Si le preguntan de otra cosa, lo dice en una frase y vuelve.
- No se inventa lo que no sabe. Si no lo tiene en sus guías, lo dice.

JERGA COLOMBIANA que va a oír: {_glosario_para_prompt()}"""


def _historial(dialogo: list[dict[str, str]], maximo: int = 24) -> list[dict[str, str]]:
    """El diálogo acumulado, no solo el último turno.

    Un «ayer me sentí afiebrada, como treinta y ocho» del turno 3 no puede
    perderse en el turno 9: es una variable clínica que ya se preguntó.
    """
    mensajes = []
    for t in dialogo[-maximo:]:
        if not t["texto"]:
            continue
        papel = "assistant" if t["hablante"] == "agente" else "user"
        mensajes.append({"role": papel, "content": t["texto"]})
    return mensajes


def recortar(texto: str) -> tuple[str, list[str]]:
    """Aplica las reglas de estilo al texto ya generado. Devuelve qué hubo que corregir."""
    incidencias: list[str] = []
    limpio = _MULETILLAS.sub("", texto).strip()
    if limpio != texto.strip():
        incidencias.append("muletilla_de_asistente_eliminada")
        limpio = limpio[:1].upper() + limpio[1:]  # la muletilla se llevaba la mayúscula

    frases = [f.strip() for f in _FIN_ORACION.split(limpio) if f.strip()]
    if len(frases) > MAX_FRASES:
        # Truncar por delante se comería la pregunta y dejaría el protocolo parado:
        # el agente diría algo amable y no pediría el dato que le falta. Se conserva
        # la primera frase, que da contexto, y la última pregunta, que es la que
        # hace avanzar la conversación.
        preguntas = [f for f in frases if f.rstrip().endswith("?")]
        if preguntas and preguntas[-1] not in frases[:MAX_FRASES]:
            limpio = " ".join([frases[0], preguntas[-1]])
        else:
            limpio = " ".join(frases[:MAX_FRASES])
        incidencias.append(f"respuesta_truncada_a_{MAX_FRASES}_frases")

    palabras = limpio.split()
    if len(palabras) > MAX_PALABRAS + 10:
        # Se corta en la frontera de la primera oración, no a mitad de palabra.
        limpio = frases[0] if frases else " ".join(palabras[:MAX_PALABRAS])
        incidencias.append("respuesta_demasiado_larga")

    # Dos signos de interrogación de cierre = dos preguntas en un turno.
    if limpio.count("?") > 1:
        corte = limpio.find("?", limpio.find("?") + 1)
        limpio = limpio[:corte].rstrip() if corte > 0 else limpio
        incidencias.append("segunda_pregunta_eliminada")

    return limpio.strip(), incidencias


def contiene_tranquilizador(texto: str) -> str | None:
    plano = texto.lower()
    return next((f for f in FRASES_PROHIBIDAS_SI_NO_VERDE if f in plano), None)


async def responder(ficha: Ficha, dialogo: list[dict[str, str]], traza: TurnTrace) -> str:
    """Redacta el siguiente turno del agente."""
    mensajes = [{"role": "system", "content": sistema(ficha)}, *_historial(dialogo)]

    traza.iniciar("llm")
    respuesta = await llm.chat(mensajes, max_tokens=120, temperatura=0.4)
    traza.terminar("llm")
    traza.tokens_in += respuesta.tokens_in
    traza.tokens_out += respuesta.tokens_out
    traza.llm_calls += 1
    traza.modelo = respuesta.modelo
    for inc in respuesta.incidencias:
        traza.incidencia(inc)

    if not respuesta.texto:
        traza.incidencia("respuesta_vacia_del_llm")
        return "Perdón, se me fue la señal un segundo. ¿Me repite lo último?"

    texto, incidencias = recortar(respuesta.texto)
    for inc in incidencias:
        traza.incidencia(inc)
    return texto

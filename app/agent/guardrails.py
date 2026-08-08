"""Verificación posterior a la generación. Deterministas, no instrucciones de prompt.

La rúbrica nombra tres penalizaciones explícitas: alucinar dosis o medicamentos,
tranquilizar ante una bandera roja, y caer en una inyección de prompt. Las tres
tienen aquí un mecanismo que **se ejecuta sobre el texto ya generado**.

La diferencia importa. Un prompt que dice «no menciones dosis» funciona casi
siempre, y «casi siempre» en salud es una forma cara de decir «a veces no». Un
regex que busca `\\d+ mg` en la respuesta y la sustituye por un guion seguro no
tiene días malos.

Los tres vectores de inyección se defienden igual: lo que dice el paciente por voz,
y **lo que trae dentro un PDF que el jurado sube a la consola**. El segundo es el
que suele olvidarse.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.agent.scripts_es_co import (
    FRASES_PROHIBIDAS_SI_NO_VERDE,
    FUERA_DE_MISION,
    INYECCION_DETECTADA,
    SIN_DOSIS,
)

log = logging.getLogger("postopfriend.agente")


@dataclass
class Veredicto:
    """Resultado de pasar una respuesta por los guardrails."""

    texto: str
    bloqueada: bool = False
    motivos: list[str] = field(default_factory=list)
    sustituida_por: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "bloqueada": self.bloqueada,
            "motivos": self.motivos,
            "sustituida_por": self.sustituida_por,
        }


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes)


def _sin_tildes(texto: str) -> str:
    """Quita tildes CONSERVANDO las mayúsculas.

    Hace falta para los patrones de inyección: «muéstrame» tiene que casar con
    `muestrame`, pero `\\bDAN\\b` no puede volverse `dan`, que en español es un
    verbo corriente («me dan ganas de vomitar») y dispararía en falso.
    """
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


# ─── 1. Dosis y medicamentos ─────────────────────────────────────────────────

# Fármacos que aparecen de verdad en un postoperatorio colombiano. La lista no
# pretende ser exhaustiva: los patrones de dosificación cubren el resto.
FARMACOS = (
    "acetaminofen", "acetaminofén", "paracetamol", "ibuprofeno", "diclofenaco",
    "naproxeno", "dipirona", "metamizol", "tramadol", "codeina", "codeína",
    "morfina", "hidromorfona", "oxicodona", "cefalexina", "cefazolina",
    "ciprofloxacina", "amoxicilina", "ampicilina", "clavulanico", "clavulánico",
    "metronidazol", "clindamicina", "enoxaparina", "heparina", "warfarina",
    "rivaroxaban", "omeprazol", "ondansetron", "ondansetrón", "metoclopramida",
)

PATRONES_DOSIS = (
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|ml|mcg|µg|g|ui|u\.i\.)\b", re.IGNORECASE),
    re.compile(r"\bcada\s+(?:\d+|una?|dos|tres|cuatro|seis|ocho|doce)\s*(?:horas?|h\b|dias?|días?)",
               re.IGNORECASE),
    # La frecuencia se dice tanto con cifra como con letra: «3 veces al día» y
    # «tres veces al día» son la misma indicación farmacológica.
    re.compile(r"\b(?:\d+|una?|dos|tres|cuatro|cinco|seis)\s*(?:veces|tomas?)\s+(?:al|por)\s+d[ií]a",
               re.IGNORECASE),
    re.compile(r"\b(?:una|dos|tres|cuatro|media)\s+(?:tableta|pastilla|c[aá]psula|ampolla)s?\b",
               re.IGNORECASE),
)


def afirmaciones_de_dosis(texto: str) -> list[str]:
    """Fragmentos de la respuesta que constituyen una indicación farmacológica."""
    hallazgos: list[str] = []
    for patron in PATRONES_DOSIS:
        hallazgos += [m.group().strip() for m in patron.finditer(texto)]
    plano = _plano(texto)
    hallazgos += [f for f in FARMACOS if _plano(f) in plano]
    return sorted(set(hallazgos))


def verificar_afirmaciones_clinicas(respuesta: str, fragmentos: list[str]) -> Veredicto:
    """Una dosis o un fármaco solo pueden salir si están literalmente en las fuentes.

    Y aun así, el agente no receta: si aparece algo farmacológico, la respuesta se
    sustituye por el guion que remite al médico. El corpus puede decir que se usa
    metronidazol; eso no autoriza a un agente telefónico a indicárselo a un paciente.
    """
    hallazgos = afirmaciones_de_dosis(respuesta)
    if not hallazgos:
        return Veredicto(texto=respuesta)

    fuentes = _plano(" ".join(fragmentos))
    sin_respaldo = [h for h in hallazgos if _plano(h) not in fuentes]

    motivo = (
        f"dosis_o_farmaco_sin_respaldo:{','.join(sin_respaldo)}"
        if sin_respaldo
        else f"dosis_o_farmaco_en_fuentes:{','.join(hallazgos)}"
    )
    log.warning("guardrail de dosis: %s", motivo)
    return Veredicto(
        texto=SIN_DOSIS, bloqueada=True, motivos=[motivo], sustituida_por="SIN_DOSIS"
    )


# ─── 2. Tranquilizar cuando no toca ──────────────────────────────────────────

def frase_tranquilizadora(texto: str) -> str | None:
    plano = _plano(texto)
    return next((f for f in FRASES_PROHIBIDAS_SI_NO_VERDE if _plano(f) in plano), None)


def verificar_no_tranquiliza(respuesta: str, nivel: str) -> Veredicto:
    """Con el nivel en amarillo o rojo, «no se preocupe» es una penalización directa.

    No es una cuestión de tono: minimizar un síntoma que el motor acaba de marcar
    como preocupante es lo que hace que un paciente no vaya a urgencias.
    """
    if nivel in ("verde", ""):
        return Veredicto(texto=respuesta)
    frase = frase_tranquilizadora(respuesta)
    if frase is None:
        return Veredicto(texto=respuesta)
    log.warning("guardrail: se intentó tranquilizar con nivel %s («%s»)", nivel, frase)
    return Veredicto(
        texto=respuesta, bloqueada=True, motivos=[f"tranquiliza_en_nivel_{nivel}:{frase}"]
    )


# ─── 3. Inyección de prompt ──────────────────────────────────────────────────

# Una inyección que reasigna el papel del agente tiene que nombrar el papel. Sin
# esa exigencia, «eres» a secas bloqueaba «Eres muy amable, gracias» y «actúa como»
# bloqueaba «mi hija actúa como si yo no pudiera hacer nada sola»: dos frases
# perfectamente normales que el filtro sustituía por el guion de inyección, cortando
# la llamada en seco. Lo detectó `evals/run_safety_eval.py` (caso leg_03).
#
# La lista deja fuera «enfermera» a propósito: es el papel que el agente ya tiene,
# así que asignárselo no le da nada que no tuviera. Lo que se persigue es el intento
# de convertirlo en otra cosa —un médico que receta, un asistente sin filtros—, y eso
# siempre nombra lo que quiere que sea.
_ROL_SUPLANTADO = (
    r"(?:medic[oa]|doctor[ae]?|cirujan[oa]|farmaceut|asistente|ia\b|chat\s?gpt|gpt|"
    r"modelo|desarrollador[ae]?|experto|sistema|bot\b|hacker|abogad[oa]|"
    r"human[oa]|dios)"
)

PATRONES_INYECCION = (
    re.compile(r"ignor[ae][a-z]*\s+(?:todas?\s+)?(?:tus|las|sus)\s+instruc", re.IGNORECASE),
    re.compile(r"olvid[ae][a-z]*\s+(?:todo\s+)?lo\s+anterior", re.IGNORECASE),
    re.compile(rf"\b(?:ahora\s+)?eres\s+(?:un[ao]?|el|la|mi)?\s*{_ROL_SUPLANTADO}",
               re.IGNORECASE),
    # «actúa como si fueras mi cirujano» es la misma asignación de rol con un rodeo.
    # El «si fueras» es opcional: lo que decide sigue siendo que nombre el rol, así
    # que «actúa como si yo no pudiera hacer nada» no cae.
    re.compile(
        rf"\bactu[ae]\s+como\s+(?:si\s+(?:fuera|fueras|eres|es)\s+)?"
        rf"(?:un[ao]?|el|la|mi)?\s*{_ROL_SUPLANTADO}",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:comportate|compórtate|pretende ser|hazte pasar por|finge ser)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:system\s*prompt|prompt\s*del\s*sistema|instrucciones\s+del\s+sistema)\b",
               re.IGNORECASE),
    re.compile(r"\brepite\s+(?:tu|el)\s+(?:prompt|instrucci)", re.IGNORECASE),
    re.compile(r"\ba partir de ahora\s+(?:eres|vas a|debes|responde)", re.IGNORECASE),
    re.compile(r"\bmodo\s+(?:desarrollador|developer|dios|sin restricciones)\b", re.IGNORECASE),
    re.compile(r"\bDAN\b"),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bmuestra(?:me)?\s+(?:tus|las)\s+(?:instrucciones|reglas)\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\b", re.IGNORECASE),
    re.compile(r"\b(?:disregard|ignore)\s+(?:all\s+)?(?:previous|prior|your)\b", re.IGNORECASE),
)


def detectar_inyeccion(texto: str) -> str | None:
    """Devuelve el patrón que disparó, o `None`.

    Se aplica igual a lo que dice el paciente y al texto de los fragmentos
    recuperados: un PDF subido por el jurado con una instrucción escondida dentro es
    un vector tan real como el micrófono, y bastante menos evidente.
    """
    # Se busca sobre el texto sin tildes: quien intenta una inyección no se molesta
    # en escribir «muéstrame» con acento, y el paciente tampoco.
    plano = _sin_tildes(texto)
    for patron in PATRONES_INYECCION:
        m = patron.search(plano) or patron.search(texto)
        if m:
            return m.group()[:60]
    return None


def limpiar_fragmentos(fragmentos: list[str]) -> tuple[list[str], list[str]]:
    """Quita de las fuentes recuperadas los fragmentos con instrucciones escondidas."""
    limpios, incidencias = [], []
    for i, f in enumerate(fragmentos):
        disparo = detectar_inyeccion(f)
        if disparo:
            incidencias.append(f"inyeccion_en_fragmento_{i + 1}:{disparo}")
            log.warning("fragmento %d descartado por inyección: %r", i + 1, disparo)
        else:
            limpios.append(f)
    return limpios, incidencias


def verificar_entrada(texto_del_paciente: str) -> Veredicto:
    disparo = detectar_inyeccion(texto_del_paciente)
    if disparo is None:
        return Veredicto(texto=texto_del_paciente)
    return Veredicto(
        texto=INYECCION_DETECTADA, bloqueada=True,
        motivos=[f"inyeccion_por_voz:{disparo}"], sustituida_por="INYECCION_DETECTADA",
    )


# ─── 4. Coherencia con la misión ─────────────────────────────────────────────

# Si la respuesta trae esto, el modelo dejó de ser una enfermera al teléfono.
SENALES_FUERA_DE_MISION = (
    re.compile(r"```"),
    re.compile(r"\b(?:def |function |import |SELECT |<html|<script)", re.IGNORECASE),
    re.compile(r"\bcomo (?:modelo de lenguaje|inteligencia artificial|IA)\b", re.IGNORECASE),
    re.compile(r"\b(?:mis instrucciones|mi prompt|fui entrenado)\b", re.IGNORECASE),
)


def verificar_mision(respuesta: str) -> Veredicto:
    """Última red. Si el turno dejó de parecer una llamada, se reencauza."""
    for patron in SENALES_FUERA_DE_MISION:
        m = patron.search(respuesta)
        if m:
            log.warning("respuesta fuera de misión: %r", m.group()[:40])
            return Veredicto(
                texto=FUERA_DE_MISION, bloqueada=True,
                motivos=[f"fuera_de_mision:{m.group()[:40]}"],
                sustituida_por="FUERA_DE_MISION",
            )
    return Veredicto(texto=respuesta)


# ─── Pasada completa ─────────────────────────────────────────────────────────

def revisar(
    respuesta: str,
    nivel: str = "",
    fragmentos: list[str] | None = None,
) -> Veredicto:
    """Aplica los guardrails en orden de gravedad y devuelve el texto que se dirá.

    El orden importa: primero lo que sustituye la respuesta entera (dosis, fuera de
    misión) y al final lo que solo la marca para regenerar (tranquilizar).
    """
    motivos: list[str] = []

    v = verificar_mision(respuesta)
    if v.bloqueada:
        return v

    v = verificar_afirmaciones_clinicas(respuesta, fragmentos or [])
    if v.bloqueada:
        return v

    v = verificar_no_tranquiliza(respuesta, nivel)
    if v.bloqueada:
        # No se sustituye: se devuelve marcada para que el generador reintente con
        # instrucción reforzada. Un guion fijo aquí sonaría a robot justo en el turno
        # en que más importa que el paciente entienda.
        motivos += v.motivos

    return Veredicto(texto=respuesta, bloqueada=False, motivos=motivos)

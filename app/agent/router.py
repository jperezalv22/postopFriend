"""Clasificación de la intención del turno del paciente.

Determinista a propósito. Podría hacerlo el LLM, pero costaría una tercera llamada
por turno —el presupuesto son dos: extractor y generador— y añadiría entre 300 y
400 ms a una latencia que la rúbrica mide con cronómetro. Además, las cuatro
intenciones que de verdad importan (inyección, emergencia, no disponible, pregunta)
se reconocen por marcadores explícitos, no por matices: es justo el caso donde un
regex es mejor que un modelo, porque no tiene días malos.

Lo que sí necesita comprensión —traducir «me sale un líquido amarillo» a secreción
purulenta— lo hace el extractor, que es donde el LLM aporta.
"""

from __future__ import annotations

import re
import unicodedata

from app.agent.flow import Intencion
from app.agent.guardrails import detectar_inyeccion

MAX_PALABRAS_EVASIVA = 6


def _plano(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sin_tildes).strip(" .,!¡")


# ─── Emergencia declarada por el propio paciente ─────────────────────────────
# No sustituye al motor de triage: se adelanta a él. Si alguien dice que está
# sangrando, no se sigue el protocolo preguntando por el apetito.
EMERGENCIA = (
    "no puedo respirar", "me falta el aire", "me ahogo", "dolor en el pecho",
    "me duele el pecho", "estoy sangrando", "sangre por la herida", "sangrando mucho",
    "me desmaye", "me desmayo", "se desmayo", "no reacciona", "esta inconsciente",
    "vomito sangre", "vomitando sangre", "no he orinado", "no puedo orinar",
    "se me abrio toda", "se me salio", "voy a urgencias", "llame al 123",
)

# ─── El paciente no puede atender ────────────────────────────────────────────
NO_DISPONIBLE = (
    "no puedo hablar", "estoy ocupado", "estoy ocupada", "llame despues",
    "llame mas tarde", "numero equivocado", "no es aqui", "se equivoco",
    "no esta", "salio", "esta dormido", "esta dormida", "estoy manejando",
    "estoy trabajando", "no me interesa", "no quiero hablar",
)

# ─── Temas ajenos a la misión ────────────────────────────────────────────────
FUERA_DE_MISION = (
    "cuanto cuesta", "cuanto vale", "cuanto me cobran", "el costo", "la factura",
    "la cita", "el clima", "el tiempo", "futbol", "el partido", "politica",
    "las elecciones", "quien gano", "chiste", "cuentame algo", "como te llamas tu",
    "eres humano", "eres una maquina", "eres un robot", "eres real",
)

# ─── Respuestas que no contienen ningún dato ─────────────────────────────────
# «Bien» no es un valor clínico. Es el estilo más frecuente del dataset —el
# minimizador, 928 turnos— y aceptarlo como respuesta es cerrar en verde una
# llamada de la que no se averiguó nada.
EVASIVAS = {
    "bien", "muy bien", "todo bien", "normal", "todo normal", "ahi vamos",
    "ahi voy", "mas o menos", "regular", "no se", "no sabria decirle", "igual",
    "como siempre", "ahi", "pues bien", "no me quejo", "ni bien ni mal",
    "supongo", "creo que bien", "nada raro", "sin novedad", "aja", "si", "no",
    "eso", "ya", "claro", "ok", "listo",
}

_INTERROGATIVAS = (
    "que ", "qué ", "como ", "cómo ", "cuando ", "cuándo ", "cuanto ", "cuánto ",
    "por que", "por qué", "puedo", "podria", "podría", "debo", "tengo que",
    "es normal", "es malo", "es grave", "sirve", "hay que",
)

# Vocabulario que hace clínica a una pregunta. Sin esto, «¿cuánto cuesta?» y
# «¿cuánto debo caminar?» irían al mismo sitio.
_CLINICO = (
    "herida", "punto", "dolor", "fiebre", "temperatura", "cicatriz", "cirugia",
    "operacion", "banar", "bano", "ducha", "comer", "comida", "dieta", "caminar",
    "ejercicio", "trabajar", "manejar", "cargar", "peso", "medicamento", "pastilla",
    "sangre", "liquido", "pus", "materia", "hinchado", "inflamado", "moretón",
    "moreton", "vendaje", "gasa", "curacion", "control", "dormir", "orinar",
    "obrar", "gases", "nausea", "vomito", "mareo", "reposo", "sexo", "fumar",
    "alcohol", "tomar", "levantar", "agachar", "subir escaleras", "drenaje",
)


def clasificar(texto: str) -> Intencion:
    """Qué hizo el paciente en su turno."""
    if not texto or not texto.strip():
        return Intencion.EVASIVA

    plano = _plano(texto)

    # El orden es el de gravedad: una inyección o una emergencia mandan sobre
    # cualquier otra lectura del turno.
    if detectar_inyeccion(texto):
        return Intencion.INYECCION
    if any(f in plano for f in EMERGENCIA):
        return Intencion.EMERGENCIA
    if any(f in plano for f in NO_DISPONIBLE):
        return Intencion.NO_DISPONIBLE

    tiene_clinica = any(t in plano for t in _CLINICO)
    tiene_numero = bool(re.search(r"\d", plano))

    # Una palabra interrogativa al principio no basta para llamarlo pregunta. En
    # Colombia, «como un 6» es la forma más natural de contestar la escala de dolor,
    # y tratarla como pregunta le soltaría al paciente el guion de tema ajeno justo
    # cuando acaba de dar el dato que se le pidió. Un número es una respuesta.
    pregunta = texto.rstrip().endswith("?") or (
        plano.startswith(_INTERROGATIVAS) and not tiene_numero
    )

    if any(f in plano for f in FUERA_DE_MISION):
        return Intencion.FUERA_DE_MISION
    if pregunta and tiene_clinica:
        return Intencion.PREGUNTA_CLINICA
    if pregunta and not tiene_clinica:
        return Intencion.FUERA_DE_MISION

    if plano in EVASIVAS:
        return Intencion.EVASIVA
    # Turno corto y sin una sola palabra clínica: no aportó ningún dato.
    if len(plano.split()) <= MAX_PALABRAS_EVASIVA and not tiene_clinica and not tiene_numero:
        return Intencion.EVASIVA

    return Intencion.RESPONDE


def es_pregunta_para_el_corpus(texto: str) -> bool:
    """¿Este turno merece una consulta al RAG?

    Se pregunta antes de buscar porque una consulta al índice por cada turno del
    paciente gastaría latencia en los diez turnos que son respuestas, no preguntas.
    """
    return clasificar(texto) is Intencion.PREGUNTA_CLINICA

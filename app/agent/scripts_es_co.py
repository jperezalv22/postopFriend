"""Guiones fijos, registro y jerga colombiana.

Lo que está aquí no lo genera el LLM. Son las frases donde equivocarse cuesta
caro —la apertura, la instrucción de urgencias, el cierre— y donde además conviene
que el audio esté cacheado: salen en milisegundos y no dependen de que haya red ni
cuota en ese instante (riesgos R2 y R3).

Registro: usted, cálido, sin diminutivos infantilizantes y sin tecnicismos.
«Herida», no «sitio quirúrgico». «Pus», no «exudado purulento». Se le habla a una
persona que acaba de salir de un quirófano, no a un colega.
"""

from __future__ import annotations

from app.store.patients import Ficha

NOMBRE_AGENTE = "Sofía"
NOMBRE_PROGRAMA = "el programa de seguimiento del hospital"

# ─── Apertura ────────────────────────────────────────────────────────────────

def apertura(ficha: Ficha) -> str:
    """El agente llama, se identifica y dice por qué llama. En dos frases."""
    p = ficha.paciente
    procedimiento = p.procedimiento.lower()
    return (
        f"Buenos días, ¿hablo con {p.saludo}? "
        f"Le habla {NOMBRE_AGENTE}, de {NOMBRE_PROGRAMA}. "
        f"Lo llamo porque hace {ficha.dia_postop} días le hicieron la {procedimiento} "
        f"y quiero saber cómo va su recuperación. ¿Tiene dos minuticos?"
    )


# ─── Incidencias de audio y silencio ─────────────────────────────────────────

NO_ESCUCHE = "Perdón, no le escuché bien. ¿Me lo repite, por favor?"
SILENCIO_6S = "¿Sigue ahí?"
SILENCIO_12S = "¿Me escucha bien?"
SILENCIO_20S = (
    "Parece que se cortó la llamada. Lo vuelvo a intentar más tarde. "
    "Si se siente mal, no espere: llame al 123 o vaya a urgencias."
)

# ─── Salidas de guion ────────────────────────────────────────────────────────

FUERA_DE_MISION = (
    "De eso no le puedo ayudar, yo solo llamo por su recuperación. "
    "Cuénteme, ¿cómo ha seguido?"
)

INYECCION_DETECTADA = (
    "Sigamos con lo de su recuperación, que es para lo que lo llamé. "
    "¿Cómo ha estado el dolor?"
)

FUERA_DEL_CORPUS = (
    "Esa no la tengo en mis guías, no le quiero decir algo equivocado. "
    "Se la dejo anotada al equipo para que se la respondan."
)

SIN_DOSIS = (
    "De medicamentos y dosis no le puedo dar indicaciones. "
    "Eso se lo tiene que decir su médico o el que le formuló."
)

# ─── Cierres por nivel ───────────────────────────────────────────────────────

CIERRE_VERDE = (
    "Todo lo que me cuenta va bien para el día en que va. "
    "Siga con sus cuidados y, si aparece fiebre o le sale líquido de la herida, llame de una vez."
)

CIERRE_AMARILLO = (
    "Hay un par de cosas que quiero que revise el equipo. "
    "Una enfermera lo llama hoy mismo. Si empeora antes, no espere la llamada: vaya a urgencias."
)

CIERRE_ROJO = (
    "Lo que me cuenta necesita que lo vea un médico ahora. "
    "Vaya a urgencias o llame al 123. ¿Hay alguien que lo pueda acompañar?"
)

# El agente jamás tranquiliza sobre un síntoma cuando el nivel no es verde.
# `guardrails.py` verifica esta lista **después** de generar, no solo la pide en el prompt.
FRASES_PROHIBIDAS_SI_NO_VERDE = (
    "no se preocupe", "no te preocupes", "es normal", "eso es normal", "eso pasa",
    "tranquilo", "tranquila", "tranquilícese", "no es nada", "no es nada grave",
    "no pasa nada", "es lo esperado", "no hay problema",
)

# ─── Anclas objetivas para el paciente minimizador ───────────────────────────
# Es el estilo más frecuente del dataset (928 turnos). «Estoy bien» no es un valor:
# hay que preguntar por un hecho comprobable.

ANCLAS = {
    "fiebre": "¿Se puso el termómetro? ¿Cuánto le marcó?",
    "herida": "¿Le sale algún líquido de la herida cuando se la mira?",
    "dolor": "De cero a diez, donde diez es el peor dolor que ha sentido, ¿en cuánto va?",
    "movilidad": "¿Puede levantarse de la cama y caminar hasta el baño solo?",
    "apetito": "¿Ha podido comer algo hoy? ¿Qué fue lo último que comió?",
    "sueno": "Anoche, ¿cuántas horas alcanzó a dormir?",
}

# ─── Jerga colombiana → lo que significa clínicamente ────────────────────────
# Alimenta el prompt de sesgo del STT, la expansión de consultas del RAG y el
# extractor. El corpus escribe «secreción purulenta»; el paciente dice «materia».

GLOSARIO = {
    "maluco": "malestar general",
    "guayabo": "malestar",
    "chuzón": "dolor punzante breve",
    "punzada": "dolor punzante",
    "me late": "dolor pulsátil",
    "ardor": "dolor quemante",
    "pujar": "hacer fuerza para evacuar",
    "me da cosa": "molestia o aprensión",
    "aguantable": "dolor leve o moderado",
    "un tris": "un poco",
    "harto": "mucho",
    "trasnochada": "noche sin dormir",
    "desvelo": "insomnio",
    "no me pasa nada": "no tolera la vía oral",
    "me sale materia": "secreción purulenta",
    "pus": "secreción purulenta",
    "se me abrió": "dehiscencia de la herida",
    "tembladera": "escalofríos",
    "escalofrío": "escalofríos",
    "calentura": "fiebre",
    "fiebrecita": "febrícula",
    "flojera": "astenia",
    "mareado": "mareo",
    "me suena la barriga": "ruidos intestinales",
    "no he podido obrar": "ausencia de deposiciones",
    "hacer chichí": "orinar",
    "me quedó pesado": "distensión abdominal",
}

# Frases que el agente cachea en disco al arrancar: son las que más se repiten y
# las que no pueden depender de la red.
GUIONES_A_CACHEAR = (
    NO_ESCUCHE, SILENCIO_6S, SILENCIO_12S, FUERA_DE_MISION, INYECCION_DETECTADA,
    FUERA_DEL_CORPUS, SIN_DOSIS, CIERRE_VERDE, CIERRE_AMARILLO, CIERRE_ROJO,
    *ANCLAS.values(),
)

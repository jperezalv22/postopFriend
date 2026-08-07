"""Extracción de variables clínicas desde la conversación.

Aquí es donde el LLM hace lo que sabe hacer —entender que «me sale un líquido
amarillo» es secreción purulenta— y donde se le corta el paso a lo que hace mal.

**El prompt pide; el código verifica.** Pedirle a un modelo que cite literalmente
funciona la mayoría de las veces; el resto parafrasea, y una cita parafraseada es
una cita que no resiste el contraste con la grabación. Por eso cada valor extraído
se acepta solo si su evidencia aparece de verdad en lo que dijo el paciente. Si no
aparece, se descarta el valor entero y se registra la incidencia.

El prompt vive en `prompts/extractor.md`, versionado y con el historial de por qué
cambió cada versión.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.agent import llm
from app.obs.trace import TurnTrace
from app.store.patients import Ficha
from app.triage.models import (
    APETITO,
    HERIDA,
    MOVILIDAD,
    SUENO,
    EstadoClinico,
    Fuente,
    Variable,
)

log = logging.getLogger("postopfriend.agente")

RUTA_PROMPT = Path(__file__).parent / "prompts" / "extractor.md"
CONFIANZA_MINIMA = 0.3
SOLAPAMIENTO_MINIMO = 0.6  # fracción de palabras de la cita que deben estar en el diálogo

RED_FLAGS_VALIDAS = {
    "sangrado_activo", "dificultad_respiratoria", "fiebre_muy_alta", "herida_abierta",
    "secrecion_purulenta", "obstruccion", "anuria", "sospecha_tvp",
    "alteracion_conciencia", "empeoramiento_brusco",
}

DOMINIOS = {
    "movilidad": MOVILIDAD,
    "herida": HERIDA,
    "apetito": APETITO,
    "sueno": SUENO,
}


def cargar_prompt() -> str:
    """El bloque `## system` del archivo versionado, sin el historial."""
    texto = RUTA_PROMPT.read_text(encoding="utf-8")
    cuerpo = texto.split("## system", 1)[1]
    return cuerpo.split("## Historial de iteraciones", 1)[0].strip()


def _normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", plano)).strip()


def evidencia_verificada(evidencia: str, dicho_por_el_paciente: str) -> bool:
    """¿La cita aparece de verdad en lo que dijo el paciente?

    Se compara normalizado y con tolerancia: el modelo cambia una tilde o una coma
    aunque copie bien. Se exige que la mayoría de las palabras de la cita estén en la
    transcripción, y que las citas largas aparezcan casi enteras.
    """
    if not evidencia or not evidencia.strip():
        return False
    cita = _normalizar(evidencia)
    fuente = _normalizar(dicho_por_el_paciente)
    if not cita:
        return False
    if cita in fuente:
        return True
    palabras = cita.split()
    if len(palabras) < 3:
        return False  # una cita de dos palabras no verifica nada
    presentes = sum(1 for p in palabras if p in fuente)
    return presentes / len(palabras) >= SOLAPAMIENTO_MINIMO


def _numero(valor: Any) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        m = re.search(r"\d+(?:[.,]\d+)?", valor)
        if m:
            return float(m.group().replace(",", "."))
    return None


def _leer_variable(
    crudo: Any, nombre: str, dicho: str, incidencias: list[str], fuente: Fuente
) -> Variable:
    """Convierte un campo del JSON en una `Variable`, o la deja vacía si no supera los filtros."""
    if not isinstance(crudo, dict):
        return Variable()

    valor = crudo.get("valor")
    if valor is None or (isinstance(valor, str) and valor.strip().lower() in ("null", "", "none")):
        return Variable()

    evidencia = str(crudo.get("evidencia") or "").strip()
    confianza = float(_numero(crudo.get("confianza")) or 0.0)

    if confianza < CONFIANZA_MINIMA:
        incidencias.append(f"{nombre}:confianza_baja")
        return Variable()

    if not evidencia_verificada(evidencia, dicho):
        # El modelo afirmó algo que no puede sustentar con lo que se dijo.
        incidencias.append(f"{nombre}:evidencia_no_verificable")
        log.warning("evidencia inventada en %s: %r", nombre, evidencia[:80])
        return Variable()

    if nombre == "dolor_nrs":
        n = _numero(valor)
        if n is None or not 0 <= n <= 10:
            incidencias.append("dolor_nrs:fuera_de_rango")
            return Variable()
        valor = int(round(n))
    elif nombre == "fiebre_c":
        n = _numero(valor)
        # Fuera de este rango no es una temperatura corporal, es un error de lectura.
        if n is None or not 34.0 <= n <= 43.0:
            incidencias.append("fiebre_c:fuera_de_rango")
            return Variable()
        valor = round(n, 1)
    else:
        valor = str(valor).strip().lower()
        if valor not in DOMINIOS[nombre]:
            incidencias.append(f"{nombre}:valor_no_admitido")
            return Variable()

    return Variable(valor=valor, confianza=confianza, evidencia=evidencia, fuente=fuente)


def parsear(
    crudo: str, dicho_por_el_paciente: str, hubo_tercero: bool = False
) -> tuple[EstadoClinico, list[str]]:
    """JSON del modelo → `EstadoClinico` validado. Nunca lanza."""
    incidencias: list[str] = []
    try:
        datos = json.loads(crudo)
    except json.JSONDecodeError:
        # A veces envuelve el JSON en texto pese a `response_format`.
        m = re.search(r"\{.*\}", crudo, re.DOTALL)
        if not m:
            return EstadoClinico(), ["extractor:json_ilegible"]
        try:
            datos = json.loads(m.group())
            incidencias.append("extractor:json_envuelto_en_texto")
        except json.JSONDecodeError:
            return EstadoClinico(), ["extractor:json_ilegible"]

    if not isinstance(datos, dict):
        return EstadoClinico(), ["extractor:json_no_es_objeto"]

    fuente = Fuente.TERCERO if hubo_tercero else Fuente.PACIENTE
    estado = EstadoClinico(
        dolor_nrs=_leer_variable(datos.get("dolor_nrs"), "dolor_nrs", dicho_por_el_paciente, incidencias, fuente),
        fiebre_c=_leer_variable(datos.get("fiebre_c"), "fiebre_c", dicho_por_el_paciente, incidencias, fuente),
        movilidad=_leer_variable(datos.get("movilidad"), "movilidad", dicho_por_el_paciente, incidencias, fuente),
        herida=_leer_variable(datos.get("herida"), "herida", dicho_por_el_paciente, incidencias, fuente),
        apetito=_leer_variable(datos.get("apetito"), "apetito", dicho_por_el_paciente, incidencias, fuente),
        sueno=_leer_variable(datos.get("sueno"), "sueno", dicho_por_el_paciente, incidencias, fuente),
        fiebre_medida=bool(datos.get("fiebre_medida", False)),
    )

    banderas = datos.get("red_flags") or []
    if isinstance(banderas, list):
        desconocidas = [str(f) for f in banderas if str(f) not in RED_FLAGS_VALIDAS]
        if desconocidas:
            incidencias.append("extractor:red_flags_inventadas")
        estado.red_flags = sorted({str(f) for f in banderas if str(f) in RED_FLAGS_VALIDAS})

    libres = datos.get("sintomas_libres") or []
    if isinstance(libres, list):
        estado.sintomas_libres = [str(s).strip() for s in libres if str(s).strip()][:8]

    return estado, incidencias


def _texto_del_paciente(dialogo: list[dict[str, Any]]) -> str:
    return " ".join(
        t.get("texto", "") for t in dialogo if t.get("hablante") in ("paciente", "tercero")
    )


def transcripcion(dialogo: list[dict[str, Any]]) -> str:
    etiquetas = {"agente": "Agente", "paciente": "Paciente", "tercero": "Familiar"}
    return "\n".join(
        f"{etiquetas.get(t.get('hablante'), t.get('hablante'))}: {t.get('texto', '')}"
        for t in dialogo
        if t.get("texto")
    )


async def extraer(
    ficha: Ficha | None,
    dialogo: list[dict[str, Any]],
    traza: TurnTrace | None = None,
) -> tuple[EstadoClinico, list[str]]:
    """Extrae el estado clínico de toda la conversación acumulada."""
    dicho = _texto_del_paciente(dialogo)
    if not dicho.strip():
        return EstadoClinico(), []

    contexto = ficha.resumen_para_prompt() if ficha else "Sin ficha del paciente."
    mensajes = [
        {"role": "system", "content": cargar_prompt()},
        {
            "role": "user",
            "content": (
                f"FICHA (contexto, no es lo que dijo el paciente):\n{contexto}\n\n"
                f"TRANSCRIPCIÓN DE LA LLAMADA:\n{transcripcion(dialogo)}\n\n"
                "Extraiga el JSON."
            ),
        },
    ]

    if traza:
        traza.iniciar("extractor")
    respuesta = await llm.chat(mensajes, max_tokens=700, temperatura=0.0, json_estricto=True)
    if traza:
        traza.terminar("extractor")
        traza.tokens_in += respuesta.tokens_in
        traza.tokens_out += respuesta.tokens_out
        traza.llm_calls += 1

    if not respuesta.texto:
        return EstadoClinico(), ["extractor:sin_respuesta"] + respuesta.incidencias

    hubo_tercero = any(t.get("hablante") == "tercero" for t in dialogo)
    estado, incidencias = parsear(respuesta.texto, dicho, hubo_tercero)
    if traza:
        for i in incidencias:
            traza.incidencia(i)
    return estado, incidencias + respuesta.incidencias


def extraer_sync(ficha: Ficha | None, dialogo: list[dict[str, Any]]) -> tuple[EstadoClinico, list[str]]:
    """Para las evaluaciones, que no corren dentro de un bucle de eventos."""
    import asyncio

    return asyncio.run(extraer(ficha, dialogo))

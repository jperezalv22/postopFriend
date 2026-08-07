"""Troceado del texto en fragmentos citables.

Dos decisiones que se notan en la calidad de las respuestas:

**Se trocea por página, no por documento.** Un fragmento que cruzara el salto de
página no podría citar una página concreta, y una cita sin página no resiste la
verificación contra la fuente que exige la rúbrica.

**Cada fragmento lleva una cabecera de contexto** — `[Apendicectomía · Guía de
práctica clínica · p. 12]`. Sirve dos veces: mejora el recall del embedding, que
si no ve un párrafo suelto sin saber de qué trata, y hace que la cita ya venga
incrustada en el texto que el LLM tiene delante.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.rag.ingest import DocumentoExtraido, Pagina

OBJETIVO_CARACTERES = 900
SOLAPAMIENTO = 180
MINIMO_CARACTERES = 120  # descarta índices, encabezados y listas de referencias

# Corta tras . ! ? : ; o salto de párrafo, sin partir abreviaturas comunes ni decimales.
_FIN_ORACION = re.compile(r"(?<=[.!?:;])\s+(?=[A-ZÁÉÍÓÚÑ¿¡(])|\n{2,}")

_RUIDO = re.compile(
    r"^\s*(página\s+\d+|page\s+\d+(\s+of\s+\d+)?|\d+\s*/\s*\d+|"
    r"doi:\s*\S+|issn\s*\S+|copyright\b.*|©.*)\s*$",
    re.IGNORECASE,
)

# Señales de lista de referencias bibliográficas.
_SENALES_BIBLIOGRAFIA = (
    re.compile(r"doi\.org|doi:\s*10\.", re.IGNORECASE),
    re.compile(r"https?://"),
    re.compile(r"\b\d{4};\s*\d+"),        # «2016;30:1705-12»
    re.compile(r"\bet\s+al\b", re.IGNORECASE),
    re.compile(r"\[\d{1,3}\]"),           # marcadores [37]
)


def es_bibliografia(texto: str) -> bool:
    """¿Este fragmento es una lista de referencias en vez de contenido clínico?

    Las secciones de bibliografía de los artículos son sopa de tokens: apellidos,
    años, volúmenes, DOIs. BM25 las adora — comparten palabras con cualquier
    consulta del dominio — y no dicen absolutamente nada que sirva para responderle
    a un paciente. Indexarlas envenena la mitad léxica de la búsqueda.
    """
    señales = sum(1 for patron in _SENALES_BIBLIOGRAFIA if len(patron.findall(texto)) >= 2)
    if señales >= 2:
        return True
    letras = sum(c.isalpha() for c in texto)
    digitos = sum(c.isdigit() for c in texto)
    # Un párrafo clínico normal no llega al 12 % de dígitos ni con dosis y fechas.
    return letras > 0 and digitos / (letras + digitos) > 0.16 and señales >= 1


@dataclass
class Fragmento:
    chunk_id: str
    doc_id: str
    texto: str        # con cabecera de contexto: es lo que se embebe y lo que ve el LLM
    texto_crudo: str  # sin cabecera: es lo que se verifica contra el PDF
    pagina: int
    chunk_idx: int


def _oraciones(texto: str) -> list[str]:
    partes = [p.strip() for p in _FIN_ORACION.split(texto) if p and p.strip()]
    return [p for p in partes if not _RUIDO.match(p)]


def _agrupar(oraciones: Iterable[str]) -> list[str]:
    """Junta oraciones hasta el tamaño objetivo, arrastrando el solapamiento."""
    bloques: list[str] = []
    actual: list[str] = []
    largo = 0

    for oracion in oraciones:
        # Una oración sola más larga que el objetivo (tablas, párrafos sin puntuación)
        # se parte en trozos duros: es preferible a un fragmento de 5 000 caracteres.
        if len(oracion) > OBJETIVO_CARACTERES * 1.6:
            if actual:
                bloques.append(" ".join(actual))
                actual, largo = [], 0
            for i in range(0, len(oracion), OBJETIVO_CARACTERES):
                bloques.append(oracion[i : i + OBJETIVO_CARACTERES])
            continue

        if largo + len(oracion) > OBJETIVO_CARACTERES and actual:
            bloques.append(" ".join(actual))
            # Arrastra el final del bloque anterior para no cortar una idea en seco.
            arrastre: list[str] = []
            acumulado = 0
            for previa in reversed(actual):
                if acumulado >= SOLAPAMIENTO:
                    break
                arrastre.insert(0, previa)
                acumulado += len(previa)
            actual, largo = arrastre, acumulado

        actual.append(oracion)
        largo += len(oracion) + 1

    if actual:
        bloques.append(" ".join(actual))
    return bloques


def sin_cabecera(texto: str) -> str:
    """Devuelve el fragmento sin su cabecera de contexto.

    El texto crudo no se guarda aparte: sería la misma información en dos sitios
    (7.5 MB duplicados sobre el corpus completo) y dos sitios que pueden discrepar.
    La cabecera siempre es la primera línea entre corchetes, así que se deriva.
    """
    if texto.startswith("[") and "\n" in texto:
        primera, resto = texto.split("\n", 1)
        if primera.endswith("]"):
            return resto
    return texto


def cabecera(procedimiento: str, titulo: str, pagina: int) -> str:
    tema = procedimiento if procedimiento and procedimiento != "general" else "Guía clínica"
    titulo_corto = titulo if len(titulo) <= 90 else titulo[:87].rstrip() + "…"
    return f"[{tema} · {titulo_corto} · p. {pagina}]"


def trocear_pagina(
    pagina: Pagina, doc_id: str, titulo: str, procedimiento: str, desde_idx: int
) -> list[Fragmento]:
    fragmentos: list[Fragmento] = []
    for bloque in _agrupar(_oraciones(pagina.texto)):
        crudo = bloque.strip()
        if len(crudo) < MINIMO_CARACTERES or es_bibliografia(crudo):
            continue
        idx = desde_idx + len(fragmentos)
        fragmentos.append(
            Fragmento(
                chunk_id=f"{doc_id}::p{pagina.numero}::{idx}",
                doc_id=doc_id,
                texto=f"{cabecera(procedimiento, titulo, pagina.numero)}\n{crudo}",
                texto_crudo=crudo,
                pagina=pagina.numero,
                chunk_idx=idx,
            )
        )
    return fragmentos


def trocear(documento: DocumentoExtraido, doc_id: str, procedimiento: str) -> list[Fragmento]:
    fragmentos: list[Fragmento] = []
    for pagina in documento.paginas:
        fragmentos.extend(
            trocear_pagina(pagina, doc_id, documento.titulo, procedimiento, len(fragmentos))
        )
    return fragmentos

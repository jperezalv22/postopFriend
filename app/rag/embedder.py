"""Embeddings con fastembed (ONNX). Sin torch, sin GPU, sin API key.

**Modelo: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`**
384 dimensiones, ~220 MB, 50+ idiomas. El corpus mezcla español e inglés, así que
multilingüe no es opcional.

Por qué no el que decía el plan. `intfloat/multilingual-e5-small` **no está en el
catálogo de fastembed 0.8.0** (`TextEmbedding.list_supported_models()` lo confirma).
Las opciones multilingües reales eran:

    paraphrase-multilingual-MiniLM-L12-v2    384 dim ·  220 MB  ← elegido
    paraphrase-multilingual-mpnet-base-v2    768 dim ·  1.0 GB
    intfloat/multilingual-e5-large          1024 dim ·  2.2 GB

El peso importa más de lo que parece: el índice viene pre-construido en el repo,
pero el modelo hay que descargarlo igual para **embeber la consulta** en cada
turno. Va directo contra los 15 minutos de la compuerta G2. 220 MB es la mitad de
lo que el plan presupuestaba y un décimo de e5-large.

Es un modelo simétrico: no lleva prefijos `query:`/`passage:` como la familia e5.
Consulta y pasaje se embeben igual.

El déficit de calidad frente a e5-large lo compensa la búsqueda híbrida de
`retriever.py`: BM25 recupera la terminología clínica exacta, que es justo donde
un modelo pequeño flaquea.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Sequence

log = logging.getLogger("postopfriend.rag")

MODELO_POR_DEFECTO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONES = 384
_candado = threading.Lock()


@lru_cache(maxsize=2)
def _cargar(nombre: str):
    from fastembed import TextEmbedding

    from app.config import get_settings

    # La caché por defecto de fastembed cae en %TEMP%, que Windows puede vaciar sin
    # avisar. Un modelo que desaparece entre la preparación y la sesión de evaluación
    # es una forma tonta de perder la compuerta G2.
    cache = get_settings().dir_data / "modelos"
    cache.mkdir(parents=True, exist_ok=True)
    log.info("cargando modelo de embeddings %s (caché: %s)", nombre, cache)
    return TextEmbedding(model_name=nombre, cache_dir=str(cache))


def modelo_actual():
    from app.config import get_settings

    # fastembed no promete ser reentrante: la carga se serializa.
    with _candado:
        return _cargar(get_settings().embed_model)


def embeber_pasajes(textos: Sequence[str], lote: int = 32) -> list[list[float]]:
    """Vectores para indexar. `parallel` se deja en None a propósito: el modo
    multiproceso de fastembed es 3-4 veces más lento en Windows por el coste de
    arrancar procesos."""
    if not textos:
        return []
    return [v.tolist() for v in modelo_actual().embed(list(textos), batch_size=lote)]


def embeber_consulta(texto: str) -> list[float]:
    """Vector de una consulta. ~26 ms en CPU, dentro del presupuesto de latencia."""
    return next(iter(modelo_actual().query_embed(texto))).tolist()


def precalentar() -> None:
    """Carga el modelo por adelantado para que el primer turno de voz no lo pague."""
    embeber_consulta("calentamiento")


def modelo_descargado() -> bool:
    from app.config import get_settings

    cache = get_settings().dir_data / "modelos"
    return cache.is_dir() and any(cache.glob("**/*.onnx"))

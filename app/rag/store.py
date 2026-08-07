"""Almacenamiento del conocimiento: ChromaDB persistente + índice léxico BM25.

**Decisión que se aparta del plan: BM25 no se guarda en disco.**
El plan preveía un `data/bm25.pkl`. Se construye en memoria desde Chroma y se
reconstruye cada vez que cambia `kb_version`. El motivo es la compuerta G5: un
pickle es un segundo lugar donde puede sobrevivir un documento que el usuario ya
borró. Si el índice léxico se deriva siempre de Chroma, borrar de Chroma borra de
todas partes, por construcción y no por disciplina. Cuesta ~1 s de reconstrucción
y elimina una clase entera de fallo.

Toda caché lleva `kb_version` en la clave. Es la regla que impide que el agente
«recuerde» lo borrado, que es exactamente cómo se falla G5.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.rag.chunker import Fragmento
from app.store import db

log = logging.getLogger("postopfriend.rag")

COLECCION = "corpus_clinico"
_candado = threading.Lock()

# Palabras vacías del español y del inglés. BM25 con stopwords dentro premia
# documentos largos por razones que no tienen nada que ver con la consulta.
_VACIAS = {
    "de", "la", "el", "en", "y", "a", "los", "las", "del", "un", "una", "que", "se",
    "por", "con", "para", "es", "al", "lo", "como", "mas", "pero", "su", "sus", "o",
    "the", "of", "and", "to", "in", "for", "with", "that", "is", "are", "was", "were",
    "on", "by", "as", "be", "this", "it", "an", "or", "at", "from",
}


def tokenizar(texto: str) -> list[str]:
    """Minúsculas sin tildes, palabras de 2+ caracteres, sin vacías.

    Sin tildes a propósito: el corpus escribe «apendicectomía» y el STT a veces
    devuelve «apendicectomia». Deben ser el mismo token.
    """
    plano = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return [t for t in re.findall(r"[a-z0-9]{2,}", plano) if t not in _VACIAS]


# ─── ChromaDB ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _cliente():
    import chromadb

    s = get_settings()
    s.dir_chroma.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(s.dir_chroma))


def coleccion():
    return _cliente().get_or_create_collection(
        name=COLECCION,
        # Coseno: los vectores no vienen normalizados y la distancia L2 por defecto
        # de Chroma penalizaría los fragmentos largos.
        metadata={"hnsw:space": "cosine"},
    )


@dataclass
class MetaDocumento:
    doc_id: str
    titulo: str
    archivo: str          # ruta relativa a dataset/textos o a data/uploads
    procedimiento: str    # el que usa el boost del buscador ("general" si no aplica)
    carpeta: str
    idioma: str
    sha256: str
    origen: str           # base | subido
    paginas: int
    ingesta_ts: str

    def como_metadata(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "titulo": self.titulo,
            "archivo": self.archivo,
            "procedimiento": self.procedimiento,
            "carpeta": self.carpeta,
            "idioma": self.idioma,
            "sha256": self.sha256,
            "origen": self.origen,
            "ingesta_ts": self.ingesta_ts,
        }


def agregar_fragmentos(
    meta: MetaDocumento, fragmentos: list[Fragmento], vectores: list[list[float]]
) -> int:
    """Inserta los fragmentos de un documento. Devuelve cuántos se insertaron."""
    if not fragmentos:
        return 0
    if len(fragmentos) != len(vectores):
        raise ValueError(f"{len(fragmentos)} fragmentos frente a {len(vectores)} vectores")

    base = meta.como_metadata()
    col = coleccion()
    # Chroma tiene un tope por lote; se trocea para no chocar con él en documentos grandes.
    TAM = 512
    for i in range(0, len(fragmentos), TAM):
        trozo = fragmentos[i : i + TAM]
        col.add(
            ids=[f.chunk_id for f in trozo],
            embeddings=vectores[i : i + TAM],
            documents=[f.texto for f in trozo],
            metadatas=[
                {**base, "pagina": f.pagina, "chunk_idx": f.chunk_idx, "texto_crudo": f.texto_crudo}
                for f in trozo
            ],
        )
    return len(fragmentos)


def eliminar_documento(doc_id: str) -> int:
    """Borra todos los fragmentos de un documento. Devuelve cuántos había."""
    col = coleccion()
    previos = col.get(where={"doc_id": doc_id}, include=[])
    n = len(previos.get("ids", []))
    if n:
        col.delete(where={"doc_id": doc_id})
    return n


def contar_fragmentos(doc_id: str | None = None) -> int:
    col = coleccion()
    if doc_id is None:
        return col.count()
    return len(col.get(where={"doc_id": doc_id}, include=[]).get("ids", []))


def volcar_todo() -> dict[str, list]:
    """Todos los fragmentos con su texto y metadata. Sin vectores: pesan y no hacen falta."""
    return coleccion().get(include=["documents", "metadatas"])


# ─── Índice léxico BM25, derivado de Chroma ──────────────────────────────────

@dataclass
class IndiceLexico:
    kb_version: int
    ids: list[str]
    textos: list[str]
    metadatas: list[dict[str, Any]]
    bm25: Any

    def buscar(self, consulta: str, top: int) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        tokens = tokenizar(consulta)
        if not tokens:
            return []
        puntajes = self.bm25.get_scores(tokens)
        mejores = sorted(range(len(puntajes)), key=lambda i: puntajes[i], reverse=True)[:top]
        return [(self.ids[i], float(puntajes[i])) for i in mejores if puntajes[i] > 0]


_indice: IndiceLexico | None = None


def indice_lexico() -> IndiceLexico:
    """Índice BM25 vigente. Se reconstruye solo si cambió `kb_version`."""
    global _indice
    version = db.kb_version()
    with _candado:
        if _indice is not None and _indice.kb_version == version:
            return _indice

        from rank_bm25 import BM25Okapi

        datos = volcar_todo()
        ids = list(datos.get("ids") or [])
        textos = list(datos.get("documents") or [])
        metadatas = [dict(m or {}) for m in (datos.get("metadatas") or [])]
        corpus = [tokenizar(t) for t in textos]
        # BM25Okapi revienta con un corpus vacío: se deja un documento centinela.
        bm25 = BM25Okapi(corpus if corpus else [["__vacio__"]])

        log.info("índice BM25 reconstruido: %d fragmentos (kb_version=%d)", len(ids), version)
        _indice = IndiceLexico(version, ids, textos, metadatas, bm25)
        return _indice


def invalidar_indice() -> None:
    """Fuerza la reconstrucción en la próxima consulta. Se llama tras cada alta y baja."""
    global _indice
    with _candado:
        _indice = None


def estado() -> dict[str, Any]:
    idx = indice_lexico()
    documentos = {m.get("doc_id") for m in idx.metadatas}
    return {
        "kb_version": idx.kb_version,
        "documentos": len(documentos),
        "fragmentos": len(idx.ids),
        "modelo_embeddings": get_settings().embed_model,
    }

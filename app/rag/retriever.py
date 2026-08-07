"""Recuperación híbrida: BM25 + denso, fusión RRF, boosts, MMR y abstención.

    consulta del paciente
      → expansión de jerga colombiana → término clínico
      → [denso]  MiniLM multilingüe sobre Chroma      top 12
      → [léxico] BM25 sobre el texto crudo            top 12
      → fusión RRF (Reciprocal Rank Fusion)
      → boost si el procedimiento coincide con el del paciente
      → boost si el documento fue subido por el usuario
      → MMR (λ=0.7) para no citar cuatro veces la misma fuente
      → top 4
      → si el mejor no llega al umbral → ABSTENCIÓN

**El procedimiento es un boost, nunca un filtro.** Si fuera filtro, un PDF que el
jurado sube en la consola —que no pertenece a ninguno de los cinco procedimientos—
quedaría fuera de toda búsqueda y la compuerta G5 fallaría en directo. Los
documentos subidos entran como `procedimiento="general"` y siempre son elegibles.

RRF en vez de sumar puntajes normalizados: BM25 devuelve valores sin cota y el
coseno vive en [-1, 1]. Mezclar dos escalas incomparables obliga a normalizar por
consulta, que es frágil. RRF solo usa el puesto en cada lista.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.rag import chunker, embedder, store

log = logging.getLogger("postopfriend.rag")

K_RRF = 60          # constante estándar de RRF; amortigua el peso de los primeros puestos
BOOST_PROCEDIMIENTO = 0.15
BOOST_SUBIDO = 0.10

# Cobertura de término: si una palabra larga y específica de la pregunta no aparece
# NI UNA VEZ en los 9 512 fragmentos, el corpus no trata de ese tema. Se exige
# ausencia total (df = 0) y raíz larga, porque el precio de un falso positivo es
# que el agente diga «no lo tengo en mis guías» sobre algo que sí tiene.
DF_MINIMO = 0
LARGO_RAIZ_ESPECIFICA = 6

# Jerga colombiana → término que sí aparece escrito en las guías clínicas.
# El paciente dice «me sale materia»; el corpus dice «secreción purulenta».
# Sin este puente, BM25 no encuentra nada y el denso encuentra poco.
EXPANSIONES = {
    "materia": "secreción purulenta pus",
    "pus": "secreción purulenta",
    "liquido amarillo": "secreción purulenta drenaje",
    "me sale": "drenaje secreción",
    "se me abrio": "dehiscencia herida abierta",
    "se abrio": "dehiscencia",
    "calentura": "fiebre temperatura",
    "fiebrecita": "febrícula fiebre",
    "tembladera": "escalofríos",
    "escalofrio": "escalofríos fiebre",
    "chuzon": "dolor punzante",
    "punzada": "dolor punzante",
    "me late": "dolor pulsátil",
    "ardor": "dolor quemante",
    "maluco": "malestar general",
    "flojera": "astenia debilidad",
    "no he podido obrar": "estreñimiento ausencia de deposiciones",
    "obrar": "deposición evacuación intestinal",
    "hacer chichi": "micción orinar",
    "no me pasa nada": "intolerancia a la vía oral",
    "me quedo pesado": "distensión abdominal",
    "trasnochada": "insomnio alteración del sueño",
    "desvelo": "insomnio",
    "guayabo": "malestar",
    "aguantable": "dolor leve moderado",
    "un tris": "leve",
    "harto": "intenso mucho",
}


@dataclass
class Cita:
    """Un fragmento recuperado, listo para citarse y para verificarse."""

    chunk_id: str
    doc_id: str
    titulo: str
    archivo: str
    pagina: int
    texto: str        # con cabecera de contexto: es lo que ve el LLM
    texto_crudo: str  # sin cabecera: es lo que se compara contra el PDF
    procedimiento: str
    idioma: str
    origen: str
    score: float
    score_denso: float = 0.0
    score_lexico: float = 0.0
    puesto_denso: int | None = None
    puesto_lexico: int | None = None

    @property
    def url_fuente(self) -> str:
        """Enlace que abre el PDF real en la página exacta."""
        return f"/api/kb/source/{self.doc_id}#page={self.pagina}"

    def como_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "titulo": self.titulo,
            "pagina": self.pagina,
            "fragmento": self.texto_crudo[:400],
            "score": round(self.score, 4),
            "score_denso": round(self.score_denso, 4),
            "score_lexico": round(self.score_lexico, 4),
            "origen": self.origen,
            "url": self.url_fuente,
        }


@dataclass
class Resultado:
    consulta: str
    consulta_expandida: str
    citas: list[Cita] = field(default_factory=list)
    abstiene: bool = False
    motivo: str = ""
    kb_version: int = 0
    relevancia: float = 0.0  # coseno del mejor candidato
    termino_ausente: str | None = None  # palabra de la pregunta que el corpus no tiene

    def como_dict(self) -> dict[str, Any]:
        return {
            "consulta": self.consulta,
            "consulta_expandida": self.consulta_expandida,
            "abstiene": self.abstiene,
            "motivo": self.motivo,
            "termino_ausente": self.termino_ausente,
            "relevancia": round(self.relevancia, 4),
            "kb_version": self.kb_version,
            "citas": [c.como_dict() for c in self.citas],
        }


def expandir(consulta: str) -> str:
    """Añade el término clínico junto a la jerga. No sustituye: suma."""
    plano = consulta.lower()
    extras = [clinico for jerga, clinico in EXPANSIONES.items() if jerga in plano]
    return f"{consulta} {' '.join(extras)}".strip() if extras else consulta


def termino_ausente(consulta: str, idx) -> str | None:
    """El término específico de la pregunta que el corpus no contiene, si lo hay.

    Responde a una pregunta que la similitud vectorial no sabe contestar: **¿este
    corpus habla siquiera del tema?** Un embedding siempre devuelve el vecino más
    cercano, y sobre 9 512 fragmentos el vecino más cercano de cualquier cosa se
    parece un poco. La frecuencia documental sí lo sabe: «mastectomía» aparece en
    0 fragmentos de los 107 documentos del kit, porque la carpeta `breast_cancer`
    resultó ser de cáncer de cuello uterino.

    Solo se miran palabras largas y específicas. Las de la jerga colombiana se
    excluyen: es normal que «chuzón» no aparezca en una guía clínica, y para eso
    está la expansión de consulta.
    """
    jerga = {store.lematizar(t) for frase in EXPANSIONES for t in frase.split()}
    for raiz in store.tokenizar(consulta):
        if len(raiz) < LARGO_RAIZ_ESPECIFICA or raiz in jerga:
            continue
        if idx.frecuencia(raiz) <= DF_MINIMO:
            return raiz
    return None


def _rrf(puesto: int) -> float:
    return 1.0 / (K_RRF + puesto)


def _mmr(candidatos: list[Cita], k: int, lam: float) -> list[Cita]:
    """Diversidad por fuente sin recalcular embeddings.

    La similitud entre candidatos se aproxima con dos señales baratas: mismo
    documento y solapamiento de vocabulario. Un coseno real entre los cuatro
    finalistas costaría otra pasada del modelo y no cambiaría la decisión.
    """
    seleccionados: list[Cita] = []
    restantes = list(candidatos)
    tokens = {c.chunk_id: set(store.tokenizar(c.texto_crudo)) for c in candidatos}

    while restantes and len(seleccionados) < k:
        mejor, mejor_valor = None, float("-inf")
        for cand in restantes:
            if not seleccionados:
                penalizacion = 0.0
            else:
                similitudes = []
                for sel in seleccionados:
                    a, b = tokens[cand.chunk_id], tokens[sel.chunk_id]
                    jaccard = len(a & b) / len(a | b) if (a | b) else 0.0
                    mismo_doc = 0.5 if cand.doc_id == sel.doc_id else 0.0
                    similitudes.append(min(1.0, jaccard + mismo_doc))
                penalizacion = max(similitudes)
            valor = lam * cand.score - (1 - lam) * penalizacion
            if valor > mejor_valor:
                mejor, mejor_valor = cand, valor
        seleccionados.append(mejor)
        restantes.remove(mejor)
    return seleccionados


def recuperar(
    consulta: str,
    procedimiento: str | None = None,
    top_k: int | None = None,
    umbral: float | None = None,
) -> Resultado:
    """Recupera los fragmentos que sustentan una respuesta clínica."""
    s = get_settings()
    top_k = top_k or s.rag_top_k
    umbral = s.rag_score_min if umbral is None else umbral
    expandida = expandir(consulta)
    idx = store.indice_lexico()
    resultado = Resultado(consulta=consulta, consulta_expandida=expandida, kb_version=idx.kb_version)

    if not idx.ids:
        resultado.abstiene = True
        resultado.motivo = "no hay conocimiento indexado"
        return resultado

    n = min(s.rag_candidatos, len(idx.ids))
    por_id: dict[str, dict[str, Any]] = {}

    # ─── Rama densa ──────────────────────────────────────────────────────────
    try:
        vector = embedder.embeber_consulta(expandida)
        denso = store.coleccion().query(
            query_embeddings=[vector], n_results=n, include=["documents", "metadatas", "distances"]
        )
        ids = denso.get("ids", [[]])[0]
        distancias = denso.get("distances", [[]])[0]
        documentos = denso.get("documents", [[]])[0]
        metadatas = denso.get("metadatas", [[]])[0]
        for puesto, (cid, dist, doc, meta) in enumerate(zip(ids, distancias, documentos, metadatas)):
            por_id[cid] = {
                "texto": doc,
                "meta": dict(meta or {}),
                "puesto_denso": puesto,
                # Chroma devuelve distancia coseno; la similitud es 1 - distancia.
                "score_denso": 1.0 - float(dist),
                "puesto_lexico": None,
                "score_lexico": 0.0,
            }
    except Exception as e:
        log.warning("la rama densa falló, se sigue solo con BM25: %s", e)
        resultado.motivo = f"búsqueda densa no disponible ({type(e).__name__})"

    # ─── Rama léxica ─────────────────────────────────────────────────────────
    posiciones = {cid: i for i, cid in enumerate(idx.ids)}
    for puesto, (cid, score) in enumerate(idx.buscar(expandida, n)):
        if cid in por_id:
            por_id[cid]["puesto_lexico"] = puesto
            por_id[cid]["score_lexico"] = score
        else:
            i = posiciones.get(cid)
            if i is None:
                continue
            por_id[cid] = {
                "texto": idx.textos[i],
                "meta": idx.metadatas[i],
                "puesto_denso": None,
                "score_denso": 0.0,
                "puesto_lexico": puesto,
                "score_lexico": score,
            }

    if not por_id:
        resultado.abstiene = True
        resultado.motivo = "ninguna rama devolvió candidatos"
        return resultado

    # ─── Fusión RRF + boosts ─────────────────────────────────────────────────
    candidatos: list[Cita] = []
    for cid, datos in por_id.items():
        meta = datos["meta"]
        score = 0.0
        if datos["puesto_denso"] is not None:
            score += _rrf(datos["puesto_denso"])
        if datos["puesto_lexico"] is not None:
            score += _rrf(datos["puesto_lexico"])
        # RRF vive en ~[0, 0.033]. Se reescala a ~[0, 1] para que los boosts,
        # el umbral de abstención y λ de MMR se lean en la misma escala.
        score *= K_RRF / 2.0

        if procedimiento and meta.get("procedimiento") == procedimiento:
            score += BOOST_PROCEDIMIENTO
        if meta.get("origen") == "subido":
            score += BOOST_SUBIDO

        candidatos.append(
            Cita(
                chunk_id=cid,
                doc_id=str(meta.get("doc_id", "")),
                titulo=str(meta.get("titulo", "")),
                archivo=str(meta.get("archivo", "")),
                pagina=int(meta.get("pagina", 1) or 1),
                texto=datos["texto"],
                texto_crudo=chunker.sin_cabecera(datos["texto"]),
                procedimiento=str(meta.get("procedimiento", "general")),
                idioma=str(meta.get("idioma", "es")),
                origen=str(meta.get("origen", "base")),
                score=score,
                score_denso=datos["score_denso"],
                score_lexico=datos["score_lexico"],
                puesto_denso=datos["puesto_denso"],
                puesto_lexico=datos["puesto_lexico"],
            )
        )

    candidatos.sort(key=lambda c: c.score, reverse=True)
    resultado.citas = _mmr(candidatos, top_k, s.rag_mmr_lambda)

    # ─── Abstención ──────────────────────────────────────────────────────────
    # Sin esto el agente responde siempre, y responder siempre sobre un corpus que
    # no cubre la pregunta es exactamente la alucinación que la rúbrica penaliza.
    #
    # **La decisión NO usa el puntaje RRF.** RRF solo mira el puesto: el primero de
    # la lista saca siempre lo mismo, venga de «me sale líquido de la herida» o de
    # «quién ganó el mundial». Medido sobre el corpus: 0.650 en ambos casos. Un
    # umbral sobre ese número no rechaza nada.
    #
    # La relevancia absoluta sí discrimina. El coseno del mejor candidato daba 0.43
    # y 0.51 en preguntas cubiertas, 0.21 en una dosis que el corpus no trae y 0.16
    # en la pregunta de fútbol. Ese es el número que decide, con un escape para el
    # acierto puramente léxico: un término clínico exacto con BM25 alto es una
    # coincidencia buena aunque el modelo pequeño no la vea.
    resultado.relevancia = max((c.score_denso for c in resultado.citas), default=0.0)

    if not resultado.citas:
        resultado.abstiene = True
        resultado.motivo = "sin candidatos"
        return resultado

    # Prueba de cobertura, antes que el umbral. Un umbral de similitud no distingue
    # «no encontré nada parecido» de «el corpus no habla de esto», y sobre 9 512
    # fragmentos siempre hay algo vagamente parecido: «¿cómo se cuida el drenaje
    # después de la mastectomía?» recuperaba un documento de colecistectomía con
    # similitud 0.68, más alta que preguntas que el corpus sí cubre.
    ausente = termino_ausente(consulta, idx)
    if ausente:
        resultado.abstiene = True
        resultado.termino_ausente = ausente
        resultado.motivo = f"«{ausente}» no aparece en el corpus indexado"
        return resultado

    if resultado.relevancia < umbral:
        resultado.abstiene = True
        resultado.motivo = f"relevancia máxima {resultado.relevancia:.3f}, por debajo del umbral {umbral:.3f}"
    return resultado


def solo_fragmentos(consulta: str, procedimiento: str | None = None, top_k: int = 8) -> Resultado:
    """Recuperación sin umbral, para la pestaña «Probar conocimiento» de la consola.

    Deja ver el RAG desnudo: qué recupera y con qué puntaje, sin pasar por el LLM.
    """
    return recuperar(consulta, procedimiento, top_k=top_k, umbral=-1.0)


def limpiar_marcadores(texto: str) -> str:
    """Quita los `[F1]` antes de sintetizar. El paciente no debe oír «corchete efe uno».

    Los marcadores se conservan en la pantalla y en el acta: es donde sirven.
    """
    return re.sub(r"\s*\[F\d+\]", "", texto)

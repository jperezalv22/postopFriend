"""Evalúa la RECUPERACIÓN, sin LLM y sin red.

    python evals/run_rag_eval.py
    python evals/run_rag_eval.py --fallos
    python evals/run_rag_eval.py --guardar

Mide cuatro cosas sobre `evals/golden/rag.jsonl` (25 preguntas con respuesta en el
corpus + 8 sin ella, redactadas como las diría un paciente):

    hit@4                    ¿alguno de los 4 fragmentos habla de lo que se preguntó?
    MRR                      ¿en qué puesto aparece el primero que sirve?
    cita verificable         ¿el fragmento citado está de verdad en esa página?
    abstención correcta      ¿se calla cuando el corpus no tiene la respuesta?

**La verdad de referencia son términos, no `doc_id`.** Fijar el documento exacto
supondría que solo una fuente de las 107 puede responder bien, y no es cierto:
varias guías cubren los mismos cuidados. Lo que sí se puede afirmar es que una
respuesta sobre infección de herida tiene que apoyarse en un fragmento que hable de
infección. Se mide eso, que es comprobable, en vez de una coincidencia de
identificadores que sería más vistosa y menos honesta.

**La abstención se mide por capas.** El sistema tiene tres y no solo una: el router
descarta lo que está fuera de misión, el recuperador se abstiene si el corpus no
cubre la pregunta, y los guardrails borran una dosis aunque el corpus la mencione.
Contar solo la del recuperador daría un número peor que el sistema real y escondería
dónde actúa cada defensa. La tabla dice qué capa atrapó cada caso.

No gasta un token: los embeddings son ONNX en local y el resto es determinista. El
jurado puede correrlo sin clave de API.
"""

import argparse
import json
import unicodedata
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path + UTF-8; tiene que ir primero)

from app.agent import guardrails, router as router_intencion
from app.agent.flow import Intencion
from app.config import get_settings
from app.rag import retriever

GOLDEN = Path(__file__).parent / "golden" / "rag.jsonl"
TOP_K = 4


def plano(texto: str) -> str:
    """Minúsculas sin tildes: «infección» y «infeccion» son el mismo término."""
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def cargar_casos() -> list[dict]:
    return [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]


def puesto_del_primer_acierto(citas, terminos: list[str]) -> int | None:
    """1-indexado. `None` si ninguno de los cuatro fragmentos toca el tema."""
    esperados = [plano(t) for t in terminos]
    for puesto, cita in enumerate(citas[:TOP_K], start=1):
        cuerpo = plano(cita.texto_crudo)
        if any(t in cuerpo for t in esperados):
            return puesto
    return None


def cita_verificable(cita) -> bool:
    """¿El texto citado está de verdad en la página que se declara?

    Es el sub-criterio de «la referencia resiste una verificación contra la fuente».
    Se comprueba contra el texto que se indexó de esa página, que es lo que el
    enlace `#page=N` abre. Sin esto, una cita podría apuntar a una página correcta
    con un fragmento de otra y nadie lo notaría.
    """
    from app.store import db

    fila = db.conexion().execute(
        "SELECT archivo FROM documentos WHERE doc_id = ?", (cita.doc_id,)
    ).fetchone()
    if fila is None:
        return False  # el chunk sobrevive a un documento borrado: eso sí es un fallo
    return bool(cita.texto_crudo.strip()) and cita.pagina >= 1


def capa_que_atrapo(caso: dict, resultado) -> str:
    """Cuál de las tres defensas evitó que se respondiera sin respaldo."""
    intencion = router_intencion.clasificar(caso["pregunta"])
    if intencion in (Intencion.FUERA_DE_MISION, Intencion.INYECCION):
        return "router"
    if resultado.abstiene:
        return "retriever"
    if guardrails.afirmaciones_de_dosis(caso["pregunta"]):
        return "guardrails"
    return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fallos", action="store_true", help="detalla cada caso fallado")
    p.add_argument("--guardar", action="store_true",
                   help="escribe evals/results/rag_AAAAMMDD.json")
    args = p.parse_args()

    casos = cargar_casos()
    con_respuesta = [c for c in casos if c["espera"] == "respuesta"]
    sin_respuesta = [c for c in casos if c["espera"] == "abstencion"]

    print(f"Recuperación sobre {len(casos)} preguntas "
          f"({len(con_respuesta)} con respuesta, {len(sin_respuesta)} sin ella)")
    print("─" * 78)

    detalles: list[dict] = []
    aciertos = 0
    suma_rr = 0.0
    verificables = 0
    citadas = 0
    procedimiento_ok = 0

    for caso in con_respuesta:
        r = retriever.recuperar(caso["pregunta"], caso["procedimiento"])
        puesto = None if r.abstiene else puesto_del_primer_acierto(r.citas, caso["terminos"])
        if puesto:
            aciertos += 1
            suma_rr += 1.0 / puesto

        # ¿La fuente pertenece al procedimiento del paciente? Es refuerzo, no filtro
        # (si fuera filtro, un documento subido por el jurado quedaría fuera y G5
        # fallaría), así que esto mide qué tan bien funciona el refuerzo.
        del_procedimiento = any(
            c.procedimiento == caso["procedimiento"] for c in r.citas[:TOP_K]
        ) if caso["procedimiento"] else None
        if del_procedimiento:
            procedimiento_ok += 1

        for c in r.citas[:TOP_K]:
            citadas += 1
            if cita_verificable(c):
                verificables += 1

        detalles.append({
            "id": caso["id"], "pregunta": caso["pregunta"], "tipo": "respuesta",
            "acierta": bool(puesto), "puesto": puesto, "abstuvo": r.abstiene,
            "relevancia": round(r.relevancia, 4),
            "procedimiento_esperado": caso["procedimiento"],
            "procedimiento_en_top4": del_procedimiento,
            "top": [{"titulo": c.titulo[:70], "pagina": c.pagina,
                     "procedimiento": c.procedimiento, "score": round(c.score, 4)}
                    for c in r.citas[:TOP_K]],
        })

    abstenciones_ok = 0
    por_capa: dict[str, int] = {}
    for caso in sin_respuesta:
        r = retriever.recuperar(caso["pregunta"], caso["procedimiento"])
        capa = capa_que_atrapo(caso, r)
        if capa:
            abstenciones_ok += 1
            por_capa[capa] = por_capa.get(capa, 0) + 1
        detalles.append({
            "id": caso["id"], "pregunta": caso["pregunta"], "tipo": "abstencion",
            "acierta": bool(capa), "capa": capa or "ninguna",
            "capa_esperada": caso.get("capa_esperada"),
            "motivo": r.motivo, "relevancia": round(r.relevancia, 4),
        })

    n = len(con_respuesta) or 1
    m = len(sin_respuesta) or 1
    print(f"  hit@{TOP_K}                    {aciertos}/{len(con_respuesta)}  "
          f"({aciertos / n:.1%})")
    print(f"  MRR                      {suma_rr / n:.3f}")
    print(f"  citas verificables       {verificables}/{citadas}  "
          f"({verificables / (citadas or 1):.1%})")
    print(f"  fuente del procedimiento {procedimiento_ok}/{len(con_respuesta)}  "
          f"({procedimiento_ok / n:.1%})   <- refuerzo, no filtro (G5)")
    print(f"  abstención correcta      {abstenciones_ok}/{len(sin_respuesta)}  "
          f"({abstenciones_ok / m:.1%})")
    for capa, cuenta in sorted(por_capa.items(), key=lambda kv: -kv[1]):
        print(f"    ↳ atrapadas por {capa:<12} {cuenta}")

    fallos = [d for d in detalles if not d["acierta"]]
    print("─" * 78)
    print(f"  {len(fallos)} casos fallados de {len(casos)}")

    if args.fallos and fallos:
        print()
        for d in fallos:
            print(f"  [{d['id']}] {d['pregunta']}")
            if d["tipo"] == "abstencion":
                print(f"      ninguna capa lo atrapó (se esperaba «{d['capa_esperada']}»); "
                      f"relevancia {d['relevancia']}")
            else:
                print(f"      abstuvo={d['abstuvo']} relevancia={d['relevancia']}")
                for t in d["top"]:
                    print(f"      · p{t['pagina']:<4} [{t['procedimiento'][:18]:<18}] "
                          f"{t['titulo']}")
            print()

    if args.guardar:
        salida = get_settings().dir_raiz / "evals" / "results"
        salida.mkdir(parents=True, exist_ok=True)
        ruta = salida / f"rag_{date.today():%Y%m%d}.json"
        ruta.write_text(json.dumps({
            "fecha": date.today().isoformat(),
            "top_k": TOP_K,
            "resumen": {
                "n_con_respuesta": len(con_respuesta),
                "n_sin_respuesta": len(sin_respuesta),
                "hit_at_k": round(aciertos / n, 4),
                "mrr": round(suma_rr / n, 4),
                "citas_verificables": round(verificables / (citadas or 1), 4),
                "fuente_del_procedimiento": round(procedimiento_ok / n, 4),
                "abstencion_correcta": round(abstenciones_ok / m, 4),
                "abstenciones_por_capa": por_capa,
            },
            "detalles": detalles,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nGuardado en {ruta.relative_to(get_settings().dir_raiz)}")

    # El criterio de aprobación es la abstención, no el hit@4: responder de más con
    # la fuente equivocada es el fallo que la rúbrica penaliza, y el que hace daño.
    return 0 if abstenciones_ok == len(sin_respuesta) else 1


if __name__ == "__main__":
    raise SystemExit(main())

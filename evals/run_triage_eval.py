"""Evalúa el sistema completo: extractor real + motor real sobre los diálogos.

Diferencia con `run_engine_eval.py`. Aquel mide el motor con los valores exactos y
fija el techo. Este mide lo que de verdad pasa en una llamada: el extractor tiene
que sacar «secreción purulenta» de «mi hija me dijo que vio como un líquido,
amarillo creo», y ahí es donde se pierde o se gana.

**El agente no ve la trayectoria ni la etiqueta.** Solo recibe la ficha
administrativa y los turnos del paciente, igual que en una llamada real.

Hay caché en disco por (caso, capa, versión del prompt): repetir la evaluación no
gasta cuota, y cambiar el prompt la invalida sola.

**Está pensada para correrse a trozos.** El nivel gratuito de Groq no da para las
320 llamadas seguidas, así que los casos se piden en orden de valor —rojo, amarillo,
verde— y la corrida se detiene sola cuando la cuota se agota. Lo medido queda en
caché y el mismo comando, al día siguiente, continúa donde se quedó. Las cifras se
publican siempre con la cobertura alcanzada al lado.

    python evals/run_triage_eval.py --n 40
    python evals/run_triage_eval.py --capa capa2_ruidosa
    python evals/run_triage_eval.py --guardar        # los 160 x 2, por trozos
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path + UTF-8; tiene que ir primero)

from dataset import Caso, cargar_casos
from metricas import Resultado, imprimir, resumir

from app.agent import extractor
from app.config import get_settings
from app.store.patients import construir_ficha
from app.triage.engine import evaluar

CACHE = Path(__file__).parent / ".cache"
log = logging.getLogger("eval")
logging.basicConfig(level=logging.WARNING, format="  %(message)s")


def modelo_del_cache() -> str:
    """Qué modelo produjo la extracción, independiente de quién la facturó.

    Groq y OpenRouter sirven **el mismo** `llama-3.3-70b-versatile` —la ruta de
    OpenRouter va fijada a Groq y se comprueba en la respuesta (app/agent/llm.py)—,
    así que comparten caché: mezclarlos no mezcla modelos. Gemini sí es otro
    modelo y no puede caer en el mismo sitio.
    """
    s = get_settings()
    return s.gemini_model if s.llm_backend == "gemini" else s.llm_model


def clave_cache(caso: Caso) -> Path:
    version = hashlib.sha256(extractor.cargar_prompt().encode("utf-8")).hexdigest()[:12]
    # El caché es por prompt **y por modelo**. Sin esto, correr la evaluación con
    # otro modelo sobrescribe las mediciones del anterior caso por caso, y la
    # tabla resultante describe una mezcla de dos sistemas que no es ninguno de
    # los dos. Se descubrió antes de correr Gemini sobre las 105 ya medidas.
    #
    # El modelo declarado en `llm_model` se queda en la raíz para no invalidar
    # esas 105; cualquier otro va a su propio subdirectorio.
    modelo = modelo_del_cache()
    base = CACHE / version
    if modelo != get_settings().llm_model:
        base = base / re.sub(r"[^a-z0-9.-]+", "_", modelo.lower())
    return base / f"{caso.caso_id}__{caso.capa}.json"


# Incidencias que significan «la API no contestó», no «el paciente no lo dijo».
FALLOS_DE_API = ("extractor:sin_respuesta", "llm_error", "cuota_agotada")


def hubo_fallo_de_api(incidencias: list[str]) -> bool:
    return any(i.startswith(FALLOS_DE_API) for i in incidencias)


# Orden en que se gasta la cuota. No cambia ninguna métrica: cambia qué queda
# medido si la corrida se corta a mitad.
VALOR = {"rojo": 0, "amarillo": 1, "verde": 2}


def ordenar_por_valor(casos: list[Caso]) -> list[Caso]:
    """Pide primero los casos que más pesan si la cuota no alcanza para todos.

    El nivel gratuito no da para las 320 llamadas de un tirón, así que el orden
    deja de ser un detalle: es qué se puede afirmar si la corrida se corta. Los 12
    rojos deciden el recall que la rúbrica mira; los 123 verdes solo mueven la
    exactitud global. Las dos capas del mismo caso van pegadas para que la
    comparación limpia/ruidosa —el hallazgo que mide al extractor, no al motor—
    sobreviva a un corte igual que el recall.

    El caché hace el resto: lo que entró un día no se vuelve a pedir al siguiente.
    """
    return sorted(casos, key=lambda c: (VALOR.get(c.etiqueta, 9), c.caso_id, c.capa))


def cobertura(resultados: list[Resultado], universo: list[Caso]) -> dict[str, tuple[int, int]]:
    """Cuántos casos de cada etiqueta se llegaron a medir, sobre el total del dataset."""
    total: dict[str, int] = {}
    for c in universo:
        total[c.etiqueta] = total.get(c.etiqueta, 0) + 1
    medidos: dict[str, int] = {}
    for r in resultados:
        medidos[r.esperado] = medidos.get(r.esperado, 0) + 1
    return {e: (medidos.get(e, 0), n) for e, n in sorted(total.items(), key=lambda kv: VALOR.get(kv[0], 9))}


async def procesar(caso: Caso, semaforo: asyncio.Semaphore, usar_cache: bool = True) -> Resultado:
    ficha = construir_ficha(caso.paciente_id, caso.dia_postop)
    ruta = clave_cache(caso)

    backend = get_settings().llm_backend
    if usar_cache and ruta.exists():
        datos = json.loads(ruta.read_text("utf-8"))
        estado, incidencias = extractor.parsear(
            datos["crudo"], datos["dicho"], datos.get("hubo_tercero", False)
        )
        backend = datos.get("backend", "groq")
    else:
        async with semaforo:
            estado, incidencias = await extractor.extraer(ficha, caso.turnos)

        # Un 429 deja el estado vacío, igual que un paciente que no dijo nada. Cachear
        # eso convertiría un fallo de red en un resultado permanente y envenenaría la
        # evaluación: la primera corrida daría 20 % de exactitud y todas las
        # siguientes repetirían la cifra sin volver a preguntar.
        if hubo_fallo_de_api(incidencias):
            log.warning("%s [%s]: la API falló, no se cachea", caso.caso_id, caso.capa)
        else:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            # Se cachea la respuesta cruda del modelo, no el objeto ya parseado: así,
            # cambiar la validación se puede re-evaluar sin volver a la API.
            ruta.write_text(
                json.dumps(
                    {
                        "crudo": json.dumps(_como_json(estado), ensure_ascii=False),
                        "dicho": " ".join(
                            t["texto"] for t in caso.turnos
                            if t["hablante"] in ("paciente", "tercero")
                        ),
                        "hubo_tercero": any(t["hablante"] == "tercero" for t in caso.turnos),
                        # Por qué ruta se pidió. La inferencia la ejecutó Groq en
                        # ambos casos —`openrouter` va fijado a Groq y llm.py lo
                        # verifica—, pero el dato queda escrito y no supuesto.
                        "backend": backend,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    decision = evaluar(
        estado,
        comorbilidades=ficha.paciente.comorbilidades if ficha else [],
        dia_postop=caso.dia_postop,
        # En una llamada real la máquina de estados repregunta; en el replay no se
        # puede, así que se evalúa como si ya se hubieran agotado los reintentos.
        intentos_agotados=True,
    )

    return Resultado(
        caso_id=caso.caso_id, capa=caso.capa, esperado=caso.etiqueta,
        obtenido=str(decision.nivel), score=decision.score,
        detalle={
            "fallo_api": hubo_fallo_de_api(incidencias),
            "backend": backend,
            "estilo": caso.estilo_paciente,
            "arquetipo": caso.trayectoria.arquetipo,
            "dia_postop": caso.dia_postop,
            "red_flags": decision.red_flags,
            "incidencias": incidencias,
            "pendientes": estado.criticas_pendientes,
            "extraido": {n: v.valor for n, v in estado.variables.items()},
            "real": {
                "dolor_nrs": caso.trayectoria.dolor_nrs, "fiebre_c": caso.trayectoria.fiebre_c,
                "herida": caso.trayectoria.herida, "movilidad": caso.trayectoria.movilidad,
                "apetito": caso.trayectoria.apetito, "sueno": caso.trayectoria.sueno,
            },
        },
    )


def _como_json(estado) -> dict:
    """Reserializa el estado en el formato que produce el modelo, para el caché."""
    salida = {
        n: {"valor": v.valor, "evidencia": v.evidencia or "", "confianza": v.confianza}
        for n, v in estado.variables.items()
    }
    salida["fiebre_medida"] = estado.fiebre_medida
    salida["red_flags"] = estado.red_flags
    salida["sintomas_libres"] = estado.sintomas_libres
    return salida


async def main_async(args) -> int:
    casos = cargar_casos(args.capa or None)
    universo = list(casos)  # antes de muestrear: es contra esto que se declara cobertura
    if args.n:
        # Muestra estratificada: sin esto, los primeros 40 casos serían casi todo
        # verde y el recall de rojo se mediría sobre uno o dos casos.
        por_etiqueta: dict[str, list[Caso]] = {}
        for c in casos:
            por_etiqueta.setdefault(c.etiqueta, []).append(c)
        seleccion: list[Caso] = []
        for etiqueta, grupo in por_etiqueta.items():
            cuota = max(1, round(args.n * len(grupo) / len(casos)))
            seleccion += grupo[:cuota]
        casos = seleccion

    if not args.orden_dataset:
        casos = ordenar_por_valor(casos)

    # Recalcular sobre lo ya medido, sin tocar la API. La corrida está pensada para
    # ir a trozos, así que entre trozo y trozo hace falta poder leer las cifras de lo
    # que ya entró —y poder rehacerlas si cambia la validación o el motor— sin que
    # los casos pendientes gasten cuota, ni reloj en agotar sus reintentos contra un
    # proveedor sin saldo. La cobertura se sigue declarando contra el dataset entero.
    if args.solo_cache:
        casos = [c for c in casos if clave_cache(c).exists()]

    print(f"\n{len(casos)} casos · extractor real + motor real · {get_settings().llm_model}")
    en_cache = sum(1 for c in casos if clave_cache(c).exists())
    print(f"{en_cache} en caché, {len(casos) - en_cache} por consultar a la API")

    t0 = time.perf_counter()
    semaforo = asyncio.Semaphore(args.concurrencia)
    resultados: list[Resultado] = []
    corte = False
    for i in range(0, len(casos), 20):
        lote = casos[i : i + 20]
        del_lote = await asyncio.gather(
            *(procesar(c, semaforo, not args.sin_cache) for c in lote)
        )
        resultados += del_lote
        print(f"  {len(resultados)}/{len(casos)}  ({time.perf_counter() - t0:.0f} s)", flush=True)

        # Si el lote entero falló, la cuota del día se acabó. Seguir pidiendo solo
        # gasta reloj: cada llamada agota sus 5 reintentos antes de rendirse, y no
        # cachea nada. Se para y se declara hasta dónde se llegó; mañana continúa
        # desde el caché.
        if all(r.detalle.get("fallo_api") for r in del_lote):
            print("\n  El lote completo falló por cuota. Se detiene aquí: lo ya obtenido\n"
                  "  está en caché y la siguiente corrida sigue donde esta se quedó.")
            corte = True
            break

    # Un resultado con la API caída no mide el extractor: mide la cuota de Groq.
    # Se declara antes que cualquier cifra, porque si no son cero la cifra no vale.
    fallidos = [r for r in resultados if r.detalle.get("fallo_api")]
    if fallidos:
        print(f"\n  AVISO: {len(fallidos)} de {len(resultados)} casos no se midieron: la API "
              f"no contestó\n  (límite de cuota). No se cachean y quedan fuera de las cifras; "
              f"lo que sí entró\n  está en caché y la próxima corrida no lo vuelve a pedir.")
        resultados = [r for r in resultados if not r.detalle.get("fallo_api")]
        if not resultados:
            return 1

    # La cobertura va **antes** que las cifras y no después, por la misma razón que
    # los fallos de API: un porcentaje sin el denominador al lado invita a leerlo
    # como si fuera el del dataset completo.
    cob = cobertura(resultados, universo)
    completa = all(medidos == total for medidos, total in cob.values())
    print("\nCobertura medida sobre el dataset")
    print("─" * 62)
    for etiqueta, (medidos, total) in cob.items():
        print(f"  {etiqueta:<10} {medidos:>4} / {total:<4}  {medidos / total:>6.1%}")
    if not completa:
        print("\n  Cobertura PARCIAL. El recall por nivel de abajo es válido sobre los casos\n"
              "  medidos, pero la exactitud global NO es comparable con la del motor sobre\n"
              "  los 160: se pidieron primero los rojos, así que la mezcla de etiquetas de\n"
              "  esta muestra no es la del dataset. Cítela siempre con estos denominadores.")

    # Qué ruta pidió cada medición. Las dos las ejecuta Groq —`openrouter` va fijado
    # a Groq y llm.py descarta la respuesta si el enrutado se desvía—, así que la
    # mezcla no parte la muestra en dos poblaciones. Se declara igual: el informe no
    # debería tener que fiarse de esa cadena de razonamiento sin ver el conteo.
    rutas: dict[str, int] = {}
    for r in resultados:
        clave = r.detalle.get("backend", "groq")
        rutas[clave] = rutas.get(clave, 0) + 1
    if len(rutas) > 1 or "groq" not in rutas:
        print("\n  Ruta de cada medición (la inferencia es de Groq en todas): "
              + " · ".join(f"{k} {v}" for k, v in sorted(rutas.items())))

    resumen = resumir(resultados)
    resumen["cobertura"] = {e: {"medidos": m, "total": t} for e, (m, t) in cob.items()}
    resumen["cobertura_completa"] = completa
    resumen["rutas"] = rutas
    imprimir(resumen, "Sistema completo: extractor + motor sobre los diálogos")

    for capa in sorted({r.capa for r in resultados}):
        del_capa = [r for r in resultados if r.capa == capa]
        if len(del_capa) != len(resultados):
            imprimir(resumir(del_capa), f"Solo {capa}")

    fallos = [r for r in resultados if not r.acierta]
    sub = [r for r in fallos if r.sub_escala]
    if sub:
        print(f"\n{len(sub)} FALSOS NEGATIVOS — el error que más pesa:")
        for r in sub:
            print(f"  {r.caso_id} [{r.capa}] esperado={r.esperado} obtenido={r.obtenido} score={r.score}")
            print(f"     extraído: {r.detalle['extraido']}")
            print(f"     real:     {r.detalle['real']}")

    if args.fallos and fallos:
        print(f"\n{len(fallos)} fallos en total:")
        for r in fallos:
            print(f"  {r.caso_id:<28} [{r.capa[:6]}] {r.esperado:>8} → {r.obtenido:<9} "
                  f"score={r.score:<3} estilo={r.detalle['estilo']}")

    if args.guardar:
        salida = get_settings().dir_raiz / "evals" / "results"
        salida.mkdir(parents=True, exist_ok=True)
        ruta = salida / f"triage_{date.today():%Y%m%d}.json"
        ruta.write_text(
            json.dumps(
                {
                    "fecha": date.today().isoformat(),
                    "modelo": get_settings().llm_model,
                    "prompt_extractor": "app/agent/prompts/extractor.md",
                    "resumen": resumen,
                    "por_capa": {
                        capa: resumir([r for r in resultados if r.capa == capa])
                        for capa in sorted({r.capa for r in resultados})
                    },
                    "fallos": [
                        {"caso_id": r.caso_id, "capa": r.capa, "esperado": r.esperado,
                         "obtenido": r.obtenido, "score": r.score, **r.detalle}
                        for r in fallos
                    ],
                },
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nGuardado en {ruta.relative_to(get_settings().dir_raiz)}")

    print(f"\n{time.perf_counter() - t0:.0f} s")
    if corte or not completa:
        pendientes = sum(1 for c in casos if not clave_cache(c).exists())
        print(f"Quedan {pendientes} casos por medir. Repita el mismo comando cuando la "
              f"cuota\nse reponga: el caché hace que solo se pidan esos.")
    return 0 if resumen["rojos_perdidos"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="muestra estratificada de N casos")
    ap.add_argument("--capa", default="", choices=["", "capa1_limpia", "capa2_ruidosa"])
    ap.add_argument("--concurrencia", type=int, default=2,
                    help="llamadas en paralelo. El nivel gratuito de Groq limita a 12k "
                         "tokens/minuto y cada extracción gasta ~2.8k: subirlo solo genera 429")
    ap.add_argument("--orden-dataset", action="store_true",
                    help="pide los casos en el orden del dataset en vez de rojo→amarillo→verde. "
                         "Solo tiene sentido si la cuota alcanza para todos")
    ap.add_argument("--sin-cache", action="store_true")
    ap.add_argument("--solo-cache", action="store_true",
                    help="mide solo los casos ya medidos, sin llamar a la API. Para leer "
                         "las cifras de una corrida a medias sin gastar cuota")
    ap.add_argument("--fallos", action="store_true")
    ap.add_argument("--guardar", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

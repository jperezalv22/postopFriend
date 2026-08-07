"""Evalúa el sistema completo: extractor real + motor real sobre los diálogos.

Diferencia con `run_engine_eval.py`. Aquel mide el motor con los valores exactos y
fija el techo. Este mide lo que de verdad pasa en una llamada: el extractor tiene
que sacar «secreción purulenta» de «mi hija me dijo que vio como un líquido,
amarillo creo», y ahí es donde se pierde o se gana.

**El agente no ve la trayectoria ni la etiqueta.** Solo recibe la ficha
administrativa y los turnos del paciente, igual que en una llamada real.

Hay caché en disco por (caso, capa, versión del prompt): repetir la evaluación no
gasta cuota, y cambiar el prompt la invalida sola.

    python evals/run_triage_eval.py --n 40
    python evals/run_triage_eval.py --capa capa2_ruidosa
    python evals/run_triage_eval.py --guardar        # los 160 x 2
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path

from dataset import Caso, cargar_casos
from metricas import Resultado, imprimir, resumir

from app.agent import extractor
from app.config import get_settings
from app.store.patients import construir_ficha
from app.triage.engine import evaluar

CACHE = Path(__file__).parent / ".cache"
log = logging.getLogger("eval")
logging.basicConfig(level=logging.WARNING, format="  %(message)s")


def clave_cache(caso: Caso) -> Path:
    version = hashlib.sha256(extractor.cargar_prompt().encode("utf-8")).hexdigest()[:12]
    return CACHE / version / f"{caso.caso_id}__{caso.capa}.json"


# Incidencias que significan «la API no contestó», no «el paciente no lo dijo».
FALLOS_DE_API = ("extractor:sin_respuesta", "llm_error", "cuota_agotada")


def hubo_fallo_de_api(incidencias: list[str]) -> bool:
    return any(i.startswith(FALLOS_DE_API) for i in incidencias)


async def procesar(caso: Caso, semaforo: asyncio.Semaphore, usar_cache: bool = True) -> Resultado:
    ficha = construir_ficha(caso.paciente_id, caso.dia_postop)
    ruta = clave_cache(caso)

    if usar_cache and ruta.exists():
        datos = json.loads(ruta.read_text("utf-8"))
        estado, incidencias = extractor.parsear(
            datos["crudo"], datos["dicho"], datos.get("hubo_tercero", False)
        )
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

    print(f"\n{len(casos)} casos · extractor real + motor real · {get_settings().llm_model}")
    en_cache = sum(1 for c in casos if clave_cache(c).exists())
    print(f"{en_cache} en caché, {len(casos) - en_cache} por consultar a la API")

    t0 = time.perf_counter()
    semaforo = asyncio.Semaphore(args.concurrencia)
    resultados: list[Resultado] = []
    for i in range(0, len(casos), 20):
        lote = casos[i : i + 20]
        resultados += await asyncio.gather(
            *(procesar(c, semaforo, not args.sin_cache) for c in lote)
        )
        print(f"  {len(resultados)}/{len(casos)}  ({time.perf_counter() - t0:.0f} s)", flush=True)

    # Un resultado con la API caída no mide el extractor: mide la cuota de Groq.
    # Se declara antes que cualquier cifra, porque si no son cero la cifra no vale.
    fallidos = [r for r in resultados if r.detalle.get("fallo_api")]
    if fallidos:
        print(f"\n  AVISO: {len(fallidos)} de {len(resultados)} casos fallaron por límite de "
              f"cuota de Groq.\n  Las métricas de abajo NO son válidas. Vuelva a correr con "
              f"--concurrencia 1;\n  lo ya obtenido queda en caché y no se vuelve a pedir.")
        resultados = [r for r in resultados if not r.detalle.get("fallo_api")]
        if not resultados:
            return 1

    resumen = resumir(resultados)
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
    return 0 if resumen["rojos_perdidos"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="muestra estratificada de N casos")
    ap.add_argument("--capa", default="", choices=["", "capa1_limpia", "capa2_ruidosa"])
    ap.add_argument("--concurrencia", type=int, default=2,
                    help="llamadas en paralelo. El nivel gratuito de Groq limita a 12k "
                         "tokens/minuto y cada extracción gasta ~2.8k: subirlo solo genera 429")
    ap.add_argument("--sin-cache", action="store_true")
    ap.add_argument("--fallos", action="store_true")
    ap.add_argument("--guardar", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

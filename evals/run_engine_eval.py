"""Evalúa el MOTOR de triage aislado, sin LLM y sin red.

Alimenta el motor con la trayectoria clínica real de los 160 casos y compara su
salida contra `label_ground_truth`. Corre en menos de un segundo y no gasta cuota.

Por qué separar esto de `run_triage_eval.py`. El sistema completo puede fallar por
dos motivos muy distintos: porque las reglas están mal, o porque el extractor leyó
mal al paciente. Medirlos juntos deja sin saber cuál arreglar. Esta evaluación fija
el techo: si el motor no clasifica bien con los valores exactos, ningún extractor,
por bueno que sea, lo va a salvar.

    python evals/run_engine_eval.py
    python evals/run_engine_eval.py --sin-moduladores
    python evals/run_engine_eval.py --fallos
"""

import argparse
import json
from datetime import date

import _bootstrap  # noqa: F401  (sys.path + UTF-8; tiene que ir primero)

from dataset import Caso, cargar_casos
from metricas import Resultado, imprimir, resumir

from app.config import get_settings
from app.triage.engine import evaluar
from app.triage.models import EstadoClinico, Variable
from app.store.patients import obtener_paciente


def estado_desde_trayectoria(caso: Caso) -> EstadoClinico:
    """Los valores exactos, con evidencia sintética para que pasen la validación.

    El motor exige `evidencia` porque en producción un valor sin cita del paciente
    no se puede verificar. Aquí la evidencia es la propia trayectoria: se declara
    como tal para que nadie confunda esta evaluación con una llamada real.
    """
    t = caso.trayectoria
    origen = f"trayectoria {t.trayectoria_id}"
    return EstadoClinico(
        dolor_nrs=Variable(valor=t.dolor_nrs, confianza=1.0, evidencia=origen),
        fiebre_c=Variable(valor=t.fiebre_c, confianza=1.0, evidencia=origen),
        movilidad=Variable(valor=t.movilidad, confianza=1.0, evidencia=origen),
        herida=Variable(valor=t.herida, confianza=1.0, evidencia=origen),
        apetito=Variable(valor=t.apetito, confianza=1.0, evidencia=origen),
        sueno=Variable(valor=t.sueno, confianza=1.0, evidencia=origen),
        fiebre_medida=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--con-moduladores", action="store_true",
                    help="fuerza los moduladores de riesgo (apagados por defecto)")
    ap.add_argument("--sin-moduladores", action="store_true")
    ap.add_argument("--fallos", action="store_true", help="lista cada caso fallado")
    ap.add_argument("--guardar", action="store_true", help="escribe evals/results/")
    args = ap.parse_args()

    # Una trayectoria es la misma en las dos capas: la capa solo cambia el ruido del
    # diálogo, no el estado clínico. Se evalúa una vez por caso.
    casos = [c for c in cargar_casos() if c.capa == "capa1_limpia"]
    # Por defecto se evalúa la configuración que se entrega, no una hipotética.
    moduladores = get_settings().triage_moduladores
    if args.con_moduladores:
        moduladores = True
    if args.sin_moduladores:
        moduladores = False

    resultados: list[Resultado] = []
    for caso in casos:
        paciente = obtener_paciente(caso.paciente_id)
        decision = evaluar(
            estado_desde_trayectoria(caso),
            comorbilidades=paciente.comorbilidades if paciente else [],
            dia_postop=caso.dia_postop,
            moduladores=moduladores,
        )
        resultados.append(
            Resultado(
                caso_id=caso.caso_id, capa="trayectoria",
                esperado=caso.etiqueta, obtenido=str(decision.nivel),
                score=decision.score,
                detalle={
                    "arquetipo": caso.trayectoria.arquetipo,
                    "dia_postop": caso.dia_postop,
                    "motivo": decision.motivo,
                    "red_flags": decision.red_flags,
                    "desglose": [r.como_dict() for r in decision.desglose],
                },
            )
        )

    resumen = resumir(resultados)
    etiqueta = "con moduladores" if moduladores else "sin moduladores"
    imprimir(resumen, f"Motor de triage sobre las trayectorias reales · {etiqueta}")

    print("\nDistribución de score por etiqueta:")
    for nivel in ("verde", "amarillo", "rojo"):
        scores = sorted(r.score for r in resultados if r.esperado == nivel)
        if scores:
            print(f"  {nivel:<9} min={scores[0]:>2}  mediana={scores[len(scores) // 2]:>2}  max={scores[-1]:>2}")

    fallos = [r for r in resultados if not r.acierta]
    if args.fallos and fallos:
        print(f"\n{len(fallos)} casos fallados:")
        for r in fallos:
            flecha = "sub-escala" if r.sub_escala else "sobre-escala"
            print(f"  {r.caso_id:<28} esperado={r.esperado:<9} obtenido={r.obtenido:<9} "
                  f"score={r.score:<3} {flecha}  [{r.detalle['arquetipo']}]")

    if args.guardar:
        salida = get_settings().dir_raiz / "evals" / "results"
        salida.mkdir(parents=True, exist_ok=True)
        ruta = salida / f"engine_{date.today():%Y%m%d}.json"
        ruta.write_text(
            json.dumps(
                {
                    "fecha": date.today().isoformat(),
                    "moduladores": moduladores,
                    "resumen": resumen,
                    "fallos": [
                        {"caso_id": r.caso_id, "esperado": r.esperado, "obtenido": r.obtenido,
                         "score": r.score, **r.detalle}
                        for r in fallos
                    ],
                },
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nGuardado en {ruta.relative_to(get_settings().dir_raiz)}")

    # El criterio de aprobación no es la exactitud: es no perder un solo rojo.
    return 0 if resumen["rojos_perdidos"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

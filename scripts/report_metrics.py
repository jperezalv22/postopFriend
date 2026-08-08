"""Genera la tabla de métricas del README. Nadie transcribe un número a mano.

    python scripts/report_metrics.py              # imprime, no toca nada
    python scripts/report_metrics.py --escribir   # sustituye el bloque del README
    python scripts/report_metrics.py --ruta ""    # sin filtrar por ruta del LLM

La rúbrica comprueba que «las métricas reportadas sean verificables en los logs y
concuerden con lo que ocurre en la sesión». La única forma de garantizarlo es que
la tabla del informe no se escriba: se genere. Este script sustituye lo que hay
entre `<!-- METRICS:START -->` y `<!-- METRICS:END -->` por lo que devuelve
`app/obs/metricas.py`, que es la misma función que alimenta el panel.

**Por defecto solo cuenta la ruta `groq`.** Mientras el plan de pago de Groq esté
cerrado, el desarrollo va por OpenRouter y la base tiene turnos de las dos. Una
cifra que las sume no describe la configuración que se entrega, así que la ruta
medida se imprime junto a la tabla en vez de quedar implícita.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path + UTF-8; tiene que ir primero)

from app.config import get_settings
from app.obs import metricas
from app.obs.tokens import FECHA_CONSULTA_PRECIOS, FUENTE_PRECIOS
from app.store import db

INICIO = "<!-- METRICS:START -->"
FIN = "<!-- METRICS:END -->"

#: Objetivo declarado en el plan. Se imprime al lado del dato medido para que la
#: comparación no dependa de que quien lee recuerde el número.
OBJETIVO_P50_MS = 1500


def _fila(etiqueta: str, valor: object, nota: str = "") -> str:
    return f"| {etiqueta} | {valor} | {nota} |"


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f} ms".replace(",", " ")


def _usd(v: float | None) -> str:
    if not v:
        return "US$ 0"
    return f"US$ {v:.6f}" if v < 0.01 else f"US$ {v:.4f}"


def bloque(m: dict, ruta: str | None) -> str:
    """El markdown que va entre los marcadores."""
    lat = m["latencia"]
    con = m["consumo"]
    cos = m["costo_usd"]
    lla = m["llamadas"]

    if not lat["n"] and not con["turnos_del_agente"]:
        return (
            f"*(sin datos para la ruta `{ruta or 'todas'}`. Se generan solos: corra "
            f"una llamada y vuelva a ejecutar `python scripts/report_metrics.py "
            f"--escribir`.)*"
        )

    etiqueta_ruta = f"`{ruta}`" if ruta else "todas las rutas"
    lineas = [
        f"Medido sobre **{lla['n']} llamadas** y **{con['turnos_del_agente']} turnos "
        f"del agente** por {etiqueta_ruta} "
        f"(`{', '.join(m['modelos_medidos']) or m['tarifa_aplicada']}`).",
        "",
        "| Métrica | Valor | Nota |",
        "|---|---:|---|",
        _fila("Latencia P50", _ms(lat["p50"]),
              f"objetivo {OBJETIVO_P50_MS} ms · fin de habla → primer audio, "
              f"reloj del navegador"),
        _fila("Latencia P95", _ms(lat["p95"]), f"máximo observado {_ms(lat['max'])}"),
        _fila("Turnos bajo 1.5 s",
              "—" if lat["bajo_objetivo"] is None else f"{lat['bajo_objetivo']:.0%}",
              f"{lat['n']} turnos medidos, {lat['turnos_sin_medir']} sin ACK del cliente"),
    ]

    for nombre, e in m["etapas_ms"].items():
        lineas.append(_fila(f"↳ etapa `{nombre}` P50", _ms(e["p50"]),
                            f"P95 {_ms(e['p95'])} · {e['n']} turnos"))

    lineas += [
        _fila("Tokens entrada / salida",
              f"{con['tokens_in']:,} / {con['tokens_out']:,}".replace(",", " "),
              "acumulados"),
        _fila("Invocaciones al LLM por turno",
              "—" if con["llm_calls_por_turno"] is None else con["llm_calls_por_turno"],
              "presupuesto declarado: 2 (extractor + generador)"),
        _fila("Consultas al corpus", con["rag_consultas"],
              "solo en turnos con pregunta clínica"),
        _fila("Costo por turno", _usd(cos["por_turno"]), ""),
        _fila("Costo por llamada", _usd(cos["por_llamada"]),
              f"{lla['turnos_por_llamada']} turnos de media"),
        _fila("Proyección 1 000 llamadas", f"US$ {cos['proyeccion_1000_llamadas']}",
              cos["supuesto"]),
        "",
        f"Tarifas de `{m['tarifa_aplicada']}` y `{get_settings().stt_model}`, "
        f"consultadas el {FECHA_CONSULTA_PRECIOS} en {FUENTE_PRECIOS}. "
        f"edge-tts no cobra.",
    ]

    if m["incidencias"]:
        detalle = " · ".join(f"`{k}` {v}" for k, v in m["incidencias"].items())
        lineas += ["", f"Incidencias registradas: {detalle}."]

    lineas += [
        "",
        f"<sub>Generado por `python scripts/report_metrics.py --escribir` desde "
        f"`data/postop.db`. No se edita a mano.</sub>",
    ]
    return "\n".join(lineas)


def escribir(ruta_readme: Path, contenido: str) -> bool:
    """Sustituye el bloque entre marcadores. Devuelve si hubo cambio."""
    texto = ruta_readme.read_text(encoding="utf-8")
    if INICIO not in texto or FIN not in texto:
        raise SystemExit(
            f"{ruta_readme.name} no tiene los marcadores {INICIO} … {FIN}. "
            "El script no adivina dónde va la tabla."
        )
    nuevo = re.sub(
        re.escape(INICIO) + r".*?" + re.escape(FIN),
        f"{INICIO}\n{contenido}\n{FIN}",
        texto,
        flags=re.DOTALL,
    )
    if nuevo == texto:
        return False
    ruta_readme.write_text(nuevo, encoding="utf-8")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ruta", default="groq",
                   help="ruta del LLM a medir; cadena vacía para no filtrar")
    p.add_argument("--escribir", action="store_true",
                   help="sustituye el bloque del README en vez de solo imprimirlo")
    p.add_argument("--json", action="store_true", help="vuelca el resumen crudo")
    args = p.parse_args()

    db.inicializar()
    ruta = args.ruta or None
    m = metricas.resumen(ruta_llm=ruta)

    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    contenido = bloque(m, ruta)
    if not args.escribir:
        print(contenido)
        print()
        print("— nada escrito. Añada --escribir para actualizar el README.")
        return 0

    readme = get_settings().dir_raiz / "README.md"
    cambio = escribir(readme, contenido)
    print(contenido)
    print()
    print(f"— README.md {'actualizado' if cambio else 'ya estaba al día'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

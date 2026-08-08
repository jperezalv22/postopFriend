"""Cuánta cuota de Groq queda ahora mismo, según el propio servidor.

Existe porque el nivel gratuito es la restricción que decide qué evaluaciones se
pueden correr antes del cierre, y hasta ahora los límites estaban **supuestos**
(12 000 TPM / 100 000 TPD, leídos de la documentación). Una planificación sobre un
número supuesto es exactamente la clase de cifra que este repo no admite.

Groq devuelve el estado real en las cabeceras `x-ratelimit-*` de cualquier
respuesta. Este script hace **una** llamada mínima (~30 tokens) y las imprime sin
interpretarlas de más: la ventana de cada límite se deduce del tiempo de reinicio
que informa el servidor —segundos es por minuto, horas es por día—, en vez de
asumir qué cabecera corresponde a qué.

    python scripts/cuota_groq.py                # tabla legible
    python scripts/cuota_groq.py --json         # para adjuntar al informe
    python scripts/cuota_groq.py --modelo llama-3.1-8b-instant
"""

import _bootstrap  # noqa: F401  (sys.path + UTF-8; debe ir primero)

import argparse
import json
import re
from datetime import datetime, timezone

from app.config import get_settings

# «2m59.56s», «7.66s», «1h30m», «120ms»
_DURACION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?"
                       r"(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def segundos(valor: str | None) -> float | None:
    """Convierte el formato de duración de Groq a segundos."""
    if not valor:
        return None
    m = _DURACION.fullmatch(valor.strip())
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def ventana(reset_s: float | None, limite: int | None, usados: int | None) -> tuple[str, float | None]:
    """Qué ventana cubre ese límite. Groq no la nombra, pero se deduce exacta.

    El cubo se rellena de forma continua, así que el tiempo que informa el servidor
    es lo que tarda en reponer **lo que se acaba de gastar**, no lo que falta para
    un corte de medianoche. Regla de tres:

        ventana = reset * limite / usados

    Con 1 petición de 1000 y reset 86.4 s da 86 400 s = un día exacto; con 37 tokens
    de 12 000 y reset 0.185 s da 60 s = un minuto exacto. No es una estimación.
    """
    if reset_s is None or not limite or not usados:
        return "?", None
    total_s = reset_s * limite / usados
    for etiqueta, referencia in (("por minuto", 60), ("por hora", 3600), ("por día", 86400)):
        if abs(total_s - referencia) <= referencia * 0.1:
            return etiqueta, total_s
    return f"~{total_s:,.0f} s", total_s


def sondear(modelo: str) -> dict:
    from groq import Groq

    s = get_settings()
    if not s.groq_api_key:
        raise SystemExit(
            "Falta GROQ_API_KEY. Copie .env.example a .env y ponga su clave.\n"
            "Consígala gratis en https://console.groq.com/keys"
        )

    cliente = Groq(api_key=s.groq_api_key, max_retries=0)
    # La llamada más barata que sigue devolviendo cabeceras de cuota: un token.
    cruda = cliente.chat.completions.with_raw_response.create(
        model=modelo,
        messages=[{"role": "user", "content": "ok"}],
        max_tokens=1,
        temperature=0,
    )
    cabeceras = {k.lower(): v for k, v in cruda.headers.items()
                 if k.lower().startswith("x-ratelimit")}
    uso = cruda.parse().usage
    return {
        "consultado": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "modelo": modelo,
        "costo_de_la_sonda_tokens": int(getattr(uso, "total_tokens", 0) or 0),
        "cabeceras": cabeceras,
    }


def filas(cabeceras: dict[str, str]) -> list[dict]:
    """Empareja limit/remaining/reset por recurso (requests, tokens)."""
    salida = []
    for recurso in ("requests", "tokens"):
        limite = cabeceras.get(f"x-ratelimit-limit-{recurso}")
        resta = cabeceras.get(f"x-ratelimit-remaining-{recurso}")
        reinicio = cabeceras.get(f"x-ratelimit-reset-{recurso}")
        if limite is None and resta is None:
            continue
        reset_s = segundos(reinicio)
        lim = int(limite) if limite and limite.isdigit() else None
        res = int(resta) if resta and resta.isdigit() else None
        usados = lim - res if lim is not None and res is not None else None
        etiqueta, ventana_s = ventana(reset_s, lim, usados)
        salida.append({
            "recurso": recurso,
            "limite": lim if lim is not None else limite,
            "restante": res if res is not None else resta,
            "reinicia_en": reinicio,
            "reinicia_en_s": reset_s,
            "ventana": etiqueta,
            "ventana_s": ventana_s,
        })
    return salida


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default="", help="por defecto, el declarado en config")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--por-llamada", type=int, default=2800,
                    help="tokens que gasta una extracción, para estimar cuántas caben")
    args = ap.parse_args()

    modelo = args.modelo or get_settings().llm_model
    datos = sondear(modelo)
    datos["limites"] = filas(datos["cabeceras"])

    if args.json:
        print(json.dumps(datos, indent=2, ensure_ascii=False))
        return 0

    print(f"\nCuota de Groq — {modelo}")
    print(f"consultado {datos['consultado']}  ·  la sonda gastó "
          f"{datos['costo_de_la_sonda_tokens']} tokens\n")

    if not datos["limites"]:
        print("  El servidor no devolvió cabeceras x-ratelimit. Salida cruda:")
        for k, v in sorted(datos["cabeceras"].items()):
            print(f"    {k}: {v}")
        return 1

    print(f"  {'recurso':<10} {'límite':>12} {'restante':>12} {'reinicia en':>14}   ventana")
    for f in datos["limites"]:
        print(f"  {f['recurso']:<10} {str(f['limite']):>12} {str(f['restante']):>12} "
              f"{str(f['reinicia_en']):>14}   {f['ventana']}")

    if args.por_llamada > 0:
        print(f"\n  Con {args.por_llamada:,} tokens por extracción:")
        for f in datos["limites"]:
            if not isinstance(f["limite"], int) or not f["ventana_s"]:
                continue
            por_ventana = (f["limite"] // args.por_llamada if f["recurso"] == "tokens"
                           else f["limite"])
            unidad = "extracciones" if f["recurso"] == "tokens" else "peticiones"
            print(f"    {f['recurso']:<9} {por_ventana:>6} {unidad} {f['ventana']}")
        tok = next((f for f in datos["limites"] if f["recurso"] == "tokens"), None)
        if tok and isinstance(tok["limite"], int) and tok["ventana"] == "por minuto":
            por_hora = tok["limite"] * 60 // args.por_llamada
            print(f"\n    Ritmo sostenido si no hay tope diario: ~{por_hora} "
                  f"extracciones/hora.")

    print("\n  Cuidado al planificar: estas cabeceras solo describen los cubos que "
          "Groq publica.\n  Un tope diario de tokens (TPD), si existe para esta cuenta, "
          "NO aparece aquí y solo\n  se ve en la página Limits de la consola o al "
          "chocar con él. Compruébelo antes de\n  prometer una evaluación completa.")

    print("\n  Cabeceras crudas:")
    for k, v in sorted(datos["cabeceras"].items()):
        print(f"    {k}: {v}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

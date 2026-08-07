"""Imprime el catálogo vivo de modelos de Groq. Es la evidencia de la compuerta G3.

El reto exige uno de 4 modelos permitidos y nombra `llama-3.1-70b-versatile`.
Ese modelo ya no existe en Groq. Este script lo demuestra contra la API real,
con fecha, en vez de pedirle al jurado que crea una afirmación del README.

    python scripts/check_models.py            # tabla legible
    python scripts/check_models.py --json     # salida cruda para adjuntar al informe
"""

import _bootstrap  # noqa: F401  (sys.path + UTF-8; debe ir primero)

import argparse
import json
from datetime import datetime, timezone

from app.config import get_settings

# Los 4 modelos que el kit oficial declara permitidos (ParticipantArtifacts,
# docs/stack-tecnico.md). El kit los nombra en prosa, no por su id de API, así que
# aquí se listan los ids con los que cada uno se serviría realmente.
PERMITIDOS_POR_EL_RETO = [
    ("Llama 3.1 70B (vía Groq)", ["llama-3.1-70b-versatile", "llama-3.1-70b"], "Groq"),
    ("Google Gemini 1.5 Flash", ["gemini-1.5-flash"], "Google (fuera de Groq)"),
    ("Llama 3.2 (1B / 3B)", ["llama-3.2-1b", "llama-3.2-3b"], "local vía Ollama"),
    ("Phi-3.5 Mini (3.8B)", ["phi-3.5-mini", "phi3.5"], "local vía Ollama"),
]


def catalogo_groq() -> list[dict]:
    from groq import Groq

    s = get_settings()
    if not s.groq_api_key:
        raise SystemExit(
            "Falta GROQ_API_KEY. Copie .env.example a .env y ponga su clave.\n"
            "Consígala gratis en https://console.groq.com/keys"
        )
    respuesta = Groq(api_key=s.groq_api_key).models.list()
    return sorted(
        ({"id": m.id, "owned_by": m.owned_by, "context": getattr(m, "context_window", None)}
         for m in respuesta.data),
        key=lambda m: m["id"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="salida JSON cruda")
    args = ap.parse_args()

    modelos = catalogo_groq()
    ids = {m["id"] for m in modelos}
    consulta_ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if args.json:
        print(json.dumps({"consultado": consulta_ts, "modelos": modelos}, indent=2))
        return 0

    s = get_settings()
    print(f"\nCatálogo vivo de Groq — consultado {consulta_ts}")
    print(f"{len(modelos)} modelos disponibles con esta API key\n")
    for m in modelos:
        ctx = f"  ctx={m['context']:,}" if m["context"] else ""
        print(f"  {m['id']:<48} {m['owned_by']}{ctx}")

    print("\n─── Compuerta G3: los 4 modelos que el reto nombra como permitidos ───")
    for nombre, candidatos, donde in PERMITIDOS_POR_EL_RETO:
        encontrado = next((c for c in candidatos if c in ids), None)
        if encontrado:
            marca = f"disponible como `{encontrado}`"
        elif donde != "Groq":
            marca = f"no aplica a este catálogo ({donde})"
        else:
            marca = "NO EXISTE en el catálogo de Groq"
        print(f"  [{'x' if encontrado else ' '}] {nombre:<28} {marca}")

    print(f"\n─── Modelo declarado por esta solución ───\n  {s.llm_model}", end="  ")
    print("(presente en el catálogo)" if s.llm_model in ids else "(AUSENTE — revisar)")
    print(f"  STT: {s.stt_model}", end="  ")
    print("(presente)" if s.stt_model in ids else "(AUSENTE — revisar)")
    print(
        "\nEl modelo declarado es el sucesor directo de `llama-3.1-70b-versatile`,\n"
        "del mismo fabricante (Meta) y en el mismo proveedor que el reto recomienda.\n"
        "Ver la sección «Cumplimiento G3» del README.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

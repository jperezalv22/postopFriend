"""Descarga las tipografías a `app/static/fonts/`.

Solo hace falta si un archivo se perdió: el repo ya los trae. Existe por el mismo
motivo que `vendorizar_voz.py` — que la interfaz no dependa de que la red del
jurado deje pasar un CDN— y para que quede escrito de dónde salió cada archivo,
que es lo que no se puede reconstruir mirando un `.woff2`.

    python scripts/vendorizar_fuentes.py            # baja lo que falte
    python scripts/vendorizar_fuentes.py --todo     # vuelve a bajarlo todo

Se bajan solo los subconjuntos `latin` y `latin-ext`: la interfaz es en español y
cirílico, griego y vietnamita triplicarían el peso sin pintar un solo glifo. Las
dos familias son variables, así que un archivo cubre todos los pesos.

Si Google publica una revisión nueva, los `unicode-range` que imprime este script
al final son los que hay que copiar al bloque 0 de `app/static/css/app.css`: si el
CSS declara un rango y el archivo trae otro, el navegador se descarga la fuente
para nada o deja letras sin pintar.
"""

import _bootstrap  # noqa: F401

import argparse
import re
import sys
import urllib.request
from pathlib import Path

from app.config import get_settings

DESTINO = get_settings().dir_raiz / "app" / "static" / "fonts"

# Un agente de escritorio: la API de Google devuelve `.ttf` a los clientes que no
# reconoce, y son cinco veces más pesados que el `.woff2`.
AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CSS = ("https://fonts.googleapis.com/css2"
       "?family=Outfit:wght@400..800"
       "&family=JetBrains+Mono:wght@400..700&display=swap")

SUBCONJUNTOS = {"latin", "latin-ext"}
ARCHIVO = {"Outfit": "outfit", "JetBrains Mono": "jetbrains-mono"}

# Tamaño mínimo por archivo. Misma idea que en `app/voice/vendor.py`: una página
# de error guardada con el nombre correcto pasa desapercibida hasta que el
# navegador no encuentra un solo glifo.
MINIMO_BYTES = 8_000


def bajar(url: str) -> bytes:
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(peticion, timeout=60) as respuesta:
        return respuesta.read()


def main() -> int:
    partes = argparse.ArgumentParser(description=__doc__)
    partes.add_argument("--todo", action="store_true",
                        help="vuelve a bajar incluso lo que ya está")
    args = partes.parse_args()

    DESTINO.mkdir(parents=True, exist_ok=True)
    hoja = bajar(CSS).decode("utf-8")

    # La hoja viene como un comentario con el nombre del subconjunto seguido de su
    # bloque `@font-face`.
    bloques = re.findall(r"/\* (\S+) \*/\s*@font-face \{(.*?)\}", hoja, re.S)
    if not bloques:
        print("no se pudo interpretar la hoja de Google Fonts", file=sys.stderr)
        return 1

    rangos: list[str] = []
    for subconjunto, cuerpo in bloques:
        if subconjunto not in SUBCONJUNTOS:
            continue
        familia = re.search(r"font-family: '([^']+)'", cuerpo).group(1)
        url = re.search(r"url\((https://[^)]+)\)", cuerpo).group(1)
        rango = re.search(r"unicode-range: ([^;]+);", cuerpo).group(1).strip()
        pesos = re.search(r"font-weight: ([^;]+);", cuerpo).group(1).strip()

        destino = DESTINO / f"{ARCHIVO[familia]}-{subconjunto}.woff2"
        rangos.append(f"  {destino.name}\n    unicode-range: {rango};")

        if destino.exists() and not args.todo:
            print(f"ya está   {destino.name}  ({destino.stat().st_size:,} bytes)")
            continue

        datos = bajar(url)
        if len(datos) < MINIMO_BYTES:
            print(f"FALLA     {destino.name}: solo {len(datos):,} bytes, "
                  f"no parece una fuente", file=sys.stderr)
            return 1
        destino.write_bytes(datos)
        print(f"bajado    {destino.name}  ({len(datos):,} bytes · pesos {pesos})")

    total = sum(p.stat().st_size for p in DESTINO.glob("*.woff2"))
    print(f"\n{len(list(DESTINO.glob('*.woff2')))} archivos · {total:,} bytes en total")
    print("\nRangos que debe declarar app/static/css/app.css:")
    print("\n".join(rangos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

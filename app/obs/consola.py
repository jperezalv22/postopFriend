"""Salida de consola que no se cae por una tilde.

La consola de Windows usa cp1252. Cualquier `print` con una tilde, una flecha o un
carácter de caja levanta `UnicodeEncodeError` y mata el proceso — y todo lo que
imprime este proyecto está en español y usa líneas `─` para las tablas.

Eso no es un detalle cosmético: es la compuerta G2. Un script que se cae con el
jurado mirando es un script que no funciona, aunque el cálculo de dentro fuera
perfecto. Y falla **después** de hacer el trabajo, que es la peor forma de fallar:
parece que el problema está en el cálculo.

Vive en `app/obs/` y no duplicado en `scripts/` y `evals/` porque dos copias de la
misma corrección terminan divergiendo, y la que se quede vieja fallará justo en la
máquina donde no se probó.
"""

from __future__ import annotations

import sys


def preparar() -> None:
    """Fuerza UTF-8 en stdout y stderr. Idempotente y silenciosa si no puede.

    `errors="replace"` en vez de dejar que reviente: si algún día aparece un
    carácter que ni UTF-8 resuelve, se ve un signo raro en pantalla en lugar de
    perder toda la salida.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Flujo redirigido, ya cerrado, o un objeto que no es un TextIOWrapper
            # (pytest sustituye stdout). No hay nada que arreglar en esos casos.
            pass

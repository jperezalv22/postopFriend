"""Preámbulo común a todos los scripts. Importar primero, antes que nada de `app`.

Hace dos cosas, las dos imprescindibles en Windows:

1. Pone la raíz del repo en `sys.path` para que `import app...` funcione al correr
   `python scripts/loquesea.py` sin instalar el paquete.
2. Fuerza UTF-8 en stdout/stderr. La consola de Windows usa cp1252 y revienta con
   `UnicodeEncodeError` ante una tilde o un carácter de caja. Un script que se cae
   con el jurado mirando es un fallo de la compuerta G2, no un detalle cosmético.

Gemelo de `evals/_bootstrap.py`: los dos tienen que existir por separado porque son
lo que hace importable `app`. Lo que sí está en un solo sitio es el comportamiento
—qué encoding, qué política de errores— en `app/obs/consola.py`.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.obs.consola import preparar  # noqa: E402  (la raíz tiene que estar antes)

preparar()

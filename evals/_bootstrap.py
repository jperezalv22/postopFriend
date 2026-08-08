"""Preámbulo de las evaluaciones. Importar primero, antes que nada de `app`.

Gemelo de `scripts/_bootstrap.py`. Las dos hacen lo mismo y por la misma razón, y
las dos tienen que existir por separado: son lo que pone la raíz del repo en
`sys.path`, así que no pueden importarse la una a la otra ni vivir dentro de `app`.

Lo único que no se duplica es lo que de verdad importa —qué encoding y qué política
de errores— que está en `app/obs/consola.py`. Ver ese archivo para el porqué.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.obs.consola import preparar  # noqa: E402  (la raíz tiene que estar antes)

preparar()

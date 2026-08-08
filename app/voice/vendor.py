"""Inventario de lo que la voz necesita servir desde `/static/vendor/`.

Todo va empaquetado en el repo: si el jurado no tiene red, o la red del sitio
bloquea los CDN, la llamada tiene que funcionar igual. Eso obliga a mantener a
mano una lista que normalmente resolvería un empaquetador de JavaScript, y a
comprobarla, porque un archivo que falta aquí no rompe nada en Python: rompe en
el navegador, en mitad de la demostración.

Fue justo lo que pasó con `ort-wasm-simd-threaded.mjs`. Desde onnxruntime-web
1.19 el `.wasm` no se carga solo; el bundle importa un módulo de pegamento en
tiempo de ejecución. Al no estar, el VAD caía a «pulsar para hablar» y la
llamada dejaba de ser en tiempo real.

**Quién manda en la versión: `vad.bundle.min.js`.** El bundle del VAD no usa
`window.ort`: trae su propia copia de onnxruntime empotrada, y es esa copia la
que importa el `.mjs` y carga el `.wasm`. Así que la versión no la elegimos
nosotros, la impone el bundle — hay que leerla de dentro y servir el par que
pide. Servir otra da errores que no se parecen en nada a un problema de
versiones: con el 1.19.2 el fallo era «t.getValue is not a function», porque ese
`.mjs` no exporta `getValue` y el ORT de dentro del bundle lo llama.

**Aquí no va `ort.wasm.min.js`.** Es la distribución suelta de onnxruntime, y
con ella cargada el VAD no arranca: dos runtimes compitiendo por el mismo wasm.
Se comprobó quitándola (`/static/vad_debug.html`) y MicVAD arrancó. El VAD no la
necesita para nada, así que no se sirve.
"""

from __future__ import annotations

import re
from pathlib import Path

#: La impone `vad.bundle.min.js`, no se elige. Verificado por
#: `version_de_ort_en_el_bundle()` y por las pruebas.
VERSION_ONNXRUNTIME = "1.22.0"
VERSION_VAD = "0.0.30"

#: Archivo → (bytes mínimos esperados, para qué sirve).
#: El tamaño mínimo detecta descargas truncadas y páginas de error de 404 bytes
#: guardadas con el nombre correcto, que es el fallo que más despista.
REQUERIDOS: dict[str, tuple[int, str]] = {
    "ort-wasm-simd-threaded.mjs": (
        15_000,
        "pegamento del wasm; el ORT de dentro del bundle del VAD lo importa",
    ),
    "ort-wasm-simd-threaded.wasm": (5_000_000, "el binario del runtime"),
    "vad.bundle.min.js": (50_000, "@ricky0123/vad-web: la API MicVAD"),
    "vad.worklet.bundle.min.js": (1_500, "el AudioWorklet que trocea el micrófono"),
    "silero_vad_v5.onnx": (2_000_000, "el modelo que decide si hay voz"),
    "silero_vad_legacy.onnx": (1_500_000, "respaldo si el v5 no carga"),
}


def directorio() -> Path:
    from app.config import get_settings

    return get_settings().dir_raiz / "app" / "static" / "vendor"


def version_de_ort_en_el_bundle(base: Path | None = None) -> str | None:
    """Lee la versión de onnxruntime que `vad.bundle.min.js` lleva empotrada.

    Es la única versión que vale: el bundle ignora `window.ort` y usa su copia.
    Devuelve `None` si no se encuentra, porque un empaquetado futuro podría dejar
    de incluir la cadena y eso no debe hacer fallar el arranque, solo la prueba.
    """
    base = base or directorio()
    ruta = base / "vad.bundle.min.js"
    if not ruta.is_file():
        return None
    texto = ruta.read_text(encoding="utf-8", errors="ignore")
    # onnxruntime declara su versión como una cadena "1.22.0" dentro del bundle.
    candidatas = sorted({v for v in re.findall(r'"(\d+\.\d+\.\d+)"', texto) if v.startswith("1.")})
    return candidatas[0] if candidatas else None


def faltantes(base: Path | None = None) -> list[str]:
    """Devuelve los archivos ausentes o sospechosamente pequeños, con el motivo."""
    base = base or directorio()
    problemas: list[str] = []
    for nombre, (minimo, para_que) in REQUERIDOS.items():
        ruta = base / nombre
        if not ruta.is_file():
            problemas.append(f"{nombre} — falta ({para_que})")
        elif ruta.stat().st_size < minimo:
            real = ruta.stat().st_size
            # Sin «≥»: este texto acaba en consolas cp1252 que no saben imprimirlo.
            problemas.append(
                f"{nombre} - {real} bytes, se esperaban {minimo} o mas (descarga truncada)"
            )
    return problemas

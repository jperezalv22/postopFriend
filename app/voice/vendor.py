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

Las versiones están fijadas porque el pegamento y el `.wasm` tienen que ser de
la misma: mezclarlas da errores de memoria imposibles de leer.
"""

from __future__ import annotations

from pathlib import Path

VERSION_ONNXRUNTIME = "1.19.2"
VERSION_VAD = "0.0.30"

#: Archivo → (bytes mínimos esperados, para qué sirve).
#: El tamaño mínimo detecta descargas truncadas y páginas de error de 404 bytes
#: guardadas con el nombre correcto, que es el fallo que más despista.
REQUERIDOS: dict[str, tuple[int, str]] = {
    "ort.wasm.min.js": (40_000, "onnxruntime-web: el runtime que ejecuta el modelo"),
    "ort-wasm-simd-threaded.mjs": (
        20_000,
        "pegamento del wasm; el bundle lo importa dinámicamente",
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

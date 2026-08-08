"""Descarga los recursos de voz a `app/static/vendor/`.

Solo hace falta si un archivo se perdió o se corrompió: el repo ya los trae. Se
descargan una vez y se versionan, porque la llamada no puede depender de que la
red del jurado deje pasar un CDN.

    python scripts/vendorizar_voz.py            # baja lo que falte
    python scripts/vendorizar_voz.py --todo     # vuelve a bajarlo todo

Las versiones se fijan en `app/voice/vendor.py`. El pegamento `.mjs` y el
`.wasm` tienen que venir de la misma versión de onnxruntime-web: mezclarlas
produce fallos de memoria en el navegador que no dicen nada útil.
"""

import _bootstrap  # noqa: F401

import argparse
import sys
import urllib.request
from pathlib import Path

from app.voice import vendor

NPM = "https://cdn.jsdelivr.net/npm"
ORT = f"{NPM}/onnxruntime-web@{vendor.VERSION_ONNXRUNTIME}/dist"
VAD = f"{NPM}/@ricky0123/vad-web@{vendor.VERSION_VAD}/dist"

ORIGEN: dict[str, str] = {
    # Sin `ort.wasm.min.js`: con esa copia suelta cargada el VAD no arranca.
    "ort-wasm-simd-threaded.mjs": f"{ORT}/ort-wasm-simd-threaded.mjs",
    "ort-wasm-simd-threaded.wasm": f"{ORT}/ort-wasm-simd-threaded.wasm",
    "vad.bundle.min.js": f"{VAD}/bundle.min.js",
    "vad.worklet.bundle.min.js": f"{VAD}/vad.worklet.bundle.min.js",
    "silero_vad_v5.onnx": f"{VAD}/silero_vad_v5.onnx",
    "silero_vad_legacy.onnx": f"{VAD}/silero_vad_legacy.onnx",
}


def descargar(url: str, destino: Path, minimo: int) -> None:
    with urllib.request.urlopen(url, timeout=120) as r:
        datos = r.read()
    if len(datos) < minimo:
        # Un CDN puede responder 200 con una página de error. Se comprueba antes
        # de escribir para no dejar un archivo que parece bueno y no lo es.
        raise OSError(f"{url} devolvió {len(datos)} bytes, se esperaban ≥{minimo}")
    destino.write_bytes(datos)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="rebaja también lo que ya está")
    args = ap.parse_args()

    base = vendor.directorio()
    base.mkdir(parents=True, exist_ok=True)
    print(f"\nDestino: {base}")
    print(f"onnxruntime-web {vendor.VERSION_ONNXRUNTIME} · vad-web {vendor.VERSION_VAD}\n")

    fallos = 0
    for nombre, (minimo, para_que) in vendor.REQUERIDOS.items():
        ruta = base / nombre
        if ruta.is_file() and ruta.stat().st_size >= minimo and not args.todo:
            print(f"  ya está   {nombre}")
            continue
        try:
            descargar(ORIGEN[nombre], ruta, minimo)
            print(f"  bajado    {nombre}  ({ruta.stat().st_size:,} bytes) · {para_que}")
        except Exception as e:
            fallos += 1
            print(f"  FALLO     {nombre}: {type(e).__name__}: {e}")

    problemas = vendor.faltantes(base)
    if problemas or fallos:
        print("\nQuedan problemas:")
        for p in problemas:
            print(f"  · {p}")
        return 1
    print("\nCompleto. Compruébelo en http://127.0.0.1:8000/static/voice_check.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())

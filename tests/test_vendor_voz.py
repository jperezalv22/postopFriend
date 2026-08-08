"""Los recursos que el navegador necesita están completos y bien servidos.

Estas pruebas existen por un fallo real: faltaba `ort-wasm-simd-threaded.mjs` y
nada lo detectó hasta abrir la página de comprobación en el navegador. Las 161
pruebas pasaban, `doctor.py` decía que todo estaba bien y el VAD no arrancaba.

Se comprueba el repo tal y como se entrega, no una copia temporal: lo que falla
en la demostración es el archivo que el jurado clona.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.voice import vendor


def test_no_falta_ningun_recurso_de_voz():
    problemas = vendor.faltantes()
    assert not problemas, "arréglelo con `python scripts/vendorizar_voz.py`:\n" + "\n".join(
        problemas
    )


def test_el_servidor_entrega_cada_recurso_con_su_tipo():
    """Un `.mjs` servido como text/plain lo rechaza el navegador por MIME estricto.

    En Windows el tipo sale del registro, así que esto puede pasar en la máquina
    del jurado y no en la de desarrollo. Por eso se fija en `app.main`.
    """
    esperado = {
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".wasm": "application/wasm",
    }
    with TestClient(app) as cliente:
        for nombre in vendor.REQUERIDOS:
            r = cliente.get(f"/static/vendor/{nombre}")
            assert r.status_code == 200, f"{nombre} devolvió {r.status_code}"
            sufijo = "." + nombre.rsplit(".", 1)[1]
            if sufijo in esperado:
                tipo = r.headers["content-type"].split(";")[0]
                assert tipo == esperado[sufijo], f"{nombre} se sirvió como {tipo}"


def test_el_pegamento_y_el_wasm_son_de_la_misma_version():
    """Mezclar versiones de onnxruntime da errores de memoria ilegibles."""
    mjs = (vendor.directorio() / "ort-wasm-simd-threaded.mjs").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "ortWasmThreaded" in mjs, "no parece el pegamento de onnxruntime"

    js = (vendor.directorio() / "ort.wasm.min.js").read_text(encoding="utf-8", errors="ignore")
    assert vendor.VERSION_ONNXRUNTIME in js, (
        f"ort.wasm.min.js no declara {vendor.VERSION_ONNXRUNTIME}; "
        "el .mjs se bajó fijado a esa versión"
    )


def test_las_paginas_de_voz_cargan_el_runtime_antes_que_el_vad():
    """El bundle del VAD lee `window.ort` al importarse: el orden importa."""
    estaticos = vendor.directorio().parent
    for pagina in ("call.html", "voice_check.html"):
        html = (estaticos / pagina).read_text(encoding="utf-8")
        assert html.index("ort.wasm.min.js") < html.index("vad.bundle.min.js"), (
            f"{pagina} carga el VAD antes que onnxruntime"
        )

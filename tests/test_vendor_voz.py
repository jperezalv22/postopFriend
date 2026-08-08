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


def test_la_version_servida_es_la_que_el_bundle_del_vad_lleva_dentro():
    """El bundle del VAD no usa `window.ort`: trae su propio onnxruntime empotrado.

    Esa copia es la que importa el `.mjs` y carga el `.wasm`, así que la versión
    la impone el bundle y no nosotros. Servir otra falla con mensajes que no
    mencionan versiones: con el 1.19.2 el error era «t.getValue is not a
    function», porque ese `.mjs` no exporta `getValue`.
    """
    dentro = vendor.version_de_ort_en_el_bundle()
    assert dentro is not None, "no se pudo leer la versión de ORT en vad.bundle.min.js"
    assert dentro == vendor.VERSION_ONNXRUNTIME, (
        f"vad.bundle.min.js trae onnxruntime {dentro} pero se sirve "
        f"{vendor.VERSION_ONNXRUNTIME}. Ajuste VERSION_ONNXRUNTIME y ejecute "
        "`python scripts/vendorizar_voz.py --todo`."
    )


def test_el_pegamento_exporta_lo_que_el_runtime_le_pide():
    """`getValue` es la función cuya ausencia rompía el arranque del VAD."""
    mjs = (vendor.directorio() / "ort-wasm-simd-threaded.mjs").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "ortWasmThreaded" in mjs, "no parece el pegamento de onnxruntime"
    assert "getValue" in mjs, (
        "el .mjs no expone getValue: es de una versión anterior a la que pide el VAD"
    )


def test_no_hay_dos_versiones_de_onnxruntime_en_juego():
    """Dos ORT distintos en el mismo proyecto es lo que causó la confusión."""
    js = (vendor.directorio() / "ort.wasm.min.js").read_text(encoding="utf-8", errors="ignore")
    assert vendor.VERSION_ONNXRUNTIME in js, (
        f"ort.wasm.min.js no declara {vendor.VERSION_ONNXRUNTIME}"
    )


def test_las_paginas_de_voz_cargan_el_runtime_antes_que_el_vad():
    """El bundle del VAD lee `window.ort` al importarse: el orden importa."""
    estaticos = vendor.directorio().parent
    for pagina in ("call.html", "voice_check.html"):
        html = (estaticos / pagina).read_text(encoding="utf-8")
        assert html.index("ort.wasm.min.js") < html.index("vad.bundle.min.js"), (
            f"{pagina} carga el VAD antes que onnxruntime"
        )

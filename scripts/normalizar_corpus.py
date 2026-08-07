"""Normaliza `dataset/textos/` y escribe el manifiesto del corpus.

Por qué existe. El kit oficial trae 37 archivos con nombres de hasta 232 caracteres
y dos carpetas con espacios. En Windows eso hace fallar `git add` con
«Filename too long», y un jurado que clone el repo en Windows se queda con el
corpus incompleto: compuerta G2 caída por un motivo ajeno a la solución.

Qué hace. Renombra carpetas y archivos a slugs cortos y estables, y guarda el
título original en `dataset/textos/manifiesto.json`. Las citas siguen mostrando el
título real del documento; solo cambia el nombre en disco.

Es idempotente: correrlo dos veces no cambia nada.

    python scripts/normalizar_corpus.py            # aplica los cambios
    python scripts/normalizar_corpus.py --simular  # muestra qué haría, sin tocar nada
"""

import _bootstrap  # noqa: F401

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from app.config import get_settings

LARGO_MAX_SLUG = 56

# carpeta del kit → (carpeta normalizada, modulo_synthea, procedimiento del dataset)
CARPETAS = {
    "Appendicitis": ("apendicitis", "appendicitis", "Apendicectomía"),
    "cholecystitis": ("colecistitis", "cholecystitis", "Colecistectomía"),
    "colorectal cancer": ("cancer_colorrectal", "colorectal_cancer", "Colectomía"),
    "total joint replacement": (
        "reemplazo_articular",
        "total_joint_replacement",
        "Reemplazo de cadera/rodilla",
    ),
    "breast_cancer": ("cancer_mama", "breast_cancer", "Mastectomía"),
}

# Hallazgo verificado: la carpeta `breast_cancer` del kit no contiene un solo
# documento sobre cáncer de mama ni sobre mastectomía. Los 19 PDFs tratan de
# cáncer de cuello uterino. Se conserva el nombre original como procedencia, pero
# el procedimiento efectivo para el buscador es "general": si se etiquetaran como
# Mastectomía, las preguntas de un paciente mastectomizado harían emerger
# documentos de cérvix y el agente respondería con la fuente equivocada.
# Es preferible que declare que no sabe. Ver README §Límites del corpus.
CARPETAS_SIN_CORPUS_PROPIO = {"breast_cancer"}


def slug(texto: str, largo: int = LARGO_MAX_SLUG) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^a-zA-Z0-9]+", "-", sin_tildes).strip("-").lower()
    limpio = re.sub(r"-{2,}", "-", limpio)
    if len(limpio) > largo:  # cortar en frontera de palabra
        limpio = limpio[:largo].rsplit("-", 1)[0]
    return limpio or "documento"


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true", help="no modifica nada")
    args = ap.parse_args()

    s = get_settings()
    raiz = s.dir_textos
    if not raiz.is_dir():
        raise SystemExit(f"No existe {raiz}. Copie dataset/ del kit oficial a la raíz del repo.")

    ruta_manifiesto = raiz / "manifiesto.json"
    previo = {}
    if ruta_manifiesto.exists():
        previo = {d["archivo"]: d for d in json.loads(ruta_manifiesto.read_text("utf-8"))["documentos"]}

    documentos: list[dict] = []
    renombrados = 0

    for original, (destino, modulo, procedimiento) in CARPETAS.items():
        dir_origen = raiz / original
        dir_destino = raiz / destino
        if dir_origen.is_dir() and dir_origen != dir_destino:
            print(f"carpeta  {original}/  →  {destino}/")
            if not args.simular:
                dir_origen.rename(dir_destino)

        # En simulación la carpeta aún no se movió: se trabaja sobre la de origen.
        dir_trabajo = dir_destino if dir_destino.is_dir() else dir_origen
        if not dir_trabajo.is_dir():
            print(f"  aviso: no se encontró {original}/ ni {destino}/")
            continue

        usados: set[str] = set()
        for i, archivo in enumerate(sorted(dir_trabajo.iterdir()), start=1):
            if not archivo.is_file():
                continue

            # El título original se recupera del manifiesto si el archivo ya fue
            # renombrado en una corrida anterior; si no, es el nombre en disco.
            rel_actual = f"{destino}/{archivo.name}"
            titulo = previo.get(rel_actual, {}).get("titulo") or archivo.stem

            base = f"{i:03d}-{slug(titulo)}"
            nombre_nuevo = f"{base}{archivo.suffix.lower()}"
            while nombre_nuevo in usados:
                base += "-b"
                nombre_nuevo = f"{base}{archivo.suffix.lower()}"
            usados.add(nombre_nuevo)

            ruta_nueva = dir_trabajo / nombre_nuevo
            if archivo.name != nombre_nuevo:
                renombrados += 1
                if args.simular:
                    print(f"  {archivo.name[:70]:<70} → {nombre_nuevo}")
                else:
                    archivo.rename(ruta_nueva)
            else:
                ruta_nueva = archivo

            documentos.append(
                {
                    "archivo": f"{destino}/{nombre_nuevo}",
                    "titulo": titulo,
                    "carpeta": destino,
                    "carpeta_kit": original,
                    "modulo_synthea": modulo,
                    "procedimiento_declarado": procedimiento,
                    # Lo que realmente usa el buscador para dar el boost por procedimiento.
                    "procedimiento": "general" if original in CARPETAS_SIN_CORPUS_PROPIO else procedimiento,
                    "bytes": (archivo if args.simular else ruta_nueva).stat().st_size,
                    "sha256_archivo": sha256(archivo if args.simular else ruta_nueva),
                }
            )

    manifiesto = {
        "generado_por": "scripts/normalizar_corpus.py",
        "total_documentos": len(documentos),
        "nota_cancer_mama": (
            "La carpeta `breast_cancer` del kit oficial contiene 19 documentos sobre "
            "cáncer de cuello uterino, ninguno sobre mama ni mastectomía. Los 8 pacientes "
            "con Mastectomía quedan sin corpus propio: el agente debe declarar ese límite "
            "en vez de responder con la fuente equivocada."
        ),
        "documentos": sorted(documentos, key=lambda d: d["archivo"]),
    }

    if args.simular:
        print(f"\n[simulación] {renombrados} archivo(s) se renombrarían · {len(documentos)} documentos")
        return 0

    ruta_manifiesto.write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    largo_max = max((len(d["archivo"]) for d in documentos), default=0)
    print(f"{renombrados} archivo(s) renombrado(s) · {len(documentos)} documentos en el manifiesto")
    print(f"Ruta relativa más larga ahora: {largo_max} caracteres")
    print(f"Manifiesto: {ruta_manifiesto.relative_to(s.dir_raiz)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

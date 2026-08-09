"""Genera docs/informe-final.docx a partir de docs/informe-final.md.

Uso:
    python scripts/build_informe_docx.py

Construye (si no existe) docs/templates/reference.docx con un estilo formal y
minimalista, y llama a Pandoc para convertir el Markdown fuente heredando esos
estilos. El Markdown sigue siendo la fuente de verdad; el .docx es un artefacto
derivado y se puede regenerar en cualquier momento con este mismo comando.
"""

import _bootstrap  # noqa: F401  (sys.path + UTF-8; debe ir primero)

import shutil
import subprocess
import sys
from pathlib import Path

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

RAIZ = Path(__file__).resolve().parent.parent
MD = RAIZ / "docs" / "informe-final.md"
PLANTILLA = RAIZ / "docs" / "templates" / "reference.docx"
SALIDA = RAIZ / "docs" / "informe-final.docx"

TINTA = RGBColor(0x1F, 0x23, 0x28)
ACENTO = RGBColor(0x26, 0x37, 0x4A)
ACENTO_SUAVE = RGBColor(0x55, 0x60, 0x6E)
GRIS = RGBColor(0x6B, 0x72, 0x80)
BORDE = "C9CED6"
FUENTE = "Cambria"
MONO = "Consolas"


def _fuente(style, nombre=FUENTE, tam=None, negrita=None, cursiva=None, color=None):
    f = style.font
    f.name = nombre
    if tam is not None:
        f.size = Pt(tam)
    if negrita is not None:
        f.bold = negrita
    if cursiva is not None:
        f.italic = cursiva
    if color is not None:
        f.color.rgb = color
    # Fuerza la fuente también para texto East Asian / complex script,
    # que Word a veces resuelve aparte y deja el estilo a medias.
    rpr = style.element.get_or_add_rPr()
    rFonts = rpr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rFonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rFonts.set(qn(attr), nombre)


def _parrafo(style, antes=None, despues=None, interlineado=None, alineacion=None):
    pf = style.paragraph_format
    if antes is not None:
        pf.space_before = Pt(antes)
    if despues is not None:
        pf.space_after = Pt(despues)
    if interlineado is not None:
        pf.line_spacing = interlineado
    if alineacion is not None:
        pf.alignment = alineacion


def _sombreado(style, color_hex):
    pPr = style.element.get_or_add_pPr()
    shd = pPr.makeelement(
        qn("w:shd"), {qn("w:val"): "clear", qn("w:color"): "auto", qn("w:fill"): color_hex}
    )
    pPr.append(shd)


def _borde_inferior(style, color=BORDE, grosor=6):
    """Agrega un filete inferior al párrafo del estilo (para separar secciones)."""
    pPr = style.element.get_or_add_pPr()
    pBdr = pPr.makeelement(qn("w:pBdr"), {})
    bottom = pPr.makeelement(
        qn("w:bottom"),
        {qn("w:val"): "single", qn("w:sz"): str(grosor), qn("w:space"): "4", qn("w:color"): color},
    )
    pBdr.append(bottom)
    pPr.append(pBdr)


def construir_plantilla() -> None:
    # Siempre parte del reference.docx de fábrica de Pandoc: aplicar el estilo sobre
    # una copia ya estilizada duplicaría bordes y tamaños en cada corrida.
    PLANTILLA.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["pandoc", "-o", str(PLANTILLA), "--print-default-data-file", "reference.docx"],
        check=True,
    )

    d = docx.Document(str(PLANTILLA))

    for section in d.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.8)
        section.right_margin = Cm(2.8)

    normal = d.styles["Normal"]
    _fuente(normal, tam=11, color=TINTA)
    _parrafo(normal, despues=8, interlineado=1.3, alineacion=WD_ALIGN_PARAGRAPH.JUSTIFY)

    titulo = d.styles["Title"]
    _fuente(titulo, tam=26, negrita=True, color=ACENTO)
    _parrafo(titulo, antes=0, despues=4, alineacion=WD_ALIGN_PARAGRAPH.CENTER)

    subtitulo = d.styles["Subtitle"]
    _fuente(subtitulo, tam=13, cursiva=True, color=ACENTO_SUAVE)
    _parrafo(subtitulo, antes=0, despues=18, alineacion=WD_ALIGN_PARAGRAPH.CENTER)

    autor = d.styles["Author"]
    _fuente(autor, tam=12, negrita=True, color=ACENTO_SUAVE)
    _parrafo(autor, antes=0, despues=2, alineacion=WD_ALIGN_PARAGRAPH.CENTER)

    fecha = d.styles["Date"]
    _fuente(fecha, tam=10.5, color=GRIS)
    _parrafo(fecha, antes=0, despues=28, alineacion=WD_ALIGN_PARAGRAPH.CENTER)
    _borde_inferior(fecha)

    h1 = d.styles["Heading 1"]
    _fuente(h1, tam=16, negrita=True, color=ACENTO)
    _parrafo(h1, antes=26, despues=8, interlineado=1.0)
    _borde_inferior(h1, grosor=4)

    h2 = d.styles["Heading 2"]
    _fuente(h2, tam=13, negrita=True, color=ACENTO)
    _parrafo(h2, antes=16, despues=6, interlineado=1.0)

    h3 = d.styles["Heading 3"]
    _fuente(h3, tam=11.5, negrita=True, cursiva=True, color=ACENTO_SUAVE)
    _parrafo(h3, antes=12, despues=4, interlineado=1.0)

    tabla = d.styles["Table"]
    _fuente(tabla, tam=10, color=TINTA)
    _parrafo(tabla, antes=0, despues=0, interlineado=1.15)

    for nombre in ("Verbatim Char",):
        try:
            _fuente(d.styles[nombre], nombre=MONO, tam=9.5, color=ACENTO)
        except KeyError:
            pass

    # Pandoc sintetiza "Source Code" en la marcha si no lo encuentra aquí, y ese
    # estilo sintético hereda la justificación serif de Normal — ilegible para
    # código. Se define explícito para que el bloque de código quede monoespaciado,
    # alineado a la izquierda y con una caja gris clara detrás.
    try:
        codigo = d.styles["Source Code"]
    except KeyError:
        codigo = d.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
        codigo.base_style = normal
    _fuente(codigo, nombre=MONO, tam=9.5, color=ACENTO)
    _parrafo(codigo, antes=2, despues=2, interlineado=1.15, alineacion=WD_ALIGN_PARAGRAPH.LEFT)
    _sombreado(codigo, "F2F3F5")

    d.save(str(PLANTILLA))


def convertir() -> None:
    if not shutil.which("pandoc"):
        sys.exit("Pandoc no está instalado o no está en el PATH.")
    subprocess.run(
        [
            "pandoc",
            str(MD),
            "-o",
            str(SALIDA),
            f"--reference-doc={PLANTILLA}",
        ],
        check=True,
        cwd=str(RAIZ),
    )


if __name__ == "__main__":
    construir_plantilla()
    convertir()
    print(f"Generado: {SALIDA}")

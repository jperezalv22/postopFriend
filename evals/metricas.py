"""Matriz de confusión y métricas de triage, en una sola implementación.

Las usan las tres evaluaciones. Lo que importa aquí no es la exactitud global sino
el **recall de rojo** y los **falsos negativos**: el reto declara explícitamente que
no alertar cuando tocaba pesa más que alertar de más, y una exactitud del 92 % con
un rojo perdido es peor que un 85 % sin ninguno.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NIVELES = ("verde", "amarillo", "rojo")
GRAVEDAD = {"verde": 0, "amarillo": 1, "rojo": 2, "indeterminado": 1}


@dataclass
class Resultado:
    caso_id: str
    capa: str
    esperado: str
    obtenido: str
    score: int = 0
    detalle: dict[str, Any] = field(default_factory=dict)

    @property
    def acierta(self) -> bool:
        return self.esperado == self.obtenido

    @property
    def sub_escala(self) -> bool:
        """Predijo menos grave de lo que era. El error que la rúbrica castiga."""
        return GRAVEDAD.get(self.obtenido, 1) < GRAVEDAD.get(self.esperado, 1)

    @property
    def sobre_escala(self) -> bool:
        return GRAVEDAD.get(self.obtenido, 1) > GRAVEDAD.get(self.esperado, 1)


def matriz(resultados: list[Resultado]) -> dict[str, dict[str, int]]:
    m = {e: {o: 0 for o in NIVELES} for e in NIVELES}
    for r in resultados:
        if r.esperado in m:
            # `indeterminado` no debería salir del motor al evaluar; si sale, se
            # cuenta como amarillo, que es como se comporta al escalar.
            obtenido = r.obtenido if r.obtenido in NIVELES else "amarillo"
            m[r.esperado][obtenido] += 1
    return m


def recall(resultados: list[Resultado], nivel: str) -> tuple[int, int]:
    """(aciertos, total) para un nivel esperado."""
    total = [r for r in resultados if r.esperado == nivel]
    return sum(1 for r in total if r.obtenido == nivel), len(total)


def resumir(resultados: list[Resultado]) -> dict[str, Any]:
    n = len(resultados) or 1
    falsos_negativos = [r for r in resultados if r.sub_escala]
    rojos_perdidos = [r for r in falsos_negativos if r.esperado == "rojo"]

    datos: dict[str, Any] = {
        "n": len(resultados),
        "exactitud": round(sum(r.acierta for r in resultados) / n, 4),
        "matriz": matriz(resultados),
        "falsos_negativos": len(falsos_negativos),
        "rojos_perdidos": len(rojos_perdidos),
        "sobre_escalados": sum(r.sobre_escala for r in resultados),
    }
    for nivel in NIVELES:
        aciertos, total = recall(resultados, nivel)
        datos[f"recall_{nivel}"] = round(aciertos / total, 4) if total else None
        datos[f"n_{nivel}"] = total
    return datos


def imprimir(resumen: dict[str, Any], titulo: str = "") -> None:
    if titulo:
        print(f"\n{titulo}")
    print("─" * 62)
    m = resumen["matriz"]
    print(f"{'esperado \\ obtenido':<22}" + "".join(f"{o:>12}" for o in NIVELES))
    for esperado in NIVELES:
        fila = "".join(f"{m[esperado][o]:>12}" for o in NIVELES)
        print(f"  {esperado:<20}{fila}")
    print("─" * 62)
    print(f"  n = {resumen['n']}   exactitud {resumen['exactitud']:.1%}")
    for nivel in NIVELES:
        r = resumen.get(f"recall_{nivel}")
        marca = "" if r is None else f"{r:.1%}"
        print(f"  recall {nivel:<9} {marca:>7}   (n={resumen.get(f'n_{nivel}')})")
    print(f"  falsos negativos     {resumen['falsos_negativos']}")
    print(f"  rojos perdidos       {resumen['rojos_perdidos']}   <- el número que decide")
    print(f"  sobre-escalados      {resumen['sobre_escalados']}")

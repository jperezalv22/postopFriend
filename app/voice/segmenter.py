"""Corte del texto en frases para sintetizar mientras el LLM sigue escribiendo.

Esperar a que el LLM termine antes de empezar a hablar suma su latencia completa a
la del TTS. Cortando en la primera frase, la síntesis arranca cuando el modelo
lleva ~15 palabras y el resto se solapa con la reproducción. Es la diferencia
entre 1.2 s y 2.5 s de espera para el paciente, medida en el reloj del cliente.

La primera frase se corta antes que las siguientes a propósito: lo que decide la
latencia percibida es cuándo empieza a sonar la voz, no cuándo termina.
"""

from __future__ import annotations

import re

MINIMO_PRIMERA = 25    # caracteres antes de permitir el primer corte
MINIMO_SIGUIENTE = 60  # después ya no corre prisa: frases más largas suenan mejor
MAXIMO = 220           # corte forzado: ninguna frase debería llegar aquí

_FIN = re.compile(r"[.!?…]+[\"'»)\]]*\s")
_PAUSA = re.compile(r"[,;:]\s")

# «38.5» no termina una frase, ni «Dr.», ni «p. ej.».
_FALSO_FIN = re.compile(r"(\d\.\d|\b(sr|sra|dr|dra|ej|etc|aprox|núm|no)\.)$", re.IGNORECASE)


class Segmentador:
    """Acumula el texto que llega en streaming y suelta frases completas."""

    def __init__(self) -> None:
        self._buffer = ""
        self._emitidas = 0

    @property
    def _minimo(self) -> int:
        return MINIMO_PRIMERA if self._emitidas == 0 else MINIMO_SIGUIENTE

    def agregar(self, trozo: str) -> list[str]:
        """Añade texto del stream y devuelve las frases ya listas para sintetizar."""
        self._buffer += trozo
        listas: list[str] = []
        while True:
            corte = self._buscar_corte()
            if corte is None:
                break
            frase, self._buffer = self._buffer[:corte].strip(), self._buffer[corte:].lstrip()
            if frase:
                listas.append(frase)
                self._emitidas += 1
        return listas

    def _buscar_corte(self) -> int | None:
        if len(self._buffer) < self._minimo:
            return None

        for m in _FIN.finditer(self._buffer):
            fin = m.end()
            if fin < self._minimo:
                continue
            if _FALSO_FIN.search(self._buffer[: m.start() + 1]):
                continue
            return fin

        # Sin punto pero ya muy largo: se corta en la última coma para no ahogar la voz.
        if len(self._buffer) >= MAXIMO:
            pausas = list(_PAUSA.finditer(self._buffer[:MAXIMO]))
            return pausas[-1].end() if pausas else MAXIMO
        return None

    def vaciar(self) -> str:
        """Lo que quede al terminar el stream. Se sintetiza aunque no cierre en punto."""
        resto, self._buffer = self._buffer.strip(), ""
        if resto:
            self._emitidas += 1
        return resto

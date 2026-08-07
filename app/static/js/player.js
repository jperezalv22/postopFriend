/* Reproducción del audio del agente, con barge-in.
 *
 * Se usa MediaSource para empezar a sonar con el primer trozo de MP3 en vez de
 * esperar el audio completo. Es lo que hace que la latencia que mide la rúbrica
 * —fin de habla del paciente hasta que empieza a sonar la voz— sea la del primer
 * trozo del TTS y no la del último.
 *
 * `alPrimerAudio` se dispara con el evento `playing` del elemento <audio>: ese es
 * literalmente el instante en que el paciente empieza a oír al agente, y es el
 * que se manda al servidor como marca de tiempo del cliente.
 */

class Reproductor {
  constructor({ alPrimerAudio, alTerminar } = {}) {
    this.alPrimerAudio = alPrimerAudio || (() => {});
    this.alTerminar = alTerminar || (() => {});
    this.audio = new Audio();
    this.audio.autoplay = true;
    this.turnoActual = null;
    this.usaMSE = typeof MediaSource !== "undefined" && MediaSource.isTypeSupported("audio/mpeg");
    this._reiniciarEstado();

    this.audio.addEventListener("playing", () => {
      if (!this.avisado && this.turnoActual !== null) {
        this.avisado = true;
        this.alPrimerAudio(this.turnoActual, performance.now());
      }
    });
    this.audio.addEventListener("ended", () => this.alTerminar(this.turnoActual));
  }

  _reiniciarEstado() {
    this.avisado = false;
    this.cola = [];
    this.buffer = null;
    this.fuente = null;
    this.trozosSueltos = [];
    this.cerrado = false;
  }

  iniciarTurno(turnoIdx) {
    this.detener();
    this.turnoActual = turnoIdx;
    this._reiniciarEstado();

    if (!this.usaMSE) return; // sin MSE se acumula y se reproduce al cerrar

    this.fuente = new MediaSource();
    this.audio.src = URL.createObjectURL(this.fuente);
    this.fuente.addEventListener("sourceopen", () => {
      try {
        this.buffer = this.fuente.addSourceBuffer("audio/mpeg");
        this.buffer.addEventListener("updateend", () => this._drenar());
        this._drenar();
      } catch (e) {
        // Si MSE falla a mitad se cae al modo blob: peor latencia, pero suena.
        console.warn("MediaSource no aceptó audio/mpeg, se usa blob:", e);
        this.usaMSE = false;
      }
    });
  }

  agregar(trozo) {
    this.trozosSueltos.push(trozo);
    if (!this.usaMSE) return;
    this.cola.push(trozo);
    this._drenar();
  }

  _drenar() {
    if (!this.buffer || this.buffer.updating || !this.cola.length) return;
    if (this.fuente.readyState !== "open") return;
    try {
      this.buffer.appendBuffer(this.cola.shift());
    } catch (e) {
      console.warn("appendBuffer falló:", e);
      this.cola = [];
    }
  }

  cerrarTurno() {
    if (this.cerrado) return;
    this.cerrado = true;

    if (!this.usaMSE) {
      const blob = new Blob(this.trozosSueltos, { type: "audio/mpeg" });
      this.audio.src = URL.createObjectURL(blob);
      this.audio.play().catch(() => {});
      return;
    }
    const cerrar = () => {
      if (this.fuente && this.fuente.readyState === "open" && !this.buffer?.updating) {
        try { this.fuente.endOfStream(); } catch (_) {}
      } else if (this.fuente && this.fuente.readyState === "open") {
        setTimeout(cerrar, 40);
      }
    };
    cerrar();
  }

  /** Barge-in: el paciente habló encima del agente. Se corta en seco. */
  detener() {
    try { this.audio.pause(); } catch (_) {}
    if (this.fuente && this.fuente.readyState === "open") {
      try { this.fuente.endOfStream(); } catch (_) {}
    }
    this.audio.removeAttribute("src");
    this.audio.load();
  }

  get sonando() {
    return !this.audio.paused && !this.audio.ended && this.audio.currentTime > 0;
  }
}

window.Reproductor = Reproductor;

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
    this._cerrarFuente();
  }

  /* Cerrar el MediaSource es lo más delicado de este archivo, porque si no se
   * cierra el <audio> se queda esperando datos que ya no van a llegar: ni
   * `paused` ni `ended`, o sea `sonando` en true para siempre. Y eso no solo
   * rompe el barge-in: Chrome sigue creyendo que hay audio saliendo y mantiene
   * el cancelador de eco apretando el micrófono, así que la voz del paciente
   * llega troceada y el VAD la descarta como «misfire». El síntoma es que la
   * llamada se queda muda tras unos turnos.
   *
   * Hay tres formas de no llegar a `endOfStream()` y las tres pasan de verdad:
   *   - `audio_fin` llega antes que `sourceopen` (readyState «closed»); es una
   *     carrera que ganan las respuestas cortas y las cacheadas.
   *   - quedan trozos en la cola sin volcar al SourceBuffer.
   *   - el SourceBuffer está en pleno `updating`.
   */
  _cerrarFuente(intentos = 0) {
    if (!this.fuente || !this.cerrado) return;

    const estado = this.fuente.readyState;
    if (estado === "ended") return;             // ya está cerrado
    const listoParaCerrar =
      estado === "open" && !this.cola.length && !this.buffer?.updating;

    if (listoParaCerrar) {
      try { this.fuente.endOfStream(); } catch (_) {}
      return;
    }

    if (intentos < Reproductor.INTENTOS_DE_CIERRE) {
      setTimeout(() => this._cerrarFuente(intentos + 1), Reproductor.ESPERA_DE_CIERRE_MS);
      return;
    }

    // Se agotó la espera. Vale más quedarse sin la cola de audio que dejar el
    // elemento colgado: mientras siga «sonando», el micrófono no vuelve.
    console.warn("MediaSource no se pudo cerrar a tiempo; se libera el elemento");
    this.detener();
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
    if (!this.audio.src) return false;   // tras `detener()` no hay nada sonando
    return !this.audio.paused && !this.audio.ended && this.audio.currentTime > 0;
  }
}

/** Cuánto se espera a que el MediaSource se deje cerrar: 100 × 40 ms = 4 s. */
Reproductor.INTENTOS_DE_CIERRE = 100;
Reproductor.ESPERA_DE_CIERRE_MS = 40;

window.Reproductor = Reproductor;

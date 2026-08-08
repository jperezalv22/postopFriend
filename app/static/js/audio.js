/* Captura de micrófono con detección de voz (Silero VAD) en el navegador.
 *
 * El endpointing va en el cliente por dos razones que se pagan en puntos:
 *
 *  1. `t_fin_habla` se marca con el reloj del navegador en el instante exacto en
 *     que el paciente calla. Es el punto donde la rúbrica empieza a medir. Si el
 *     VAD estuviera en el servidor habría que restar el viaje de red, o inflar el
 *     número a favor propio.
 *  2. El barge-in es inmediato: no hay que esperar un round-trip para callar al
 *     agente cuando el paciente lo interrumpe.
 *
 * Todo se sirve desde /static/vendor/. Nada de CDN: si el jurado no tiene la red
 * abierta o el CDN se cae, la compuerta G4 se cae entera con él.
 */

/** WAV PCM 16 bits mono. Whisper lo acepta y evita depender del códec del navegador. */
function aWav(muestras, frecuencia = 16000) {
  const buffer = new ArrayBuffer(44 + muestras.length * 2);
  const v = new DataView(buffer);
  const txt = (pos, s) => { for (let i = 0; i < s.length; i++) v.setUint8(pos + i, s.charCodeAt(i)); };

  txt(0, "RIFF");
  v.setUint32(4, 36 + muestras.length * 2, true);
  txt(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);            // PCM
  v.setUint16(22, 1, true);            // mono
  v.setUint32(24, frecuencia, true);
  v.setUint32(28, frecuencia * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  txt(36, "data");
  v.setUint32(40, muestras.length * 2, true);

  for (let i = 0; i < muestras.length; i++) {
    const m = Math.max(-1, Math.min(1, muestras[i]));
    v.setInt16(44 + i * 2, m < 0 ? m * 0x8000 : m * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

class CapturaDeVoz {
  constructor({ alEmpezarHabla, alTerminarHabla, alFallar } = {}) {
    this.alEmpezarHabla = alEmpezarHabla || (() => {});
    this.alTerminarHabla = alTerminarHabla || (() => {});
    this.alFallar = alFallar || (() => {});
    this.vad = null;
    this.modo = "ninguno";
    this.grabadora = null;
    this.trozos = [];
  }

  async iniciar() {
    try {
      await this._iniciarVad();
      this.modo = "vad";
      return "vad";
    } catch (e) {
      console.warn("El VAD no arrancó, se usa pulsar para hablar:", e);
      this.alFallar(e);
      await this._iniciarPulsarParaHablar();
      this.modo = "pulsar";
      return "pulsar";
    }
  }

  async _iniciarVad() {
    if (!window.vad?.MicVAD) throw new Error("bundle del VAD no cargado");

    // No existe `window.ort` y es a propósito: el bundle del VAD trae su propio
    // onnxruntime empotrado, y con una segunda copia suelta cargada el VAD no
    // arranca. La ruta del wasm se le pasa por `onnxWASMBasePath`, que es la que
    // entiende su copia interna. Ver app/voice/vendor.py.
    this.vad = await window.vad.MicVAD.new({
      baseAssetPath: "/static/vendor/",
      onnxWASMBasePath: "/static/vendor/",
      model: "v5",
      // Ajustado para conversación telefónica con pacientes mayores: dejarles
      // terminar la frase sin cortarles, pero sin que el turno se sienta lento.
      positiveSpeechThreshold: 0.55,
      negativeSpeechThreshold: 0.38,
      redemptionFrames: 12,       // ~380 ms de silencio cierran el enunciado
      minSpeechFrames: 4,
      preSpeechPadFrames: 6,
      onSpeechStart: () => this.alEmpezarHabla(),
      onSpeechEnd: (muestras) => {
        // performance.now() aquí = instante en que el paciente terminó de hablar.
        const t = performance.now();
        this.alTerminarHabla(aWav(muestras), muestras.length / 16000, t);
      },
    });
    this.vad.start();
  }

  async _iniciarPulsarParaHablar() {
    const flujo = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.grabadora = new MediaRecorder(flujo);
    this.grabadora.ondataavailable = (e) => e.data.size && this.trozos.push(e.data);
    this.grabadora.onstop = () => {
      const t = performance.now();
      const blob = new Blob(this.trozos, { type: this.grabadora.mimeType });
      this.trozos = [];
      this.alTerminarHabla(blob, this._segundos(), t);
    };
  }

  _segundos() {
    return this.inicioGrabacion ? (performance.now() - this.inicioGrabacion) / 1000 : 0;
  }

  empezarAGrabar() {
    if (this.modo !== "pulsar" || !this.grabadora) return;
    this.inicioGrabacion = performance.now();
    this.trozos = [];
    this.grabadora.start();
    this.alEmpezarHabla();
  }

  pararDeGrabar() {
    if (this.modo === "pulsar" && this.grabadora?.state === "recording") this.grabadora.stop();
  }

  pausar() { if (this.vad) this.vad.pause(); }
  reanudar() { if (this.vad) this.vad.start(); }

  detener() {
    if (this.vad) { this.vad.pause(); this.vad.destroy?.(); this.vad = null; }
    if (this.grabadora?.stream) this.grabadora.stream.getTracks().forEach((t) => t.stop());
  }
}

window.CapturaDeVoz = CapturaDeVoz;
window.aWav = aWav;

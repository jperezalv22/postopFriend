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

/* Ajuste del endpointing, en milisegundos.
 *
 * Van en `*Ms` y no en `*Frames` porque son las únicas que el bundle mira. Ver
 * `_verificarAjustes()` más abajo: pasarlas en frames se acepta sin error y se
 * ignora en silencio.
 *
 * El modelo v5 procesa marcos de 512 muestras a 16 kHz = 32 ms cada uno, así que
 * el bundle convierte con `Math.floor(ms / 32)`. Los valores están elegidos para
 * caer justo en un múltiplo y que lo pedido sea lo aplicado.
 */
const AJUSTES = {
  // 0.55 dejaba fuera media conversación. El agente suena por el altavoz, el
  // cancelador de eco de Chrome agacha la voz del paciente mientras tanto, y una
  // respuesta de una palabra no vuelve a levantar la probabilidad hasta 0.55. Con
  // 0.40 el filtro de verdad pasa a ser `minSpeechMs`, que es donde debe estar.
  positiveSpeechThreshold: 0.40,
  negativeSpeechThreshold: 0.28,

  // 192 ms = 6 marcos. Un «sí», un «no» o un «nueve» son un turno completo en esta
  // conversación: son literalmente las respuestas que pide el protocolo. El valor
  // por omisión del bundle son 400 ms, y con eso las respuestas de una palabra se
  // descartaban como misfire y no salían nunca del navegador.
  minSpeechMs: 192,

  // 704 ms = 22 marcos de silencio cierran el enunciado. Suficiente para que un
  // paciente mayor respire a mitad de frase sin que se le corte, y muy lejos de
  // los 1376 ms por omisión, que además de dejar aire muerto en cada turno
  // ensuciaban `t_fin_habla`: se estampa cuando expira la redención.
  redemptionMs: 704,

  // 320 ms = 10 marcos por delante del ataque. Los 800 ms por omisión metían tanto
  // silencio en el WAV que Whisper se ponía a continuar su propio prompt de sesgo
  // en vez de transcribir. Ver ALUCINACIONES y _es_eco_del_prompt en voice/stt.py.
  preSpeechPadMs: 320,
};

/* Segundo filtro, sobre la confianza del modelo y no sobre la duración.
 *
 * `minSpeechMs` cuenta marcos que pasan el umbral, y con el umbral en 0.40 el eco
 * del propio agente saliendo por el altavoz lo pasa sin problema: seis marcos de
 * 0.45 son 192 ms y abren turno. Lo que se mandaba entonces era un segundo largo
 * de eco y silencio, Whisper no encontraba habla y se ponía a continuar su prompt
 * de sesgo. El agente contestaba «no le escuché bien», eso sonaba por el altavoz,
 * y volvía a disparar el VAD: tres vueltas seguidas en una llamada real.
 *
 * Silero separa las dos cosas mejor por el pico que por la duración: sobre voz
 * larga la probabilidad llega a 0.95 y pico, sobre eco cancelado se queda cerca
 * del umbral.
 *
 * **Pero solo corre mientras el agente está sonando.** Esa es la corrección que
 * salió de medir. El eco existe únicamente mientras hay audio del agente en el
 * altavoz; con el agente callado no hay nada que filtrar, y aplicar el corte solo
 * puede tirar voz buena. En una tanda de 9 enunciados reales, los 9 ocurrieron con
 * el agente en silencio y el corte mató uno que decía «Marcó un 36» —la
 * temperatura del paciente, una variable del protocolo— sin dejar rastro en
 * ningún sitio: el audio no sale del navegador, así que no hay transcripción, ni
 * turno, ni fila en la base.
 *
 * Los valores también bajaron, y con el dato delante. Sobre voz medida de verdad
 * los enunciados largos dan 0.94–1.00, pero los cortos —«sí», «un cuatro», «marcó
 * un 36», que son literalmente las respuestas que pide el protocolo— bajan hasta
 * 0.68 de pico y 0.51 de media. El corte anterior (0.80 / 0.55) estaba por encima
 * de eso: descartaba por ser corto, no por ser eco.
 *
 * Ahora el corte solo decide en la ventana donde el paciente interrumpe al agente,
 * y ahí una respuesta corta tiene que sobrevivir igual: el barge-in puntúa.
 */
const PICO_MINIMO = 0.60;

/* La media de los marcos de habla, para el caso contrario: un golpe seco —una
 * puerta, el teclado— da un pico altísimo en dos marcos y nada alrededor. Con el
 * pico solo, pasaría. */
const MEDIA_MINIMA = 0.45;

/* Cuánto puede seguir sonando el eco después de que el audio del agente termina.
 *
 * El altavoz deja de recibir muestras antes de que la sala deje de devolverlas, y
 * el cancelador de Chrome tarda otro poco en converger. Sin esta cola, un enunciado
 * que empieza justo al cerrar el audio del agente se juzgaría como si no hubiera
 * eco posible, que es exactamente cuando más hay. */
const COLA_DE_ECO_MS = 500;

class CapturaDeVoz {
  constructor({ alEmpezarHabla, alConfirmarHabla, alTerminarHabla,
                alDescartarHabla, alFallar, alMedirNivel, agenteSonando } = {}) {
    this.alEmpezarHabla = alEmpezarHabla || (() => {});
    this.alConfirmarHabla = alConfirmarHabla || (() => {});
    this.alTerminarHabla = alTerminarHabla || (() => {});
    this.alDescartarHabla = alDescartarHabla || (() => {});
    this.alFallar = alFallar || (() => {});

    /* Cuánta señal entra ahora mismo, entre 0 y 1, para pintarlo en pantalla.
     *
     * Sale gratis: con el VAD es la probabilidad de habla que el modelo ya está
     * calculando marco a marco, y en «pulsar para hablar» es el RMS del analizador.
     * Vale lo que cuesta porque separa dos fallos que en pantalla se ven igual —el
     * micrófono no entra, o el agente no contesta— y que hasta ahora solo se podían
     * distinguir abriendo la consola. */
    this.alMedirNivel = alMedirNivel || (() => {});

    // Quién sabe si el altavoz está sonando es la interfaz, que es la que tiene el
    // reproductor. Por omisión se asume que no: sin esta señal el filtro de
    // confianza no debe correr, porque su único caso de uso es el eco.
    this.agenteSonando = agenteSonando || (() => false);
    this.vad = null;
    this.modo = "ninguno";
    this.grabadora = null;
    this.trozos = [];
    this.contexto = null;   // AudioContext del medidor en modo «pulsar»
    this.vigilante = null;  // su requestAnimationFrame

    // Suelo de ruido: marcos de fuera de todo enunciado. Es la referencia contra
    // la que un pico significa algo. Rueda para no crecer sin fin en una llamada
    // larga; 300 marcos son unos 10 s de sala.
    this.enEnunciado = false;
    this.probsFondo = [];

    this._reiniciarMedidas();
  }

  /** Estadística del enunciado en curso, para el filtro por confianza. */
  _reiniciarMedidas() {
    this.marcosDeHabla = 0;
    this.sumaProb = 0;
    this.picoProb = 0;

    // Para el diagnóstico: la probabilidad marco a marco, separada en la del
    // enunciado y la del silencio de alrededor. El pico y la media resumen
    // demasiado —dos enunciados con el mismo pico pueden tener formas muy
    // distintas— y el suelo de fondo es lo que dice si el eco llega o no.
    this.probsEnunciado = [];
    this.tInicioEnunciado = null;
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
      ...AJUSTES,

      // El modelo entrega la probabilidad de habla marco a marco. Es la única
      // señal disponible para distinguir al paciente del eco del propio agente,
      // y se acumula aquí porque `onSpeechEnd` solo entrega las muestras.
      //
      // No hace falta acotarlo al enunciado: los marcos por debajo del umbral no
      // suman, y el primer marco que lo pasa es por definición el arranque del
      // enunciado. Se limpia al cerrarlo.
      onFrameProcessed: ({ isSpeech }) => {
        this.alMedirNivel(isSpeech);
        if (isSpeech >= AJUSTES.positiveSpeechThreshold) {
          this.marcosDeHabla += 1;
          this.sumaProb += isSpeech;
        }
        if (isSpeech > this.picoProb) this.picoProb = isSpeech;

        // Dentro del enunciado va a su histograma; fuera, al suelo de ruido.
        if (this.enEnunciado) {
          this.probsEnunciado.push(isSpeech);
        } else {
          this.probsFondo.push(isSpeech);
          if (this.probsFondo.length > 300) this.probsFondo.shift();
        }
      },

      // Primer marco por encima del umbral. Todavía no se sabe si es el paciente
      // o el altavoz, así que aquí solo se para el reloj del silencio.
      onSpeechStart: () => {
        this.enEnunciado = true;
        this.tInicioEnunciado = performance.now();
        this.alEmpezarHabla();
      },

      // Habla confirmada: `minSpeechMs` de voz sostenida. Cortar al agente es
      // destructivo —se pierde lo que faltaba por decir— así que el barge-in
      // cuelga de esta señal y no de la anterior. Cuesta 192 ms de reacción y
      // ahorra las interrupciones que disparaba el eco del propio agente.
      onSpeechRealStart: () => this.alConfirmarHabla(),

      onSpeechEnd: (muestras) => {
        // performance.now() aquí = instante en que el paciente terminó de hablar.
        const t = performance.now();
        const medida = this._cerrarMedidas(t);

        // El filtro de confianza solo tiene sentido donde puede haber eco. Con el
        // agente callado no hay altavoz que devolver, así que lo capturado es el
        // paciente y descartarlo es perder habla. Ver PICO_MINIMO.
        const puedeHaberEco = this.agenteSonando();
        const bajoDePico = puedeHaberEco && medida.pico < PICO_MINIMO;
        const bajoDeMedia = puedeHaberEco && medida.media < MEDIA_MINIMA;
        const motivo = [bajoDePico && "pico", bajoDeMedia && "media"].filter(Boolean).join("+");

        // Se registra siempre, pase o no. Sin los que pasan no hay línea de base
        // contra la que juzgar a los que no, y sin las muestras no se sabe si el
        // problema es el corte o el volumen de entrada.
        medida.registro = this._anotar(medida, muestras, t, motivo ? "descartado" : "enviado", motivo);

        if (bajoDePico || bajoDeMedia) {
          this.alDescartarHabla(medida);
          return;
        }
        // La duración que se reporta es la del habla, no la del recorte: el WAV
        // lleva pegados el margen de entrada y la redención, y contarlos inflaba
        // `audio_paciente_s` casi un segundo por turno en la base y en el acta.
        this.alTerminarHabla(aWav(muestras), medida.segundos, t);
      },

      // Sin esto un enunciado descartado es un agujero negro: el paciente habla,
      // no sale nada, y nadie —ni él ni el reloj del silencio— se entera.
      onVADMisfire: () => {
        const t = performance.now();
        const medida = this._cerrarMedidas(t);
        medida.registro = this._anotar(medida, null, t, "misfire", "duración");
        this.alDescartarHabla(medida);
      },
    });
    this.vad.start();
    this._verificarAjustes();
  }

  /** Cierra la estadística del enunciado y la devuelve. */
  _cerrarMedidas(t = performance.now()) {
    const marcos = this.marcosDeHabla;
    const pct = window.percentilDiag || (() => 0);
    const fondo = this.probsFondo;
    const medida = {
      marcos,
      pico: this.picoProb,
      media: marcos ? this.sumaProb / marcos : 0,
      segundos: (marcos * (this.vad?.frameProcessor?.msPerFrame || 32)) / 1000,

      // Diagnóstico. La forma del enunciado y el suelo contra el que se lo juzga.
      p50: pct(this.probsEnunciado, 0.50),
      p90: pct(this.probsEnunciado, 0.90),
      total_ms: this.tInicioEnunciado ? t - this.tInicioEnunciado : 0,
      fondo_media: fondo.length ? fondo.reduce((a, b) => a + b, 0) / fondo.length : 0,
      fondo_p90: pct(fondo, 0.90),
    };
    this.enEnunciado = false;
    this._reiniciarMedidas();
    return medida;
  }

  /* Deja constancia del enunciado, haya pasado los cortes o no.
   *
   * Los descartados se mandan además a transcribir aparte, fuera de la llamada.
   * Es la única forma de contestar la pregunta que importa: si lo que el corte
   * tiró era «me duele un ocho» o era el altavoz. Va detrás de `?diag=1` porque
   * gasta una transcripción por descarte y no puede correr en la demo del jurado.
   */
  _anotar(medida, muestras, t, veredicto, motivo) {
    const diag = window.Diagnostico;
    if (!diag?.activo) return null;

    const nivel = muestras ? window.nivelDeAudio(muestras) : { rms_dbfs: -99, cresta_dbfs: -99 };
    const registro = diag.enunciado({ ...medida, ...nivel, t_fin: t, veredicto, motivo_descarte: motivo });

    if (veredicto !== "enviado" && muestras) {
      const cuerpo = new FormData();
      cuerpo.append("audio", aWav(muestras), "descartado.wav");
      fetch("/api/diagnostico/transcribir", { method: "POST", body: cuerpo })
        .then((r) => r.json())
        .then((d) => diag.rescate(registro, d))
        .catch(() => {});
    }
    return registro;
  }

  /* El bundle acepta opciones que no existen sin decir ni pío.
   *
   * `redemptionFrames`, `minSpeechFrames` y `preSpeechPadFrames` entran al objeto
   * de opciones, sobreviven a `validateOptions` —que solo valida las `*Ms`— y
   * después nadie las lee: el procesador de marcos deriva los tres números de
   * `redemptionMs`, `minSpeechMs` y `preSpeechPadMs`. Pasarlas en frames dejaba
   * los valores por omisión puestos, y con `minSpeechMs` en 400 la puerta real
   * eran 384 ms de voz por encima del umbral. Las respuestas de una palabra no la
   * pasaban: el paciente hablaba y su turno no existía.
   *
   * Un fallo que se ve en la conversación pero no en la consola cuesta una tarde.
   * Esto lo convierte en una línea roja al arrancar.
   */
  _verificarAjustes() {
    const fp = this.vad?.frameProcessor;
    if (!fp) return;

    const ms = fp.msPerFrame || 32;
    const esperado = {
      minSpeechFrames: Math.floor(AJUSTES.minSpeechMs / ms),
      redemptionFrames: Math.floor(AJUSTES.redemptionMs / ms),
      preSpeechPadFrames: Math.floor(AJUSTES.preSpeechPadMs / ms),
    };
    const discrepa = Object.entries(esperado)
      .filter(([k, v]) => fp[k] !== v)
      .map(([k, v]) => `${k}: se pidió ${v}, quedó ${fp[k]}`);

    if (discrepa.length) {
      console.error("VAD mal configurado — el bundle ignoró opciones:", discrepa.join(" · "));
    } else {
      console.info(`VAD listo · marco ${ms} ms · habla mínima ${esperado.minSpeechFrames} `
        + `marcos (${AJUSTES.minSpeechMs} ms) · redención ${esperado.redemptionFrames} `
        + `marcos (${AJUSTES.redemptionMs} ms) · umbral ${AJUSTES.positiveSpeechThreshold} `
        + `· se manda si pico ≥ ${PICO_MINIMO} y media ≥ ${MEDIA_MINIMA}`);
    }
    return discrepa;
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
    this._vigilarNivel(flujo);
  }

  /* Medidor de entrada para el modo de respaldo.
   *
   * Aquí no hay VAD que entregue una probabilidad, así que se toma el RMS del
   * analizador. Es el único modo donde el usuario tiene que decidir a mano cuándo
   * hablar, y por tanto donde más falta hace ver que el micrófono entra: si el
   * VAD ya falló, la confianza en que algo esté llegando es la que es.
   */
  _vigilarNivel(flujo) {
    try {
      this.contexto = new (window.AudioContext || window.webkitAudioContext)();
      const fuente = this.contexto.createMediaStreamSource(flujo);
      const analizador = this.contexto.createAnalyser();
      analizador.fftSize = 1024;
      fuente.connect(analizador);
      const muestras = new Float32Array(analizador.fftSize);

      const mirar = () => {
        if (!this.contexto) return;              // `detener()` cortó el bucle
        analizador.getFloatTimeDomainData(muestras);
        let suma = 0;
        for (const m of muestras) suma += m * m;
        const rms = Math.sqrt(suma / muestras.length);
        // El RMS de la voz normal ronda 0.05–0.2. Se escala para que el medidor
        // recorra su ancho con voz de conversación y no se quede pegado abajo.
        this.alMedirNivel(Math.min(1, rms * 6));
        this.vigilante = requestAnimationFrame(mirar);
      };
      mirar();
    } catch (e) {
      console.warn("sin medidor de nivel:", e);
    }
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
    // El bucle del medidor mira `this.contexto` para saber si sigue vivo: sin
    // cerrarlo, un rAF por marco sobrevive a la llamada y mantiene el micrófono
    // abierto.
    cancelAnimationFrame(this.vigilante);
    this.vigilante = null;
    if (this.contexto) { this.contexto.close().catch(() => {}); this.contexto = null; }
    this.alMedirNivel(0);
  }
}

window.CapturaDeVoz = CapturaDeVoz;
window.AJUSTES_VAD = AJUSTES;
window.PICO_MINIMO = PICO_MINIMO;
window.MEDIA_MINIMA = MEDIA_MINIMA;
window.COLA_DE_ECO_MS = COLA_DE_ECO_MS;
window.aWav = aWav;

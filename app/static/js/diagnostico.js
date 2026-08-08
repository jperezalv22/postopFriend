/* Instrumentación del endpointing. Solo se enciende con `?diag=1`.
 *
 * Existe porque los cortes del VAD —`PICO_MINIMO`, `MEDIA_MINIMA`, `redemptionMs`—
 * se eligieron a ojo contra el eco del agente, y el precio lo paga la voz normal:
 * un enunciado que no llega al pico exigido se descarta *antes* de salir del
 * navegador, así que no hay ni transcripción ni turno ni rastro en la base. El
 * fallo es invisible en el servidor por construcción.
 *
 * Lo que hace falta para decidir con datos y no con impresiones es medir **los
 * enunciados que sí pasan y los que no, con las mismas columnas**. Hoy solo se
 * registran los descartes, y sin la línea de base no se sabe si un pico de 0.72 es
 * voz baja o es eco.
 *
 * Tres columnas son las que deciden:
 *
 *   - `pico` / `media`: lo que miran los cortes. Es la variable de decisión.
 *   - `rms_dbfs`: cuánto se habló de verdad. Separa «el corte está alto» de «el
 *     micrófono entra bajo», que se arreglan en sitios distintos.
 *   - `agente_sonando`: la única forma de distinguir al paciente del eco sin
 *     escuchar el audio. Sin esto, subir o bajar el corte es tirar una moneda.
 *
 * El resumen cruza las tres y dibuja la curva: para cada corte candidato, cuántos
 * enunciados del paciente pasarían y cuántos ecos se colarían.
 */

const MAXIMO_REGISTROS = 500;

/** Etiquetas de la prueba. Sin esto los números son de «alguien hablando». */
const ETIQUETAS = ["normal", "duro", "bajito", "lento", "rápido", "lejos del mic"];

const activo = new URLSearchParams(location.search).get("diag") === "1";

function percentil(valores, p) {
  if (!valores.length) return 0;
  const orden = [...valores].sort((a, b) => a - b);
  return orden[Math.min(orden.length - 1, Math.floor((orden.length - 1) * p))];
}

const dos = (x) => Math.round(x * 100) / 100;

/** dBFS: 0 es saturación, −60 es casi silencio. La voz cómoda cae sobre −30/−20. */
function dbfs(v) {
  return v > 0 ? Math.round(20 * Math.log10(v) * 10) / 10 : -99;
}

/** Nivel real del audio capturado, para saber si se habló duro o normal. */
function nivelDeAudio(muestras) {
  let suma = 0;
  let cresta = 0;
  for (let i = 0; i < muestras.length; i++) {
    suma += muestras[i] * muestras[i];
    const a = Math.abs(muestras[i]);
    if (a > cresta) cresta = a;
  }
  return {
    rms_dbfs: dbfs(Math.sqrt(suma / (muestras.length || 1))),
    cresta_dbfs: dbfs(cresta),
  };
}

class Diario {
  constructor() {
    this.registros = [];
    this.etiqueta = "normal";
    this._n = 0;
    this._finAnterior = null;
    this._pendiente = null;    // enunciado enviado, esperando su transcripción
    this.contexto = () => ({});
  }

  get activo() { return activo; }

  /* Un enunciado cerrado por el VAD, haya pasado los cortes o no.
   *
   * `veredicto` no se recalcula aquí a propósito: lo decide `audio.js` con sus
   * constantes, y este módulo registra lo que pasó de verdad. Si algún día las dos
   * lógicas discrepan, se vería en los datos en vez de quedar tapado. */
  enunciado(datos) {
    const t = datos.t_fin;
    const registro = {
      n: ++this._n,
      etiqueta: this.etiqueta,
      hora: new Date().toISOString().slice(11, 23),
      veredicto: datos.veredicto,
      motivo_descarte: datos.motivo_descarte || "",

      // Lo que miran los cortes.
      pico: dos(datos.pico),
      media: dos(datos.media),
      p50: dos(datos.p50),
      p90: dos(datos.p90),

      // Cuánto duró, y cuánto de eso fue habla. Un enunciado de 3 s con 400 ms de
      // habla es ruido; uno de 900 ms con 800 ms de habla es un «sí».
      marcos: datos.marcos,
      habla_ms: Math.round(datos.segundos * 1000),
      total_ms: Math.round(datos.total_ms || 0),

      // Frase partida en dos: si el hueco desde el enunciado anterior es corto, la
      // redención cortó a mitad de frase en vez de al final. Es el otro síntoma.
      hueco_ms: this._finAnterior === null ? null : Math.round(t - this._finAnterior),

      // Cuánto se habló, independiente de lo que opine el VAD.
      rms_dbfs: datos.rms_dbfs,
      cresta_dbfs: datos.cresta_dbfs,

      // El suelo: probabilidad del VAD fuera de los enunciados. Es el eco y la sala.
      fondo_media: dos(datos.fondo_media),
      fondo_p90: dos(datos.fondo_p90),

      ...this.contexto(),
      stt: null,
    };

    this._finAnterior = t;
    this.registros.push(registro);
    if (this.registros.length > MAXIMO_REGISTROS) this.registros.shift();
    this._pendiente = datos.veredicto === "enviado" ? registro : null;

    this._imprimir(registro);
    this._pintar();
    return registro;
  }

  /* Lo que Whisper devolvió para el último enunciado enviado.
   *
   * Va emparejado y no suelto porque la pregunta no es «¿transcribe bien?» sino
   * «¿a qué pico y a qué volumen empieza a transcribir mal?». Sin el par, el dato
   * del STT no dice nada sobre dónde poner los cortes. */
  transcripcion(datos) {
    if (!this._pendiente) return;
    this._pendiente.stt = datos;
    this._imprimirStt(this._pendiente);
    this._pendiente = null;
    this._pintar();
  }

  /** Lo que Whisper habría entendido de un enunciado descartado. La prueba directa. */
  rescate(registro, datos) {
    registro.rescate = datos;
    console.info(`  ↳ #${registro.n} descartado, pero Whisper entendía: ${JSON.stringify(datos.texto)}`);
    this._pintar();
  }

  evento(tipo, datos = {}) {
    if (!activo) return;
    console.info(`· ${tipo}`, datos);
  }

  _imprimir(r) {
    const marca = r.veredicto === "enviado" ? "✓" : "✗";
    const eco = r.agente_sonando ? " ⟵ EL AGENTE ESTABA SONANDO (esto puede ser eco)" : "";
    console.info(
      `${marca} #${r.n} [${r.etiqueta}] ${r.veredicto}${r.motivo_descarte ? ` (${r.motivo_descarte})` : ""}`
      + ` · pico ${r.pico} · media ${r.media} · p90 ${r.p90}`
      + ` · ${r.habla_ms} ms habla de ${r.total_ms} ms`
      + ` · nivel ${r.rms_dbfs} dBFS · fondo ${r.fondo_media}`
      + (r.hueco_ms !== null && r.hueco_ms < 1500 ? ` · HUECO ${r.hueco_ms} ms (¿frase partida?)` : "")
      + eco,
    );
  }

  _imprimirStt(r) {
    const s = r.stt || {};
    console.info(`  ↳ #${r.n} STT: ${s.vacia ? `VACÍA (${s.motivo})` : JSON.stringify(s.texto)}`);
  }

  // ─── Lectura de los datos ────────────────────────────────────────────────

  /** Los del paciente: los que ocurrieron con el agente callado. */
  _delPaciente() {
    return this.registros.filter((r) => !r.agente_sonando);
  }

  /** Los que ocurrieron mientras el agente hablaba: en su mayoría, eco. */
  _conAgenteSonando() {
    return this.registros.filter((r) => r.agente_sonando);
  }

  /* Para cada corte candidato: cuánta voz del paciente se pierde y cuánto eco entra.
   *
   * Es la decisión entera en una tabla. El corte bueno es el más bajo que siga
   * dejando fuera el eco — no el más alto que aguante la voz, que es como se llegó
   * al 0.80 de hoy. */
  curva() {
    const paciente = this._delPaciente();
    const eco = this._conAgenteSonando();
    if (!paciente.length) return [];

    const filas = [];
    for (let corte = 0.50; corte <= 0.96; corte += 0.05) {
      const c = dos(corte);
      filas.push({
        "corte de pico": c,
        "voz del paciente que pasa": `${paciente.filter((r) => r.pico >= c).length}/${paciente.length}`,
        "% que pasa": Math.round(100 * paciente.filter((r) => r.pico >= c).length / paciente.length),
        "eco que se cuela": eco.length ? `${eco.filter((r) => r.pico >= c).length}/${eco.length}` : "—",
        "actual": c === 0.80 ? "◀ hoy" : "",
      });
    }
    return filas;
  }

  resumen() {
    const total = this.registros.length;
    if (!total) {
      console.warn("Sin enunciados registrados todavía. Inicie una llamada y hable.");
      return;
    }
    const paciente = this._delPaciente();
    const eco = this._conAgenteSonando();
    const descartados = this.registros.filter((r) => r.veredicto !== "enviado");
    const picos = paciente.map((r) => r.pico);
    const niveles = paciente.map((r) => r.rms_dbfs);

    console.group(`%cResumen de ${total} enunciados`, "font-weight:bold;font-size:13px");

    console.log(
      `Del paciente (agente callado): ${paciente.length} · `
      + `con el agente sonando: ${eco.length} · descartados en total: ${descartados.length}`,
    );

    if (picos.length) {
      console.log(
        `Pico del paciente — mínimo ${dos(Math.min(...picos))} · `
        + `p10 ${dos(percentil(picos, 0.10))} · p50 ${dos(percentil(picos, 0.50))} · `
        + `máximo ${dos(Math.max(...picos))}   (el corte de hoy es 0.80)`,
      );
      console.log(
        `Nivel del paciente — p10 ${percentil(niveles, 0.10)} dBFS · `
        + `p50 ${percentil(niveles, 0.50)} dBFS   (voz cómoda ≈ −30 a −20)`,
      );
    }

    // Por etiqueta: es la comparación que responde «¿tengo que hablar duro?».
    const porEtiqueta = {};
    for (const r of paciente) {
      (porEtiqueta[r.etiqueta] ||= []).push(r);
    }
    console.log("\nPor etiqueta de la prueba (solo enunciados del paciente):");
    console.table(Object.entries(porEtiqueta).map(([et, rs]) => ({
      etiqueta: et,
      n: rs.length,
      "pasaron": rs.filter((r) => r.veredicto === "enviado").length,
      "pico mediano": dos(percentil(rs.map((r) => r.pico), 0.5)),
      "pico mínimo": dos(Math.min(...rs.map((r) => r.pico))),
      "nivel mediano dBFS": percentil(rs.map((r) => r.rms_dbfs), 0.5),
      "STT vacías": rs.filter((r) => r.stt?.vacia).length,
    })));

    console.log("\nDónde poner el corte (`PICO_MINIMO` en app/static/js/audio.js):");
    console.table(this.curva());

    const partidas = paciente.filter((r) => r.hueco_ms !== null && r.hueco_ms < 1500);
    if (partidas.length) {
      console.warn(
        `${partidas.length} enunciados empezaron a menos de 1.5 s del anterior: `
        + `frases partidas en dos por \`redemptionMs\` (hoy 704 ms).`,
      );
    }

    console.log("\nPara traérmelo: Diagnostico.copiar()  ·  o  Diagnostico.descargar()");
    console.groupEnd();
    return this.curva();
  }

  // ─── Sacar los datos de aquí ─────────────────────────────────────────────

  json() {
    return JSON.stringify({
      generado: new Date().toISOString(),
      navegador: navigator.userAgent,
      ajustes_vad: window.AJUSTES_VAD || null,
      cortes: { pico_minimo: window.PICO_MINIMO, media_minima: window.MEDIA_MINIMA },
      total: this.registros.length,
      registros: this.registros,
    }, null, 2);
  }

  async copiar() {
    await navigator.clipboard.writeText(this.json());
    console.info(`Copiado: ${this.registros.length} enunciados. Péguelo en el chat.`);
    return `${this.registros.length} enunciados copiados al portapapeles`;
  }

  descargar() {
    const url = URL.createObjectURL(new Blob([this.json()], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagnostico-voz-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    return "descargado";
  }

  limpiar() {
    this.registros = [];
    this._n = 0;
    this._finAnterior = null;
    this._pendiente = null;
    this._pintar();
    return "limpio";
  }

  // ─── Panel en pantalla ───────────────────────────────────────────────────

  montar() {
    const caja = document.getElementById("diag");
    if (!caja || !activo) return;
    caja.hidden = false;
    caja.innerHTML = `
      <h2>Diagnóstico de captura</h2>
      <p class="mono" style="color:var(--tenue);margin-bottom:10px">
        Marque cómo va a hablar, hable, y al terminar pulse «Resumen» o «Copiar».</p>
      <div class="fila" style="margin-bottom:10px">
        <select id="diag-etiqueta" style="flex:1">
          ${ETIQUETAS.map((e) => `<option${e === "normal" ? " selected" : ""}>${e}</option>`).join("")}
        </select>
      </div>
      <div id="diag-cifras" class="metricas" style="margin-bottom:10px"></div>
      <div class="fila">
        <button id="diag-resumen">Resumen</button>
        <button id="diag-copiar" class="primario">Copiar</button>
        <button id="diag-descargar">Descargar</button>
        <button id="diag-limpiar" class="peligro">Limpiar</button>
      </div>`;

    document.getElementById("diag-etiqueta").addEventListener("change", (e) => {
      this.etiqueta = e.target.value;
      console.info(`etiqueta de la prueba: ${this.etiqueta}`);
    });
    document.getElementById("diag-resumen").addEventListener("click", () => this.resumen());
    document.getElementById("diag-copiar").addEventListener("click", async () => {
      await this.copiar();
      const b = document.getElementById("diag-copiar");
      b.textContent = "¡copiado!";
      setTimeout(() => { b.textContent = "Copiar"; }, 1500);
    });
    document.getElementById("diag-descargar").addEventListener("click", () => this.descargar());
    document.getElementById("diag-limpiar").addEventListener("click", () => this.limpiar());
    this._pintar();
  }

  _pintar() {
    const caja = document.getElementById("diag-cifras");
    if (!caja) return;
    const paciente = this._delPaciente();
    const pasaron = paciente.filter((r) => r.veredicto === "enviado").length;
    const picos = paciente.map((r) => r.pico);
    caja.innerHTML = `
      <div class="metrica"><div class="num">${pasaron}/${paciente.length}</div>
        <div class="eti">pasaron el corte</div></div>
      <div class="metrica"><div class="num">${picos.length ? dos(percentil(picos, 0.5)) : "—"}</div>
        <div class="eti">pico mediano</div></div>
      <div class="metrica"><div class="num">${this.registros.filter((r) => r.stt?.vacia).length}</div>
        <div class="eti">STT vacías</div></div>`;
  }
}

window.Diagnostico = new Diario();
window.nivelDeAudio = nivelDeAudio;
window.percentilDiag = percentil;
document.addEventListener("DOMContentLoaded", () => window.Diagnostico.montar());

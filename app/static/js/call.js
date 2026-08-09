/* Interfaz de llamada: conecta el WebSocket, la captura de voz y la reproducción. */

const $ = (id) => document.getElementById(id);
const NIVELES = ["verde", "amarillo", "rojo", "indeterminado"];

/* Todo lo que llega del servidor pasa por aquí antes de tocar `innerHTML`.
 *
 * El título de un documento y el texto de un fragmento vienen de PDFs que puede
 * haber subido cualquiera: un `<` suelto en un título rompía la tarjeta de citas
 * sin decir por qué, y esa tarjeta es la que sostiene la trazabilidad clínica. */
const esc = (s) => String(s ?? "").replace(/[<>&"]/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

// Tramos de la escalada de silencio (§6.1 del plan). El cliente solo cuenta: es
// quien sabe si el micrófono está oyendo algo. Qué se dice y cuándo se cierra por
// protocolo lo decide el servidor, donde viven los guiones.
const TRAMOS_DE_SILENCIO = [6, 12, 20];

/* Cada cuánto se vuelve a mirar si el agente ya se calló.
 *
 * El reloj del silencio no puede arrancar en el mensaje `escuchando`: el servidor
 * lo manda cuando termina de *enviar* el MP3, y el navegador todavía tiene el
 * audio entero por reproducir. Con la apertura —unas 45 palabras, ~17 s de voz—
 * los 6 s del primer tramo vencían a mitad de la frase, el cliente pedía auxilio,
 * llegaba «¿Sigue ahí?» como turno nuevo y `iniciarTurno()` llamaba a `detener()`:
 * el agente se cortaba a sí mismo a media palabra para preguntar si seguía ahí.
 * En una llamada real pasó cuatro veces.
 *
 * Lo que cuenta es cuándo el paciente deja de oír, no cuándo el servidor deja de
 * escribir en el socket. */
const ESPERA_ENTRE_MIRADAS_MS = 250;

/* Gracia entre `audio_inicio` y el primer sonido del elemento.
 *
 * `sonando` es false mientras el <audio> no ha arrancado, así que sin esta gracia
 * un turno recién anunciado parecería ya terminado y el reloj arrancaría antes de
 * que sonara la primera sílaba. Y al revés: si el MediaSource se queda colgado y
 * nunca emite `ended` —el fallo que documenta player.js—, pasada la gracia se
 * considera callado y el reloj arranca igual. Una llamada muda para siempre es
 * peor que una pregunta de más. */
const GRACIA_DE_ARRANQUE_MS = 1500;

/* Objetivo de latencia declarado en el README, en ms. Se pinta en pantalla para
 * que la cifra se lea contra algo: «1 840» no dice nada sin saber contra qué. */
const OBJETIVO_LATENCIA_MS = 1500;

/** Las seis variables del protocolo, en el orden en que se preguntan.
 *
 * Las claves son las del `EstadoClinico` del backend, no nombres bonitos: el
 * mensaje `triage` llega con `dolor_nrs` y `fiebre_c`, y una clave que no calce
 * pinta «pendiente» sobre un dato que sí se recogió. */
const VARIABLES = {
  dolor_nrs: "Dolor (0-10)", fiebre_c: "Fiebre (°C)", herida: "Herida",
  movilidad: "Movilidad", apetito: "Apetito", sueno: "Sueño",
};

const estado = {
  ws: null,
  captura: null,
  reproductor: null,
  callId: null,
  turnoQueSuena: null,
  latencias: [],
  pacientes: [],
  silencio: { temporizadores: [], reintento: null, desde: null },
  esperandoAlPaciente: false,  // el servidor dijo «escuchando»
  audioEnCurso: false,         // hay un turno del agente anunciado y sin terminar
  inicioAudioAgente: null,     // cuándo se anunció, para la gracia de arranque
  finAudioAgente: null,        // cuándo se calló el altavoz, para la cola del eco
  descartes: 0,            // enunciados seguidos que el VAD descartó
  temporizadorPista: null,
  modoCaptura: "",
  inicioLlamada: null,     // para el reloj de duración
  relojLlamada: null,
  nivelPendiente: null,    // último marco del VAD sin pintar todavía
  pintadoDeNivel: null,    // rAF en vuelo
};

// ─── Silencio ──────────────────────────────────────────────────────────────

function pararSilencio() {
  estado.silencio.temporizadores.forEach(clearTimeout);
  estado.silencio.temporizadores = [];
  clearTimeout(estado.silencio.reintento);
  estado.silencio.reintento = null;
}

/* ¿Le queda audio por sonar al agente?
 *
 * Es distinto de `agenteAudible()`, que lleva media segundo de cola para el eco:
 * aquí no interesa el eco sino si el paciente todavía está escuchando. */
function agenteTodaviaHablando() {
  if (!estado.audioEnCurso) return false;
  if (estado.reproductor?.sonando) return true;
  // Anunciado pero sin sonar: o no ha arrancado aún, o se quedó colgado.
  return performance.now() - estado.inicioAudioAgente < GRACIA_DE_ARRANQUE_MS;
}

function armarSilencio() {
  // Se rearma entero en cada turno: los tramos se cuentan desde el último audio
  // del agente, no desde que empezó la llamada.
  pararSilencio();
  if (estado.ws?.readyState !== 1 || !estado.esperandoAlPaciente) return;

  // Mientras el agente suene, el paciente no está callado: está escuchando. El
  // reloj no ha empezado todavía. Ver ESPERA_ENTRE_MIRADAS_MS.
  if (agenteTodaviaHablando()) {
    estado.silencio.reintento = setTimeout(armarSilencio, ESPERA_ENTRE_MIRADAS_MS);
    return;
  }

  estado.silencio.desde = performance.now();
  estado.silencio.temporizadores = TRAMOS_DE_SILENCIO.map((s) =>
    setTimeout(() => {
      if (estado.ws?.readyState === 1) {
        estado.ws.send(JSON.stringify({ tipo: "silencio", segundos: s }));
      }
    }, s * 1000));
}

// ─── Utilidades de pantalla ────────────────────────────────────────────────

/* El letrero de turno.
 *
 * Además del nombre del estado dice qué se espera de quien mira la pantalla. En
 * una llamada de voz la pregunta que desconcierta no es «¿en qué estado está el
 * sistema?» sino «¿me toca hablar?», y esa no la contestaba un punto de color. */
const ESTADOS = {
  conectando: ["Conectando…", "Pidiendo permiso del micrófono y abriendo la llamada."],
  "sin-microfono": ["Sin micrófono", "Revise el permiso en la barra de direcciones, o responda por escrito abajo."],
  escuchando: ["Su turno", "Hable con normalidad: el detector de voz cierra la frase solo."],
  procesando: ["Procesando", "Transcribiendo, extrayendo variables y decidiendo el nivel."],
  hablando: ["Habla Sofía", "Puede interrumpirla en cualquier momento: se calla al oírle."],
  colgado: ["Llamada terminada", "El acta de cierre está más abajo, con el enlace de descarga."],
};

function ponerEstado(valor) {
  const [titulo, pista] = ESTADOS[valor] || [valor, ""];
  $("estado").textContent = titulo;
  $("pista-estado").textContent = pista;
  const activo = ["escuchando", "procesando", "hablando"].includes(valor);
  $("punto").className = "punto " + (activo ? valor : "");
  $("estado-caja").className = "estado-llamada " + (activo ? valor : "");
}

// ─── Reloj de la llamada ───────────────────────────────────────────────────

/* Cuánto lleva la llamada. Es el dato que uno busca sin pensarlo en cualquier
 * teléfono y aquí, además, sirve para leer el acta: la duración que reporta el
 * servidor tiene que coincidir con lo que se vio en pantalla. */
function arrancarReloj() {
  estado.inicioLlamada = Date.now();
  $("reloj").hidden = false;
  clearInterval(estado.relojLlamada);
  const pintar = () => {
    const s = Math.floor((Date.now() - estado.inicioLlamada) / 1000);
    $("reloj-valor").textContent =
      `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  pintar();
  estado.relojLlamada = setInterval(pintar, 1000);
}

function pararReloj() {
  clearInterval(estado.relojLlamada);
  estado.relojLlamada = null;
}

// ─── Medidor de micrófono ──────────────────────────────────────────────────

/* Cuánta señal está entrando, marco a marco.
 *
 * Sin esto, un micrófono silenciado por el sistema operativo es indistinguible de
 * un agente que no contesta: el paciente habla, no pasa nada, y no hay forma de
 * saber de qué lado está el problema. La marca vertical es el umbral del VAD, así
 * que la pantalla dice además *cuánto* falta para que el enunciado salga.
 *
 * Se pinta en un rAF y no en cada marco: el VAD entrega uno cada 32 ms y escribir
 * en el DOM a esa cadencia compite con la reproducción del audio. */
function mostrarNivel(prob) {
  estado.nivelPendiente = prob;
  if (estado.pintadoDeNivel) return;
  estado.pintadoDeNivel = requestAnimationFrame(() => {
    estado.pintadoDeNivel = null;
    const p = estado.nivelPendiente ?? 0;
    const umbral = window.AJUSTES_VAD?.positiveSpeechThreshold ?? 0.4;
    const relleno = $("mic-nivel");
    relleno.style.width = `${Math.min(100, p * 100)}%`;
    relleno.classList.toggle("voz", p >= umbral);
  });
}

/* El medidor se enseña en los dos modos, pero la marca del umbral solo tiene
 * sentido con el VAD: en «pulsar para hablar» no hay corte que superar, se manda
 * lo que se grabe, y dibujar una línea ahí diría algo que no es cierto. */
function prepararMedidor(modo) {
  $("caja-medidor").hidden = !modo;
  if (!modo) return;
  const umbral = window.AJUSTES_VAD?.positiveSpeechThreshold ?? 0.4;
  const marca = $("mic-umbral");
  marca.hidden = modo !== "vad";
  marca.style.left = `${umbral * 100}%`;
  marca.title = `Umbral del detector de voz (${umbral}). Por debajo, el enunciado no se envía.`;
  ponerLeyendaDelMicro("entrada del micrófono", "var(--tenue)");
}

function ponerLeyendaDelMicro(texto, color) {
  const l = $("mic-leyenda");
  l.textContent = texto;
  l.style.color = color;
}

/* Aviso de enunciado descartado.
 *
 * Un misfire suelto es normal: una tos, una silla, el paciente que arranca y se
 * corta. Encadenar varios no lo es —significa que el micrófono está entrando
 * troceado y el paciente lleva rato hablándole a nadie—, y eso sí hay que decirlo
 * en pantalla en vez de dejar la llamada en silencio. El contador se limpia en
 * cuanto un enunciado sí pasa.
 */
const DESCARTES_PARA_AVISAR = 3;

function avisarDescarte(medida) {
  estado.descartes += 1;

  // La medida que lo descartó, para poder afinar los cortes con datos en vez de a
  // ojo. Si aquí salen picos por encima de 0.9, es habla de verdad y el corte está
  // demasiado alto; si salen rondando el umbral, es eco y está donde debe.
  if (medida) {
    console.info(`enunciado descartado · ${medida.marcos} marcos · `
      + `pico ${medida.pico.toFixed(2)} · media ${medida.media.toFixed(2)}`);
  }
  // El aviso va junto al medidor y no en la cabecera: es donde está mirando quien
  // acaba de hablar y no obtuvo respuesta.
  ponerLeyendaDelMicro("no se entendió — repita, por favor", "var(--amarillo)");
  clearTimeout(estado.temporizadorPista);
  estado.temporizadorPista = setTimeout(
    () => ponerLeyendaDelMicro("entrada del micrófono", "var(--tenue)"), 2500);

  if (estado.descartes === DESCARTES_PARA_AVISAR) {
    agregarTurno("sistema",
      "el micrófono se está oyendo entrecortado: acérquelo o use audífonos "
      + "(por el altavoz, el cancelador de eco recorta la voz mientras habla el agente)",
      `mic-${Date.now()}`, "aviso-warn");
  }
}

/* ¿Puede el micrófono estar oyendo al propio agente ahora mismo?
 *
 * Es la señal de la que cuelga el filtro de confianza del VAD: el eco solo existe
 * mientras hay audio del agente en el altavoz, así que fuera de esa ventana el
 * filtro solo puede tirar voz del paciente. Se mide medio segundo más allá del
 * final del audio porque la sala y el cancelador de Chrome van por detrás.
 */
function agenteAudible() {
  if (estado.reproductor?.sonando) return true;
  return estado.finAudioAgente !== null
    && performance.now() - estado.finAudioAgente < (window.COLA_DE_ECO_MS || 500);
}

const relojDePared = () =>
  new Date().toLocaleTimeString("es-CO", { hour12: false });

function agregarTurno(hablante, texto, turnoIdx, clase = "") {
  const cont = $("transcripcion");
  cont.querySelector(".vacio-guia")?.remove();
  cont.querySelector(".vacio")?.remove();
  const div = document.createElement("div");
  div.className = `turno ${hablante}${clase ? " " + clase : ""}`;
  div.dataset.turno = turnoIdx;
  const quien = hablante === "agente" ? "Sofía · enfermera" : "Paciente";
  div.innerHTML = `<div class="quien"><span>${quien}</span>
    <span class="mono" style="color:var(--muy-tenue)">${relojDePared()}</span>
    <span class="latencia" id="lat-${esc(turnoIdx)}"></span></div><p></p>`;
  div.querySelector("p").textContent = texto;
  cont.appendChild(div);
  cont.scrollTop = cont.scrollHeight;
}

function mostrarLatencia(turnoIdx, ms, etapas, tokensIn, tokensOut) {
  if (ms != null) {
    estado.latencias.push(ms);
    const marca = $(`lat-${turnoIdx}`);
    if (marca) {
      marca.textContent = `${Math.round(ms)} ms`;
      marca.classList.toggle("lenta", ms > OBJETIVO_LATENCIA_MS);
    }
    // La unidad va en el propio número y el color lo lee contra el objetivo: la
    // misma regla que aplica el panel, para que las dos pantallas no discrepen.
    ponerMetricaMs("m-latencia", ms, ms <= OBJETIVO_LATENCIA_MS ? "verde" : "amarillo");
    const ordenadas = [...estado.latencias].sort((a, b) => a - b);
    const p50 = ordenadas[Math.floor(ordenadas.length / 2)];
    ponerMetricaMs("m-p50", p50, p50 <= OBJETIVO_LATENCIA_MS ? "verde" : "amarillo");
  }
  $("m-tokens").textContent = `${tokensIn || 0}/${tokensOut || 0}`;
  const desglose = Object.entries(etapas || {})
    .map(([k, v]) => `${k} ${Math.round(v)} ms`).join(" · ");
  $("etapas").textContent = desglose || "Sin turnos medidos todavía.";
}

function ponerMetricaMs(id, ms, color) {
  const caja = $(id);
  caja.innerHTML = `${Math.round(ms).toLocaleString("es-CO")}<span class="unidad">ms</span>`;
  caja.style.color = `var(--${color})`;
}

/* Cada dato con su icono del sprite de call.html.
 *
 * No es adorno: ocho baldosas con el mismo aspecto obligan a leer los ocho
 * rótulos para encontrar la que se busca, y durante la llamada la que se busca
 * casi siempre es la misma (día postop, o comorbilidades). El icono da un punto
 * de anclaje que se reconoce sin leer. */
function baldosaFicha(icono, rotulo, valor, clase = "") {
  return `<div>
    <span class="caja-ico" aria-hidden="true"><svg class="ico chico"><use href="#i-${icono}"/></svg></span>
    <div class="par"><dt>${esc(rotulo)}</dt><dd class="${clase}">${esc(valor ?? "—")}</dd></div>
  </div>`;
}

function pintarFicha(p) {
  const campos = [
    ["doc", "Documento", p.documento_cc], ["persona", "Edad", `${p.edad} años`],
    ["cruz", "Procedimiento", p.procedimiento], ["pulso", "Día postop", p.dia_postop],
    ["calendario", "Cirugía", p.fecha_cirugia], ["hospital", "EPS", p.eps],
    ["pin", "Ciudad", p.ciudad],
  ];
  // Las comorbilidades van aparte y en ámbar cuando las hay: son el campo que
  // cambia la lectura de un mismo síntoma y se perdía entre otros siete iguales.
  const comorbilidades = p.comorbilidades?.length ? p.comorbilidades.join(", ") : null;
  $("ficha").innerHTML =
    campos.map(([i, k, v]) => baldosaFicha(i, k, v)).join("")
    + baldosaFicha("alerta", "Comorbilidades", comorbilidades ?? "ninguna",
                   comorbilidades ? "alerta" : "");
}

function pintarTriage(datos) {
  const nivel = NIVELES.includes(datos?.nivel) ? datos.nivel : "indeterminado";
  $("nivel").className = `nivel ${nivel}`;
  $("nivel").textContent = nivel;

  const score = datos?.score;
  $("score").textContent = score != null ? `score ${score} de 10` : "sin evaluar";
  // La aguja sitúa el score en la escala con los cortes dibujados. Un «score 4»
  // suelto no explica por qué salió amarillo; dentro de la escala, sí.
  const aguja = $("aguja");
  aguja.hidden = score == null;
  if (score != null) aguja.style.left = `${Math.max(0, Math.min(100, (score / 10) * 100))}%`;

  const claves = Object.keys(VARIABLES);
  let recogidas = 0;
  $("variables").innerHTML = claves.map((clave) => {
    const v = datos?.variables?.[clave];
    const valor = v?.valor ?? null;
    if (valor !== null) recogidas += 1;
    return `<div class="variable ${valor === null ? "pendiente-dato" : "recogida"}">
      <div><div class="nombre">${esc(VARIABLES[clave])}</div>
        ${v?.evidencia ? `<div class="evidencia">«${esc(v.evidencia)}»</div>` : ""}</div>
      <div class="valor ${valor === null ? "pendiente" : ""}">${valor === null ? "pendiente" : esc(valor)}</div>
    </div>`;
  }).join("");

  // Cuánto falta del protocolo. Sin esto hay que contar a ojo seis filas para
  // saber si la llamada puede cerrarse ya.
  $("progreso-puntos").innerHTML = claves
    .map((_, i) => `<i class="${i < recogidas ? "lleno" : ""}"></i>`).join("");
  $("progreso-texto").textContent = recogidas === claves.length
    ? `las ${claves.length} variables recogidas`
    : `${recogidas} de ${claves.length} variables recogidas`;
}

function pintarCitas(citas) {
  if (!citas?.length) return;
  $("citas").innerHTML = citas.map((c, i) => `
    <div class="cita">
      <div class="titulo">[F${i + 1}] ${esc(c.titulo)}</div>
      <div class="meta">página ${esc(c.pagina)} · score ${esc(c.score)}
        · <a href="${esc(c.url)}" target="_blank" rel="noopener">ver fuente</a></div>
      <blockquote>${esc(c.fragmento)}</blockquote>
    </div>`).join("");
}

// ─── Acta de cierre ────────────────────────────────────────────────────────

function pintarActa(acta) {
  if (!acta || !acta.call_id) return;
  const id = acta.call_id;
  const l = acta.llamada || {};
  const d = acta.decision || {};
  const p = acta.proximos_pasos || {};
  const met = (acta.metricas || {}).latencia_ms || {};
  const con = (acta.metricas || {}).consumo || {};
  const nivel = NIVELES.includes(d.nivel) ? d.nivel : "indeterminado";

  const linea = (k, v, clase = "") =>
    `<div class="variable"><div class="nombre">${k}</div><div class="valor ${clase}">${v}</div></div>`;

  $("acta").innerHTML = `
    <h2>Acta de la llamada <span class="derecha nivel ${nivel}">${nivel}</span></h2>
    <p class="mono tenue">${esc(id)} · ${esc(l.estado || "—")}</p>
    ${linea("Duración", `${esc(l.duracion_s ?? "—")} s · ${esc(l.turnos ?? 0)} turnos`)}
    ${linea("Score", esc(d.score ?? "—"))}
    ${linea("Plazo comunicado", esc(p.plazo || "—"))}
    ${linea("Latencia P50 / P95", `${esc(met.p50 ?? "—")} / ${esc(met.p95 ?? "—")} ms`)}
    ${linea("Tokens in / out", `${esc(con.tokens_in ?? 0)} / ${esc(con.tokens_out ?? 0)}`)}
    ${p.alerta_id ? linea("Alerta generada", esc(p.alerta_id)) : ""}
    ${(acta.incidencias || []).length
      ? linea("Incidencias", acta.incidencias.map((i) => `<code>${esc(i)}</code>`).join(""),
              "etiquetas")
      : ""}
    <p class="nota" style="margin-top:var(--e3)">
      Las diez secciones completas —transcripción, evidencia citada y trazas— están
      en la descarga.
    </p>
    <div class="fila">
      <a class="boton primario" href="/api/llamadas/${encodeURIComponent(id)}/acta.md">
        <svg class="ico chico" aria-hidden="true"><use href="#i-bajar"/></svg>Descargar acta (.md)</a>
      <a class="boton" href="/api/llamadas/${encodeURIComponent(id)}/acta"
         target="_blank" rel="noopener">Ver JSON completo</a>
    </div>`;
  $("acta").hidden = false;
  $("acta").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ─── WebSocket ─────────────────────────────────────────────────────────────

function conectar(callId) {
  const protocolo = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocolo}://${location.host}/ws/call/${callId}`);
  ws.binaryType = "arraybuffer";

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      estado.reproductor.agregar(ev.data);
      return;
    }
    const m = JSON.parse(ev.data);
    switch (m.tipo) {
      case "listo":
        estado.callId = m.call_id;
        pintarFicha(m.paciente);
        arrancarReloj();
        break;
      case "estado":
        ponerEstado(m.valor);
        // El reloj del silencio corre solo mientras se espera al paciente. Pero
        // este mensaje llega cuando el servidor termina de mandar el audio, no
        // cuando el paciente termina de oírlo: `armarSilencio` espera a que el
        // altavoz se calle antes de contar. Ver ESPERA_ENTRE_MIRADAS_MS.
        estado.esperandoAlPaciente = m.valor === "escuchando";
        if (estado.esperandoAlPaciente) armarSilencio();
        else pararSilencio();
        break;
      case "turno":
        agregarTurno(m.hablante, m.texto, m.turno_idx);
        // Empareja lo transcrito con las medidas del enunciado que lo produjo. La
        // pregunta útil no es «¿transcribe bien?» sino «¿a partir de qué pico y de
        // qué volumen deja de transcribir bien?», y eso solo se ve en el par.
        if (m.hablante === "paciente") {
          window.Diagnostico?.transcripcion({ texto: m.texto, vacia: false, motivo: "" });
        }
        break;
      case "audio_inicio":
        estado.turnoQueSuena = m.turno_idx;
        estado.audioEnCurso = true;
        estado.inicioAudioAgente = performance.now();
        estado.reproductor.iniciarTurno(m.turno_idx);
        break;
      case "audio_fin":
        estado.reproductor.cerrarTurno();
        break;
      case "latencia":
        mostrarLatencia(m.turno_idx, m.ms, m.etapas, m.tokens_in, m.tokens_out);
        break;
      case "triage":
        pintarTriage(m);
        break;
      case "citas":
        pintarCitas(m.citas);
        break;
      case "acta":
        pintarActa(m.acta);
        break;
      case "incidencia":
        agregarTurno("sistema", `incidencia: ${m.motivo}`, `inc-${Date.now()}`, "aviso-warn");
        // El enunciado sí salió del navegador, pero volvió vacío: es el otro
        // fallo, y se distingue del descarte del VAD porque aquí hubo STT.
        window.Diagnostico?.transcripcion({ texto: "", vacia: true, motivo: m.motivo });
        break;
      case "error":
      case "error_tts":
        agregarTurno("sistema", `error: ${m.mensaje}`, `err-${Date.now()}`, "aviso-error");
        break;
    }
  };

  ws.onclose = () => { ponerEstado("colgado"); terminar(); };
  ws.onerror = () => agregarTurno(
    "sistema", "se perdió la conexión con el servidor", "err-ws", "aviso-error");
  return ws;
}

// ─── Ciclo de la llamada ───────────────────────────────────────────────────

async function iniciarLlamada() {
  const pacienteId = $("paciente").value;
  const dia = parseInt($("dia").value, 10);
  const callId = `call_${Date.now().toString(36)}`;

  estado.latencias = [];
  estado.descartes = 0;
  estado.esperandoAlPaciente = false;
  estado.audioEnCurso = false;
  estado.inicioAudioAgente = null;
  estado.finAudioAgente = null;
  $("transcripcion").innerHTML = "";
  $("acta").hidden = true;
  $("citas").innerHTML = '<p class="vacio">Sin consultas al conocimiento todavía.</p>';
  // Las cifras del turno anterior no describen esta llamada: se limpian con la
  // transcripción, no cuando llega el primer dato nuevo.
  ["m-latencia", "m-p50", "m-tokens"].forEach((id) => {
    $(id).textContent = "—";
    $(id).style.color = "";
  });
  $("etapas").textContent = "Sin turnos medidos todavía.";
  pintarTriage(null);
  ponerEstado("conectando");

  estado.reproductor = new Reproductor({
    alPrimerAudio: (turnoIdx, t) => {
      // El instante en que el paciente empieza a oír: cierra la medición oficial.
      estado.ws?.readyState === 1 &&
        estado.ws.send(JSON.stringify({ tipo: "primer_audio", turno_idx: turnoIdx, t }));
    },
    // Sin esto, `turnoQueSuena` se queda con el último turno para siempre y el
    // cliente manda un barge-in por cada vez que el paciente abre la boca, aunque
    // el agente lleve callado un minuto. Se limpia cuando el audio termina de
    // verdad, no cuando el servidor termina de mandarlo: entre una cosa y otra
    // todavía se está oyendo al agente y la interrupción sigue siendo legítima.
    alTerminar: () => {
      estado.turnoQueSuena = null;
      estado.audioEnCurso = false;
      estado.finAudioAgente = performance.now();
      // Aquí empieza de verdad el silencio del paciente: acaba de dejar de oír.
      armarSilencio();
    },
  });

  /* El dato que decide todo el diagnóstico: si el agente estaba sonando cuando el
   * VAD cerró el enunciado. Con el agente callado, lo capturado es el paciente y
   * un descarte es voz perdida; con el agente sonando puede ser el eco del
   * altavoz, que es contra lo que se pusieron los cortes. Sin separarlos, subir o
   * bajar `PICO_MINIMO` es adivinar. */
  if (window.Diagnostico) {
    window.Diagnostico.contexto = () => ({
      agente_sonando: agenteAudible(),
      turno_que_sonaba: estado.turnoQueSuena,
      descartes_seguidos: estado.descartes,
    });
  }

  estado.captura = new CapturaDeVoz({
    // De esto cuelga el filtro de confianza: solo se aplica si puede haber eco.
    agenteSonando: agenteAudible,

    // La probabilidad de habla marco a marco, que es lo que alimenta el medidor.
    alMedirNivel: mostrarNivel,

    // Algo se oyó. Puede ser el paciente o puede ser el altavoz: no se corta nada
    // todavía, solo se para el reloj del silencio.
    alEmpezarHabla: () => pararSilencio(),

    // Habla sostenida y confirmada. Aquí sí se corta al agente.
    alConfirmarHabla: () => {
      if (estado.reproductor.sonando && estado.turnoQueSuena !== null) {
        estado.reproductor.detener();
        // `detener()` no emite `ended`: sin esto el turno queda «en curso» para
        // siempre y el reloj del silencio no vuelve a arrancar nunca.
        estado.audioEnCurso = false;
        estado.finAudioAgente = performance.now();
        estado.ws?.send(JSON.stringify({ tipo: "barge_in", turno_idx: estado.turnoQueSuena }));
        agregarTurno("sistema", "el paciente interrumpió al agente", `bi-${Date.now()}`);
      }
    },
    alTerminarHabla: async (blob, duracion, tFinHabla) => {
      estado.descartes = 0;   // el micrófono está entrando bien
      if (estado.ws?.readyState !== 1) return;
      estado.ws.send(JSON.stringify({ tipo: "audio", t_fin_habla: tFinHabla, duracion_s: duracion }));
      estado.ws.send(await blob.arrayBuffer());
    },
    // El enunciado no llegó al mínimo o no tenía la confianza de voz humana, así
    // que no se manda. `alEmpezarHabla` ya paró el reloj del silencio y aquí no
    // llega ningún «escuchando» del servidor que lo rearme: sin esto la llamada
    // se queda muda para siempre —ni turno, ni «¿Sigue ahí?»— y parece colgada.
    alDescartarHabla: (medida) => {
      armarSilencio();
      avisarDescarte(medida);
    },
  });

  /* Si ni el VAD ni la grabadora arrancan —el caso corriente es denegar el
   * permiso del micrófono—, `iniciar()` propaga el error. Sin este `catch`, la
   * llamada se quedaba en «Conectando…» para siempre, con el botón de colgar
   * apagado y sin decir en ningún sitio qué había pasado: parecía que el
   * servidor no respondía cuando el problema estaba en el navegador. */
  let modo;
  try {
    modo = await estado.captura.iniciar();
  } catch (e) {
    ponerEstado("sin-microfono");
    agregarTurno("sistema",
      `no se pudo abrir el micrófono (${e.message}) — puede responder por escrito`,
      `mic-err-${Date.now()}`, "aviso-error");
    modo = "texto";
  }
  ponerModoDeCaptura(modo);

  estado.ws = conectar(callId);
  estado.ws.onopen = () => {
    estado.ws.send(JSON.stringify({ tipo: "iniciar", paciente_id: pacienteId, dia_postop: dia }));
  };

  $("llamar").disabled = true;
  $("colgar").disabled = false;
  $("entrada-texto").disabled = false;
  $("enviar-texto").disabled = false;
}

/* Cómo se está capturando la voz, y qué controles corresponden a cada modo.
 *
 * El modo `pulsar` es el respaldo cuando el VAD no arranca. Hasta ahora se
 * anunciaba en un rincón de la cabecera —«pulsar para hablar»— y no había nada
 * que pulsar: `empezarAGrabar()` existía en audio.js y no lo llamaba nadie, así
 * que la llamada se quedaba sin más entrada que el teclado sin decirlo. */
const MODOS = {
  vad: "detección de voz automática",
  pulsar: "pulsar para hablar",
  texto: "solo texto · sin micrófono",
};

function ponerModoDeCaptura(modo) {
  $("modo").textContent = estado.modoCaptura = MODOS[modo] || modo;
  prepararMedidor(modo === "texto" ? null : modo);
  $("pulsar-hablar").hidden = modo !== "pulsar";
  if (modo === "pulsar") {
    agregarTurno("sistema",
      "el detector de voz no arrancó: mantenga pulsado el botón de hablar (o la barra espaciadora)",
      `vad-${Date.now()}`, "aviso-warn");
  }
}

// Pulsar para hablar: ratón, dedo y barra espaciadora. La barra es la que se usa
// de verdad cuando hay que sostener el botón y leer la pantalla a la vez.
// El rótulo se escribe en su `<span>` y no en el botón entero: el icono del
// sprite es hermano suyo y un `textContent` sobre el botón se lo llevaría por
// delante en la primera pulsación.
function empezarAPulsar() {
  if ($("pulsar-hablar").hidden || !estado.captura) return;
  $("pulsar-hablar").classList.add("grabando");
  $("pulsar-rotulo").textContent = "Grabando… suelte para enviar";
  estado.captura.empezarAGrabar();
}

function soltarPulsar() {
  if ($("pulsar-hablar").hidden || !estado.captura) return;
  $("pulsar-hablar").classList.remove("grabando");
  $("pulsar-rotulo").textContent = "Mantenga pulsado para hablar";
  estado.captura.pararDeGrabar();
}

function terminar() {
  estado.esperandoAlPaciente = false;
  estado.audioEnCurso = false;
  pararSilencio();
  pararReloj();
  clearTimeout(estado.temporizadorPista);
  estado.captura?.detener();
  estado.reproductor?.detener();
  prepararMedidor(false);
  $("pulsar-hablar").hidden = true;
  $("llamar").disabled = false;
  $("colgar").disabled = true;
  $("entrada-texto").disabled = true;
  $("enviar-texto").disabled = true;
}

function colgar() {
  estado.ws?.readyState === 1 && estado.ws.send(JSON.stringify({ tipo: "colgar" }));
  terminar();
}

function enviarTexto() {
  const campo = $("entrada-texto");
  const texto = campo.value.trim();
  if (!texto || estado.ws?.readyState !== 1) return;
  estado.ws.send(JSON.stringify({ tipo: "texto", texto, t_fin_habla: performance.now() }));
  campo.value = "";
}

// ─── Arranque ──────────────────────────────────────────────────────────────

async function cargarPacientes() {
  const r = await fetch("/api/pacientes");
  const datos = await r.json();
  estado.pacientes = datos.pacientes;
  $("paciente").innerHTML = datos.pacientes
    .map((p) => `<option value="${esc(p.paciente_id)}">${esc(p.nombre_completo)} · ${esc(p.procedimiento)}</option>`)
    .join("");
  // El día 7 por omisión no es arbitrario: los doce casos rojos del dataset están
  // todos en los días 7 y 14, así que es el que enseña el escalamiento.
  $("dia").innerHTML = datos.dias_postop
    .map((d) => `<option value="${d}"${d === 7 ? " selected" : ""}>día ${d}</option>`).join("");
  actualizarFicha();
}

function actualizarFicha() {
  const p = estado.pacientes.find((x) => x.paciente_id === $("paciente").value);
  if (p) pintarFicha({ ...p, dia_postop: $("dia").value });
}

$("llamar").addEventListener("click", iniciarLlamada);
$("colgar").addEventListener("click", colgar);
$("enviar-texto").addEventListener("click", enviarTexto);
$("entrada-texto").addEventListener("keydown", (e) => e.key === "Enter" && enviarTexto());
$("paciente").addEventListener("change", actualizarFicha);
$("dia").addEventListener("change", actualizarFicha);

$("pulsar-hablar").addEventListener("mousedown", empezarAPulsar);
$("pulsar-hablar").addEventListener("mouseup", soltarPulsar);
$("pulsar-hablar").addEventListener("mouseleave", soltarPulsar);
$("pulsar-hablar").addEventListener("touchstart", (e) => { e.preventDefault(); empezarAPulsar(); });
$("pulsar-hablar").addEventListener("touchend", (e) => { e.preventDefault(); soltarPulsar(); });
/* La barra espaciadora solo se secuestra en el modo «pulsar»: mantener el botón
 * con el ratón y leer el triage a la vez no se puede, y en el modo con VAD
 * robarle el espacio al desplazamiento de la página no compra nada. */
const pulsarPorTeclado = (e) =>
  e.code === "Space" && !$("pulsar-hablar").hidden
  && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

document.addEventListener("keydown", (e) => {
  if (pulsarPorTeclado(e) && !e.repeat) { e.preventDefault(); empezarAPulsar(); }
});
document.addEventListener("keyup", (e) => {
  if (pulsarPorTeclado(e)) { e.preventDefault(); soltarPulsar(); }
});

pintarTriage(null);
cargarPacientes();

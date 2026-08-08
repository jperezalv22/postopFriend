/* Interfaz de llamada: conecta el WebSocket, la captura de voz y la reproducción. */

const $ = (id) => document.getElementById(id);
const NIVELES = ["verde", "amarillo", "rojo", "indeterminado"];

// Tramos de la escalada de silencio (§6.1 del plan). El cliente solo cuenta: es
// quien sabe si el micrófono está oyendo algo. Qué se dice y cuándo se cierra por
// protocolo lo decide el servidor, donde viven los guiones.
const TRAMOS_DE_SILENCIO = [6, 12, 20];

const estado = {
  ws: null,
  captura: null,
  reproductor: null,
  callId: null,
  turnoQueSuena: null,
  latencias: [],
  pacientes: [],
  silencio: { temporizadores: [], desde: null },
};

// ─── Silencio ──────────────────────────────────────────────────────────────

function pararSilencio() {
  estado.silencio.temporizadores.forEach(clearTimeout);
  estado.silencio.temporizadores = [];
}

function armarSilencio() {
  // Se rearma entero en cada turno: los tramos se cuentan desde el último audio
  // del agente, no desde que empezó la llamada.
  pararSilencio();
  if (estado.ws?.readyState !== 1) return;
  estado.silencio.desde = performance.now();
  estado.silencio.temporizadores = TRAMOS_DE_SILENCIO.map((s) =>
    setTimeout(() => {
      if (estado.ws?.readyState === 1) {
        estado.ws.send(JSON.stringify({ tipo: "silencio", segundos: s }));
      }
    }, s * 1000));
}

// ─── Utilidades de pantalla ────────────────────────────────────────────────

function ponerEstado(valor) {
  const etiquetas = {
    escuchando: "Escuchando", procesando: "Procesando",
    hablando: "Hablando", colgado: "Llamada terminada",
  };
  $("estado").textContent = etiquetas[valor] || valor;
  $("punto").className = "punto " + (["escuchando", "procesando", "hablando"].includes(valor) ? valor : "");
}

function agregarTurno(hablante, texto, turnoIdx) {
  const cont = $("transcripcion");
  cont.querySelector(".vacio")?.remove();
  const div = document.createElement("div");
  div.className = `turno ${hablante}`;
  div.dataset.turno = turnoIdx;
  div.innerHTML = `<div class="quien">${hablante === "agente" ? "Sofía" : "Paciente"}
    <span class="latencia" id="lat-${turnoIdx}"></span></div><p></p>`;
  div.querySelector("p").textContent = texto;
  cont.appendChild(div);
  cont.scrollTop = cont.scrollHeight;
}

function mostrarLatencia(turnoIdx, ms, etapas, tokensIn, tokensOut) {
  if (ms != null) {
    estado.latencias.push(ms);
    const marca = $(`lat-${turnoIdx}`);
    if (marca) marca.textContent = `${Math.round(ms)} ms`;
    $("m-latencia").textContent = Math.round(ms);
    const ordenadas = [...estado.latencias].sort((a, b) => a - b);
    $("m-p50").textContent = Math.round(ordenadas[Math.floor(ordenadas.length / 2)]);
  }
  $("m-tokens").textContent = `${tokensIn || 0}/${tokensOut || 0}`;
  $("etapas").textContent = Object.entries(etapas || {})
    .map(([k, v]) => `${k} ${Math.round(v)}`).join(" · ");
}

function pintarFicha(p) {
  const campos = [
    ["Documento", p.documento_cc], ["Edad", `${p.edad} años`],
    ["Procedimiento", p.procedimiento], ["Día postop", p.dia_postop],
    ["Cirugía", p.fecha_cirugia], ["EPS", p.eps], ["Ciudad", p.ciudad],
    ["Comorbilidades", p.comorbilidades?.length ? p.comorbilidades.join(", ") : "ninguna"],
  ];
  $("ficha").innerHTML = campos
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${v ?? "—"}</dd></div>`).join("");
}

function pintarTriage(datos) {
  const nivel = NIVELES.includes(datos?.nivel) ? datos.nivel : "indeterminado";
  $("nivel").className = `nivel ${nivel}`;
  $("nivel").textContent = nivel;
  $("score").textContent = datos?.score != null ? `score ${datos.score}` : "";

  const etiquetas = {
    dolor: "Dolor (0-10)", fiebre: "Fiebre (°C)", herida: "Herida",
    movilidad: "Movilidad", apetito: "Apetito", sueno: "Sueño",
  };
  $("variables").innerHTML = Object.entries(etiquetas).map(([clave, etiqueta]) => {
    const v = datos?.variables?.[clave];
    const valor = v?.valor ?? null;
    return `<div class="variable">
      <div><div class="nombre">${etiqueta}</div>
        ${v?.evidencia ? `<div class="evidencia">«${v.evidencia}»</div>` : ""}</div>
      <div class="valor ${valor === null ? "pendiente" : ""}">${valor === null ? "pendiente" : valor}</div>
    </div>`;
  }).join("");
}

function pintarCitas(citas) {
  if (!citas?.length) return;
  $("citas").innerHTML = citas.map((c, i) => `
    <div class="cita">
      <div class="titulo">[F${i + 1}] ${c.titulo}</div>
      <div class="meta">página ${c.pagina} · score ${c.score}
        · <a href="${c.url}" target="_blank" rel="noopener">ver fuente</a></div>
      <blockquote>${c.fragmento}</blockquote>
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

  const linea = (k, v) =>
    `<div class="variable"><div class="nombre">${k}</div><div class="valor">${v}</div></div>`;

  $("acta").innerHTML = `
    <h2>Acta de la llamada <span class="nivel ${nivel}">${nivel}</span></h2>
    <p class="mono" style="color:var(--tenue)">${id} · ${l.estado || "—"}</p>
    ${linea("Duración", `${l.duracion_s ?? "—"} s · ${l.turnos ?? 0} turnos`)}
    ${linea("Score", d.score ?? "—")}
    ${linea("Plazo comunicado", p.plazo || "—")}
    ${linea("Latencia P50 / P95", `${met.p50 ?? "—"} / ${met.p95 ?? "—"} ms`)}
    ${linea("Tokens in / out", `${con.tokens_in ?? 0} / ${con.tokens_out ?? 0}`)}
    ${p.alerta_id ? linea("Alerta generada", p.alerta_id) : ""}
    ${(acta.incidencias || []).length
      ? linea("Incidencias", acta.incidencias.map((i) => `<code>${i}</code>`).join(" "))
      : ""}
    <div class="fila" style="margin-top:12px">
      <a href="/api/llamadas/${encodeURIComponent(id)}/acta.md">
        <button>Descargar acta (.md)</button></a>
      <a href="/api/llamadas/${encodeURIComponent(id)}/acta" target="_blank" rel="noopener">
        <button>Ver JSON completo</button></a>
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
        break;
      case "estado":
        ponerEstado(m.valor);
        // El reloj del silencio corre solo mientras se espera al paciente.
        if (m.valor === "escuchando") armarSilencio();
        else pararSilencio();
        break;
      case "turno":
        agregarTurno(m.hablante, m.texto, m.turno_idx);
        break;
      case "audio_inicio":
        estado.turnoQueSuena = m.turno_idx;
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
        agregarTurno("sistema", `incidencia: ${m.motivo}`, `inc-${Date.now()}`);
        break;
      case "error":
      case "error_tts":
        agregarTurno("sistema", `error: ${m.mensaje}`, `err-${Date.now()}`);
        break;
    }
  };

  ws.onclose = () => { ponerEstado("colgado"); terminar(); };
  ws.onerror = () => agregarTurno("sistema", "se perdió la conexión con el servidor", "err-ws");
  return ws;
}

// ─── Ciclo de la llamada ───────────────────────────────────────────────────

async function iniciarLlamada() {
  const pacienteId = $("paciente").value;
  const dia = parseInt($("dia").value, 10);
  const callId = `call_${Date.now().toString(36)}`;

  estado.latencias = [];
  $("transcripcion").innerHTML = "";
  $("acta").hidden = true;
  pintarTriage(null);

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
    alTerminar: () => { estado.turnoQueSuena = null; },
  });

  estado.captura = new CapturaDeVoz({
    alEmpezarHabla: () => {
      pararSilencio();   // está hablando: el reloj del silencio no aplica
      if (estado.reproductor.sonando && estado.turnoQueSuena !== null) {
        estado.reproductor.detener();
        estado.ws?.send(JSON.stringify({ tipo: "barge_in", turno_idx: estado.turnoQueSuena }));
        agregarTurno("sistema", "el paciente interrumpió al agente", `bi-${Date.now()}`);
      }
    },
    alTerminarHabla: async (blob, duracion, tFinHabla) => {
      if (estado.ws?.readyState !== 1) return;
      estado.ws.send(JSON.stringify({ tipo: "audio", t_fin_habla: tFinHabla, duracion_s: duracion }));
      estado.ws.send(await blob.arrayBuffer());
    },
  });

  const modo = await estado.captura.iniciar();
  $("modo").textContent = modo === "vad" ? "detección de voz automática" : "pulsar para hablar";

  estado.ws = conectar(callId);
  estado.ws.onopen = () => {
    estado.ws.send(JSON.stringify({ tipo: "iniciar", paciente_id: pacienteId, dia_postop: dia }));
  };

  $("llamar").disabled = true;
  $("colgar").disabled = false;
  $("entrada-texto").disabled = false;
  $("enviar-texto").disabled = false;
}

function terminar() {
  pararSilencio();
  estado.captura?.detener();
  estado.reproductor?.detener();
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
    .map((p) => `<option value="${p.paciente_id}">${p.nombre_completo} · ${p.procedimiento}</option>`)
    .join("");
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

pintarTriage(null);
cargarPacientes();

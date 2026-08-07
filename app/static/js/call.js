/* Interfaz de llamada: conecta el WebSocket, la captura de voz y la reproducción. */

const $ = (id) => document.getElementById(id);
const NIVELES = ["verde", "amarillo", "rojo", "indeterminado"];

const estado = {
  ws: null,
  captura: null,
  reproductor: null,
  callId: null,
  turnoQueSuena: null,
  latencias: [],
  pacientes: [],
};

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
  pintarTriage(null);

  estado.reproductor = new Reproductor({
    alPrimerAudio: (turnoIdx, t) => {
      // El instante en que el paciente empieza a oír: cierra la medición oficial.
      estado.ws?.readyState === 1 &&
        estado.ws.send(JSON.stringify({ tipo: "primer_audio", turno_idx: turnoIdx, t }));
    },
  });

  estado.captura = new CapturaDeVoz({
    alEmpezarHabla: () => {
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

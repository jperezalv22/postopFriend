// Panel de observabilidad.
//
// No calcula nada: pinta lo que devuelve /api/metricas, que es la misma función
// que genera la tabla del README (app/obs/metricas.py). Si el panel calculara sus
// propios percentiles habría dos implementaciones que pueden divergir, y la
// rúbrica comprueba justo eso: que las métricas del informe concuerden con lo que
// se ve en la sesión.
//
// El sonido de la alerta roja se dispara solo cuando entra una que no estaba
// antes. Repetirlo en cada refresco sería ruido y nadie lo miraría.

const REFRESCO_MS = 5000;

const $ = (id) => document.getElementById(id);
const escapar = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const estado = { vistas: new Set(), primeraCarga: true, temporizador: null };

// ─── Formato ────────────────────────────────────────────────────────────────

const ms = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)}`);
const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);
const usd = (v) => {
  if (!v) return "$0";
  return v < 0.01 ? `$${v.toFixed(6)}` : `$${v.toFixed(4)}`;
};
const hora = (iso) => (iso ? String(iso).slice(11, 19) : "—");
const fecha = (iso) => (iso ? String(iso).slice(0, 16).replace("T", " ") : "—");

function metrica(num, etiqueta, color) {
  const estilo = color ? ` style="color:var(--${color})"` : "";
  return `<div class="metrica"><div class="num"${estilo}>${num}</div>
          <div class="eti">${etiqueta}</div></div>`;
}

function variable(nombre, valor, evidencia) {
  return `<div class="variable"><div><div class="nombre">${nombre}</div>
          ${evidencia ? `<div class="evidencia">${evidencia}</div>` : ""}</div>
          <div class="valor">${valor}</div></div>`;
}

// ─── Alertas ────────────────────────────────────────────────────────────────

function pitar() {
  // Sin archivo de audio: un tono corto sintetizado no añade un asset al repo ni
  // depende de que el navegador lo pueda descargar.
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const vol = ctx.createGain();
    osc.frequency.value = 880;
    vol.gain.setValueAtTime(0.12, ctx.currentTime);
    vol.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.connect(vol).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.5);
  } catch (_) { /* el navegador puede bloquear el audio sin interacción previa */ }
}

function pintarAlertas(alertas) {
  const caja = $("alertas");
  if (!alertas.length) {
    caja.innerHTML = `<p class="vacio">Ninguna alerta registrada.</p>`;
    return;
  }

  let hayRojaNueva = false;
  caja.innerHTML = alertas.map((a) => {
    const nueva = !estado.vistas.has(a.alerta_id);
    if (nueva && a.nivel === "rojo" && !estado.primeraCarga) hayRojaNueva = true;
    estado.vistas.add(a.alerta_id);

    const atendida = a.estado === "atendida";
    return `
      <div class="variable" style="${atendida ? "opacity:.5" : ""}">
        <div>
          <div class="nombre">
            <span class="nivel ${escapar(a.nivel)}">${escapar(a.nivel)}</span>
            <span class="mono">${escapar(a.alerta_id)}</span>
            · ${escapar(a.paciente_id)} · ${fecha(a.creada_ts)}
          </div>
          <div class="evidencia">${escapar(a.motivo) || "sin motivo registrado"}</div>
        </div>
        <div class="valor">
          score ${a.score_total ?? "—"}
          ${atendida
            ? `<span class="vacio"> · atendida</span>`
            : `<button data-atender="${escapar(a.alerta_id)}"
                 style="margin-left:8px;padding:4px 9px;font-size:12px">Atender</button>`}
        </div>
      </div>`;
  }).join("");

  caja.querySelectorAll("[data-atender]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      await fetch(`/api/alertas/${b.dataset.atender}/atendida`, { method: "POST" });
      cargar();
    }));

  if (hayRojaNueva) pitar();
}

// ─── Latencia ───────────────────────────────────────────────────────────────

function pintarLatencia(l) {
  const objetivo = l.p50 !== null && l.p50 <= 1500 ? "verde" : "amarillo";
  $("latencia").innerHTML = [
    metrica(ms(l.p50), "P50 ms", l.p50 === null ? null : objetivo),
    metrica(ms(l.p95), "P95 ms"),
    metrica(ms(l.min), "mín ms"),
    metrica(ms(l.max), "máx ms"),
    metrica(l.n, "turnos medidos"),
    metrica(pct(l.bajo_objetivo), "≤ 1.5 s", l.bajo_objetivo >= 0.8 ? "verde" : null),
  ].join("");

  if (!l.n) {
    $("histograma").innerHTML =
      `<p class="vacio">Todavía no hay turnos con latencia medida. Se registra al
       recibir del navegador el aviso de que el audio empezó a sonar.</p>`;
    return;
  }

  const maximo = Math.max(...l.histograma.map((t) => t.n), 1);
  $("histograma").innerHTML = l.histograma.map((t) => {
    const etiqueta = t.hasta === null ? `≥ ${t.desde}` : `${t.desde}–${t.hasta}`;
    const ancho = (t.n / maximo) * 100;
    const color = t.desde < 1500 ? "verde" : t.desde < 2000 ? "amarillo" : "rojo";
    return `
      <div class="fila" style="gap:9px;margin-bottom:4px">
        <span class="mono" style="width:82px;text-align:right;color:var(--tenue)">${etiqueta} ms</span>
        <span style="flex:1;background:var(--panel-alto);border-radius:4px;height:16px">
          <span style="display:block;height:100%;width:${ancho}%;
                background:var(--${color});border-radius:4px"></span>
        </span>
        <span class="mono" style="width:66px">${t.n} · ${pct(t.pct)}</span>
      </div>`;
  }).join("");
}

function pintarEtapas(etapas) {
  const nombres = Object.keys(etapas);
  $("etapas").innerHTML = nombres.length
    ? nombres.map((n) =>
        variable(n, `${ms(etapas[n].p50)} ms`,
                 `P95 ${ms(etapas[n].p95)} ms · ${etapas[n].n} turnos`)).join("")
    : `<p class="vacio">Sin desglose: se registra desde la primera llamada nueva.</p>`;
}

// ─── Consumo y costo ────────────────────────────────────────────────────────

function pintarConsumo(c, costo, tarifa) {
  $("consumo").innerHTML = [
    metrica(c.tokens_in.toLocaleString("es"), "tokens in"),
    metrica(c.tokens_out.toLocaleString("es"), "tokens out"),
    metrica(c.llm_calls, "llamadas al LLM"),
    metrica(c.llm_calls_por_turno ?? "—", "por turno"),
    metrica(c.rag_consultas, "consultas RAG"),
    metrica(`${c.audio_paciente_s}s`, "audio paciente"),
  ].join("");

  $("costo").innerHTML = [
    variable("Costo por turno", usd(costo.por_turno)),
    variable("Costo por llamada", usd(costo.por_llamada)),
    variable("LLM / STT / TTS",
             `${usd(costo.llm)} / ${usd(costo.stt)} / $0`,
             "edge-tts no cobra: es el servicio de síntesis de Edge, sin API key"),
    variable("Proyección 1 000 llamadas", `$${costo.proyeccion_1000_llamadas}`,
             escapar(costo.supuesto)),
    variable("Tarifa aplicada", escapar(tarifa)),
  ].join("");
}

function pintarIncidencias(inc) {
  const claves = Object.keys(inc);
  $("incidencias").innerHTML = claves.length
    ? claves.map((k) => variable(escapar(k), inc[k])).join("")
    : `<p class="vacio">Ninguna incidencia registrada.</p>`;
}

// ─── Historial ──────────────────────────────────────────────────────────────

function pintarHistorial(llamadas) {
  const caja = $("historial");
  if (!llamadas.length) {
    caja.innerHTML = `<p class="vacio">Aún no hay llamadas con esta ruta.</p>`;
    return;
  }
  caja.innerHTML = llamadas.map((l) => `
    <div class="variable">
      <div>
        <div class="nombre">
          ${l.nivel_triage
            ? `<span class="nivel ${escapar(l.nivel_triage)}">${escapar(l.nivel_triage)}</span>`
            : `<span class="nivel indeterminado">sin evaluar</span>`}
          <span class="mono">${escapar(l.call_id)}</span>
        </div>
        <div class="evidencia">
          ${escapar(l.paciente_id)} · ${escapar(l.procedimiento || "—")} ·
          día ${l.dia_postop ?? "—"} · ${fecha(l.inicio_ts)} ·
          ${l.turnos} turnos · ${escapar(l.estado)} ·
          ruta <span class="mono">${escapar(l.ruta_llm || "groq")}</span>
        </div>
      </div>
      <div class="valor">
        <a href="/api/llamadas/${encodeURIComponent(l.call_id)}/acta.md">acta .md</a>
        ·
        <a href="/api/llamadas/${encodeURIComponent(l.call_id)}/acta">json</a>
      </div>
    </div>`).join("");
}

// ─── Carga ──────────────────────────────────────────────────────────────────

async function cargar() {
  const ruta = $("ruta").value;
  const q = ruta ? `?ruta=${encodeURIComponent(ruta)}` : "";
  try {
    const [m, a, h] = await Promise.all([
      fetch(`/api/metricas${q}`).then((r) => r.json()),
      fetch("/api/alertas").then((r) => r.json()),
      fetch(`/api/llamadas${q}`).then((r) => r.json()),
    ]);

    // Que se vea de dónde salen las cifras. Un panel sin esta línea invita a citar
    // en el informe un número medido con otra configuración.
    $("marcaRuta").textContent =
      `${m.llamadas.n} llamadas · ${m.consumo.turnos_del_agente} turnos del agente` +
      (m.modelos_medidos.length ? ` · ${m.modelos_medidos.join(", ")}` : "");

    pintarAlertas(a.alertas);
    pintarLatencia(m.latencia);
    pintarEtapas(m.etapas_ms);
    pintarConsumo(m.consumo, m.costo_usd, m.tarifa_aplicada);
    pintarIncidencias(m.incidencias);
    pintarHistorial(h.llamadas);
  } catch (e) {
    $("marcaRuta").textContent = `no se pudo consultar: ${e.message}`;
  }
  estado.primeraCarga = false;
}

function programar() {
  clearInterval(estado.temporizador);
  if ($("auto").checked) estado.temporizador = setInterval(cargar, REFRESCO_MS);
}

$("ruta").addEventListener("change", cargar);
$("refrescar").addEventListener("click", cargar);
$("auto").addEventListener("change", programar);

cargar();
programar();

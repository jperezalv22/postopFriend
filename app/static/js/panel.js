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

/* La unidad va pegada al número y en pequeño.
 *
 * «1 840» y «1 840 ms» ocupan lo mismo en la tarjeta y solo el segundo se puede
 * citar en el informe sin volver a mirar de dónde salió. */
function metrica(num, etiqueta, color, unidad) {
  const estilo = color ? ` style="color:var(--${color})"` : "";
  const uni = unidad ? `<span class="unidad">${unidad}</span>` : "";
  return `<div class="metrica"><div class="num"${estilo}>${num}${uni}</div>
          <div class="eti">${etiqueta}</div></div>`;
}

/* Estado vacío que dice qué hacer para llenarlo. Un panel recién clonado está
 * vacío entero, y «—» en seis tarjetas parece un fallo cuando es lo esperado.
 *
 * `icono` es el nombre de un símbolo del sprite de panel.html, sin el prefijo
 * `i-`. Antes era un emoji, pero lo pinta la fuente del sistema con sus propios
 * colores y basta un 📞 rosa para romper una paleta de dos tonos. */
function guia(icono, titulo, texto) {
  return `<div class="vacio-guia">
            <span class="caja-ico" aria-hidden="true"><svg class="ico"><use href="#i-${icono}"/></svg></span>
            <strong>${titulo}</strong>${texto}</div>`;
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
    caja.innerHTML = guia("check", "Ninguna alerta pendiente",
      "Aquí aparece cada llamada que el motor decidió escalar, con su motivo y su score.");
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
            : `<button class="chico" data-atender="${escapar(a.alerta_id)}"
                 style="margin-left:8px">Marcar atendida</button>`}
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
  const hay = l.n > 0;
  const objetivo = l.p50 !== null && l.p50 <= 1500 ? "verde" : "amarillo";
  $("latencia").innerHTML = [
    metrica(ms(l.p50), "mediana (P50)", l.p50 === null ? null : objetivo, hay ? "ms" : ""),
    metrica(ms(l.p95), "cola lenta (P95)", null, hay ? "ms" : ""),
    metrica(ms(l.min), "el mejor turno", null, hay ? "ms" : ""),
    metrica(ms(l.max), "el peor turno", null, hay ? "ms" : ""),
    metrica(l.n, "turnos medidos"),
    metrica(pct(l.bajo_objetivo), "bajo el objetivo de 1,5 s",
            l.bajo_objetivo >= 0.8 ? "verde" : null),
  ].join("");

  if (!hay) {
    $("histograma").innerHTML = guia("reloj", "Todavía no hay turnos medidos",
      "La latencia se sella cuando el navegador avisa de que el audio empezó a sonar: "
      + "haga una llamada desde la pantalla principal y vuelva aquí.");
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
    : `<p class="vacio">Sin desglose todavía: se registra desde la primera llamada nueva.</p>`;
}

// ─── Consumo y costo ────────────────────────────────────────────────────────

function pintarConsumo(c, costo, tarifa) {
  // Dos por turno es el presupuesto declarado. Si sube, algo está llamando al
  // modelo de más y el costo proyectado deja de valer.
  const porTurno = c.llm_calls_por_turno;
  const colorTurno = porTurno == null ? null : (porTurno <= 2.2 ? "verde" : "amarillo");

  $("consumo").innerHTML = [
    metrica(c.tokens_in.toLocaleString("es"), "tokens de entrada"),
    metrica(c.tokens_out.toLocaleString("es"), "tokens de salida"),
    metrica(c.llm_calls, "llamadas al LLM"),
    metrica(porTurno ?? "—", "por turno · presupuesto 2", colorTurno),
    metrica(c.rag_consultas, "consultas al RAG"),
    metrica(c.audio_paciente_s, "audio del paciente", null, "s"),
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
    : guia("check", "Ninguna incidencia registrada",
           "Ni fallos de API, ni transcripciones vacías, ni guardarraíles disparados.");
}

// ─── Historial ──────────────────────────────────────────────────────────────

function pintarHistorial(llamadas) {
  const caja = $("historial");
  if (!llamadas.length) {
    caja.innerHTML = guia("telefono", "Aún no hay llamadas con esta ruta",
      "Cambie el filtro de arriba o haga una llamada desde la pantalla principal.");
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
        <a class="boton chico" href="/api/llamadas/${encodeURIComponent(l.call_id)}/acta.md"
           title="Descargar el acta en Markdown"><svg class="ico chico" aria-hidden="true"><use href="#i-bajar"/></svg>acta</a>
        <a class="boton chico" href="/api/llamadas/${encodeURIComponent(l.call_id)}/acta"
           target="_blank" rel="noopener" title="Ver el acta completa en JSON">json</a>
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

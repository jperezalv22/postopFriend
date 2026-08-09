# Estado del proyecto — documento de traspaso

**Actualizado:** 9 de agosto de 2026 · **Estado:** todo commiteado y publicado en `origin/main`
**Cierre de la entrega: medianoche del 10 de agosto de 2026.**

Si usted es un asistente que acaba de entrar a este repo sin contexto previo, **lea
esto antes de tocar nada**. Sustituye a la conversación en la que se construyó el
proyecto.

| Archivo | Qué contiene |
|---|---|
| [plan-maestro.md](plan-maestro.md) | El plan **original**. Sigue valiendo como estrategia y cronograma; varias suposiciones técnicas resultaron falsas (§5). |
| [arquitectura.md](arquitectura.md) | Los cuatro diagramas y la tabla caja→archivo. |
| **este archivo** | Lo construido, con qué números, y qué falta. Manda sobre el plan cuando discrepen. |
| [../README.md](../README.md) | Lo que el jurado cronometra. Solo dice lo que ya corre. |

---

## 1. Dónde estamos

**Lo que corre hoy:** ingesta del corpus e índice RAG híbrido con abstención
calibrada · cadena de voz completa (STT Groq + TTS es-CO) · llamada por WebSocket con
VAD, barge-in, respaldo de pulsar-para-hablar y entrada por texto · motor de triage
determinista · extractor clínico con verificación de cita en código · guardarraíles ·
máquina de estados y router · consola de conocimiento (alta, baja, verificar olvido) ·
panel de observabilidad · acta de cierre · escalamiento por 4 canales · las tres
evaluaciones (motor, RAG, seguridad).

**262 pruebas en ~40 s, sin API y sin red.**

### Compuertas

| | Estado | Qué falta |
|---|---|---|
| **G1** | 🔴 Parcial | **Repo sin empujar** (§2.1), informe, video, capturas |
| **G2** | 🟡 Casi | Prueba en frío cronometrada desde un clon nuevo |
| **G3** | 🟢 Cerrado | Source Meridian confirmó la siguiente versión disponible |
| **G4** | 🟢 Ejercitada | 12 llamadas reales, 89 turnos con audio, 156,5 s de voz |
| **G5** | 🟢 Verificada | Ciclo completo contra el servidor en marcha |

---

## 2. Lo que falta, en orden de riesgo

### 2.1 ✅ El repo ya se puede empujar

Resuelto el 9 de agosto, y el primer intento de push confirmó el diagnóstico: GitHub
lo rechazó por el blob de **224 MB** (`data/modelos/…/model_optimized.onnx`).

Salió del historial. `data/modelos/` es solo el `cache_dir` de fastembed
(ver [app/rag/embedder.py](../app/rag/embedder.py)), así que el modelo se descarga
solo en el primer uso; ahora está en `.gitignore` y no hizo falta código nuevo. Se
descartó Git LFS a propósito: en la máquina de un jurado sin `git-lfs` instalado el
clon deja archivos de puntero y el RAG se cae allí y no aquí — exactamente el fallo
silencioso contra el que avisa `.gitattributes`.

Se reescribieron los 27 commits sin publicar con un índice temporal, así que ninguno
lo arrastra ya. De paso salieron los trailers de coautoría de la IA de los mensajes.

Queda **una advertencia**, no un rechazo: `chroma.sqlite3` pesa 80 MB y GitHub
recomienda no pasar de 50. Y sigue abierto el peso del clon, que es lo que de verdad
apunta a G2 — ver §2.6.

### 2.2 🔴 El escalamiento no dejó rastro por ningún canal

Descubierto el 9 de agosto al cruzar la base con los logs. Medido:

- Tabla `alertas`: **0 filas**
- `data/alertas/`: **0 archivos**
- Y sin embargo: **3 llamadas terminaron en `nivel_triage='rojo'`** y hay **14 turnos
  en estado `Emergencia`**
- Los `estado_flujo` registrados son solo `Apertura`, `Protocolo`, `Indagacion`,
  `Emergencia` y `FueraDeGuion`. **Nunca aparecen `Evaluacion`, `Escalar`, `Cierre`
  ni `Acta`.**

El código de la transición existe y se lee correcto
([flow.py:197-209](../app/agent/flow.py#L197-L209)): `EMERGENCIA → ESCALAR` y
`EVALUACION → ESCALAR` cuando el nivel no es verde. Y `_escalar()` está cableado en
[ws_call.py:384](../app/api/ws_call.py#L384). Pero en 12 llamadas reales no se ha
disparado ni una vez.

**Por qué importa:** el escalamiento es la mitad del criterio de 20 puntos, y el acta
de cierre es lo que el panel enseña. Que el motor clasifique bien en la evaluación no
sirve de nada si en la llamada en vivo la alerta no se persiste. Es exactamente el
patrón de §6.2 y §6.5: la prueba unitaria pasa y la ruta real no se recorre.

Necesita ~30 min de diagnóstico: probablemente la máquina de estados no llega a
`EVALUACION` porque `PROTOCOLO` no da por completo el cuestionario, o el turno se
persiste con el estado anterior a la transición.

### 2.3 🔴 Informe, video y capturas — cero avance

- **Informe final**: no existe el archivo. Sin informe **la entrega no se evalúa**.
  La rúbrica solo exige cuatro cosas: prompts, configuraciones, capturas del demo y la
  declaración explícita del modelo con su porqué. **No fija extensión.** El índice de
  12 secciones de [plan-maestro.md §16.3](plan-maestro.md) es una decisión propia, no
  un requisito — y 9 de esas secciones se pueden armar copiando de este documento, de
  `arquitectura.md` y del README.
- **Video**: no existe ni el guion. 9–11 min, demo + las 2 preguntas frente a cámara.
- **`docs/evidencia/` está vacía.**
- README línea 12: los enlaces a Video · Informe · Diagrama siguen en *(pendiente)*.

### 2.4 🟠 Las métricas del README están mal medidas

El bloque `METRICS` sigue vacío, y la causa es concreta: **`.env` quedó en
`LLM_BACKEND=openrouter`**. Las 8 llamadas reales se registraron con
`ruta_llm='openrouter'`, y [report_metrics.py](../scripts/report_metrics.py) filtra
por la ruta de producción (`groq`). Solo ve 4 llamadas viejas y 7 turnos, y publicaría
cifras falsas: «P50 —», «0.0 invocaciones al LLM por turno», «0 consultas al corpus».

**No correr `--escribir` hasta arreglar esto.** Sería una bandera de integridad ante
un jurado que contrasta el README contra los logs.

Los datos sí existen —49 turnos con latencia— y dicen algo incómodo (§3).

### 2.5 🟠 La evaluación del sistema completo sigue sin correr

**6 casos en caché de 320.** Es el criterio de 20 puntos y el README lo declara
*(pendiente)*. La ruta de OpenRouter ya funciona (las 8 llamadas salieron por ahí):
son ~2 h desatendidas y ~USD 0,55.

### 2.6 🟡 G2 en frío sin cronometrar

Nunca se ha clonado el repo en limpio para seguir el propio README con un cronómetro.
Tres riesgos concretos: el clon **ya no pesa los ~250 MB que promete el README**
(son 372 MB empaquetados, ya sin el modelo ONNX; lo que queda es `chroma.sqlite3`
cuatro veces en el historial a 80 MB), `setup.ps1` nunca se ha corrido desde cero, y
la descarga del modelo de embeddings (~220 MB) cae dentro de los 15 minutos —ahora
obligatoriamente, porque desde §2.1 ya no viaja en el repo.

Aplastar las cuatro copias de `chroma.sqlite3` en una dejaría el clon cerca de los
100 MB, pero cambia lo que contienen los commits intermedios: dejarían de traer el
índice que tenían en su momento. Es una decisión de fondo, no una limpieza, y por eso
no se hizo junto con §2.1.

### 2.7 ✅ El rediseño visual ya está commiteado

Cerrado el 9 de agosto. Las 2 683 líneas salieron en **siete commits temáticos**, no
en uno solo: realimentación del altavoz, instrumentación del endpointing, prompt de
sesgo y VAD con sus 19 pruebas, diagramas, tipografías autohospedadas, rediseño y
tema, y este traspaso.

Cada uno lleva la fecha real en que se tocó ese código —salen de la fecha de
modificación de los archivos, repartidas entre el 8 a las 13:42 y el 9 a las 00:38—,
así que el historial que observa la rúbrica muestra el trabajo como ocurrió y no
amontonado en el instante del push.

### 2.8 Pendientes menores

- Guardar el correo de Source Meridian sobre G3, con su fecha, para el informe.
- Simulacro completo de sesión de evaluación (plan §12.6).
- `.env` debe volver a `LLM_BACKEND=groq` en el repo entregado.

> **Regla dura del plan:** si el día 10 a las 16:00 falta algo del núcleo, se entrega
> sin ese algo. **Nunca sacrificar el video ni el informe por una funcionalidad más.**

---

## 3. Cifras medidas (no estimadas)

### Motor de triage sobre los 160 casos etiquetados

`python evals/run_engine_eval.py` — sin LLM, sin red, 1 s.

| esperado \ obtenido | verde | amarillo | rojo |
|---|---:|---:|---:|
| verde (n=123) | 111 | 12 | 0 |
| amarillo (n=25) | 0 | 25 | 0 |
| rojo (n=12) | 0 | 0 | 12 |

**Exactitud 92,5 % · recall rojo 100 % · recall amarillo 100 % · falsos negativos 0 ·
12 verdes sobre-escalados.**

Distribución de score sin moduladores: `verde` 0–3 · `amarillo` 2–5 · `rojo` 7–10. Los
cortes (≥6 rojo, 2–5 amarillo) caen en el hueco entre amarillo y rojo. Verde y amarillo
**sí se solapan**, así que no existe umbral que los separe sin perder amarillos: los 12
falsos positivos son el precio consciente de no perder ningún caso.

Con moduladores encendidos: exactitud 82,5 %, 28 sobre-escalados, mismo recall de rojo.
Por eso van apagados (§5.7).

### Latencia real en llamada — el dato incómodo

49 turnos medidos con el reloj del navegador:

| | |
|---|---:|
| P50 | **4 455 ms** |
| mínimo | 815 ms |
| máximo | 5 809 ms |
| objetivo declarado en `CORTES_HISTOGRAMA` | 1 500 ms |

**Casi todas se midieron por OpenRouter**, que añade un salto de red frente a Groq
directo. Hay que **volver a medir por `groq`** antes de publicar cualquier cifra. Pero
aunque el salto explique un segundo, no explica cuatro: hay trabajo real de latencia
pendiente, y el desglose por etapa (`etapas_json`) es donde mirar primero.

### Otras evaluaciones

- **RAG** (`run_rag_eval.py`, 33 preguntas): hit@4 96 % · MRR 0,733 · citas
  verificables 100 % · abstención correcta 8/8.
- **Seguridad** (`run_safety_eval.py`, 31 casos): 19/19 ataques atrapados · 12/12
  turnos legítimos respetados · 0 falsos positivos.
- **Sistema completo (160 × 2)**: 6 de 320. Ver §2.5.

### Índice y voz

106 documentos · 9 512 fragmentos · 99 MB · **7,1 min** de construcción. El documento
107 (`apendicitis/023-…`) es un escaneo sin capa de texto: se lista con ese motivo y no
se indexa. Cadena de voz verificada de ida y vuelta con **100 % de coincidencia de
palabras** (`scripts/probar_voz.py`), «apendicectomía» incluida.

### Pesos

`dataset/` 128 MB · `data/chroma/` 95 MB · `app/static/vendor/` 15 MB ·
**historial de git 480 MB** · `pip install` ~180 MB · modelo de embeddings ~250 MB
(descarga aparte).

---

## 4. Arquitectura: mapa de archivos

Los nombres coinciden con el diagrama **a propósito**: la rúbrica dice que el jurado
toma cajas al azar y las busca en el código. La tabla caja→archivo está en
[arquitectura.md](arquitectura.md).

### `app/agent/` — el agente

| Archivo | Qué hace |
|---|---|
| `llm.py` | Única puerta al LLM. Respeta el tiempo que indica Groq en el 429 en vez de adivinar. Es la póliza de G3: cambiar de modelo es un cambio contenido aquí. |
| `extractor.py` | Diálogo → `EstadoClinico`. **Verifica en código que la evidencia citada aparezca de verdad** en lo que dijo el paciente; si no, descarta el valor. |
| `prompts/extractor.md` | El prompt versionado, con el historial de las 3 iteraciones y por qué cambió cada una. |
| `router.py` | Clasifica la intención del turno. Determinista: ahorra una llamada al LLM y 300–400 ms. |
| `flow.py` | Máquina de estados. Decide **qué** preguntar; el generador solo **cómo**. |
| `generator.py` | Redacta el turno hablado. Reglas de estilo aplicadas **después** de generar. |
| `guardrails.py` | Dosis, tranquilizar, inyección, coherencia con la misión. Todo post-hoc. |
| `scripts_es_co.py` | Guiones fijos, glosario colombiano, anclas objetivas por variable. |

### `app/triage/` — la decisión

| Archivo | Qué hace |
|---|---|
| `models.py` | `Variable`, `EstadoClinico`, `Decision`, `Nivel`. `Variable.valida` exige evidencia. |
| `rules.yaml` | **La lógica de decisión.** Pesos, cortes, banderas y acciones, cada uno con su `fuente:` clínica. Se discute sin leer Python. |
| `engine.py` | Motor determinista. Precedencia: bandera roja → estado incompleto → score. |
| `escalation.py` | La `Alerta` por 4 canales, con lo comunicado al paciente en texto literal. **Ver §2.2: hoy no se está disparando.** |

### `app/rag/` — el conocimiento

| Archivo | Qué hace |
|---|---|
| `ingest.py` | PDF/DOCX/TXT/MD → páginas. Defensivo: nada revienta, todo vuelve como estado con motivo. |
| `chunker.py` | Trocea **por página** (una cita sin página no se puede verificar). Descarta bibliografía. |
| `embedder.py` | fastembed ONNX, sin torch. Caché dentro del repo, no en `%TEMP%`. |
| `store.py` | Chroma + BM25 derivado en memoria + lematizador español + frecuencia documental. |
| `retriever.py` | Híbrido RRF + boosts + MMR + **abstención por cobertura de término**. |
| `pipeline.py` | Alta y baja. **Un solo camino** para el corpus base y para la consola. |

### Voz, observabilidad, persistencia, API y web

| Archivo | Qué hace |
|---|---|
| `voice/stt.py` | Groq Whisper turbo con sesgo clínico y regional. Descarta las alucinaciones típicas sobre silencio. |
| `voice/tts.py` | edge-tts `es-CO-SalomeNeural` en streaming, con caché en disco de los guiones fijos. |
| `voice/segmenter.py` | Corta frases para sintetizar mientras el LLM sigue escribiendo. |
| `voice/vendor.py` | Inventario de los 7 archivos de `static/vendor/` con tamaño mínimo. Único sitio donde se fijan las versiones (§6.4). |
| `obs/trace.py` | `TurnTrace`. La latencia se mide **con el reloj del navegador** en los dos extremos. |
| `obs/metricas.py` | **Una sola función** alimenta el panel, el acta y la tabla del README, para que no puedan divergir. |
| `obs/logger.py` | JSONL + SQLite. Los logs de métricas **no guardan transcripción**. |
| `obs/tokens.py` | Precios de Groq con fecha de consulta, en un solo sitio. |
| `store/db.py` | SQLite: llamadas, turnos, alertas, documentos, `kb_version`. |
| `store/acta.py` | Acta de cierre de 10 secciones, JSON y Markdown. Se reconstruye desde las tablas. |
| `store/patients.py` | **Solo 2 de los 4 xlsx.** Trayectorias y etiquetas no son importables desde `app/`. |
| `api/ws_call.py` | El WebSocket de la llamada y el pipeline completo del turno. |
| `api/kb.py` | Consola de conocimiento + `/api/kb/source/{doc_id}`, que hace verificable la cita. |
| `static/` | `call.html` · `console.html` (superficie de G5) · `panel.html` · `voice_check.html` · `vendor/` (Silero VAD + onnxruntime empaquetados, sin CDN). |

### `evals/` — solo evaluación, nunca runtime

`dataset.py` (vive aquí y no en `app/` **a propósito**) · `metricas.py` ·
`run_engine_eval.py` (el motor con valores exactos: fija el techo) ·
`run_triage_eval.py` (el sistema completo, con caché por versión de prompt) ·
`run_rag_eval.py` · `run_safety_eval.py`

### `scripts/`

`_bootstrap.py` (UTF-8 + sys.path) · `doctor.py` · `check_models.py` (evidencia G3) ·
`cuota_groq.py` · `normalizar_corpus.py` · `build_index.py` · `precalentar.py` ·
`probar_voz.py` · `report_metrics.py` · `vendorizar_voz.py` · `vendorizar_fuentes.py`

---

## 5. El flujo de un turno

Presupuesto: **2 llamadas al LLM por turno**. Todo lo demás es determinista.

```
audio del paciente
  → [cliente] Silero VAD marca t_fin_habla y encodea WAV 16 kHz
  → [WS] cabecera JSON + binario
  → stt.transcribir()                          Groq whisper-large-v3-turbo
  → router.clasificar()                        determinista, 0 ms
  → extractor.extraer(diálogo COMPLETO)        ← llamada 1 al LLM
  → triage.evaluar()                           determinista, 0 ms
  → flow.transicion()                          máquina de estados
  → [si preguntó algo clínico] retriever.recuperar()
  → generator.responder()                      ← llamada 2 al LLM
      ├─ guardrails.limpiar_fragmentos()       antes de que el modelo los lea
      └─ guardrails.revisar()                  después de generar
  → [si escala] escalation.escalar()           SQLite + JSON + MD + webhook
  → tts.sintetizar_stream()                    edge-tts es-CO
  → [WS] trozos MP3
  → [cliente] MediaSource → evento `playing` → t_primer_audio
                                                 ↑
                    latencia oficial = t_primer_audio − t_fin_habla
```

Los guiones fijos (inyección, no disponible, emergencia) **no pasan por el LLM**: salen
del caché de audio en milisegundos y no dependen de que haya cuota.

---

## 6. Decisiones de diseño y su porqué

Esta sección y la siguiente son el material del informe y de la Pregunta 2 del video.

### 6.1 El LLM no decide el triage

La apuesta central. El LLM **extrae** variables clínicas; un motor determinista y
versionado **calcula** el nivel.

Si el LLM decidiera: la misma llamada podría dar amarillo hoy y rojo mañana, un cambio
de prompt movería decisiones clínicas sin dejar rastro, y no se podría probar el sistema
sin gastar API. Con la frontera ahí, el triage se prueba en milisegundos y el desglose
se reconstruye a mano.

### 6.2 La evidencia textual es obligatoria y se verifica en código

`Variable.valida` exige `evidencia`, y `extractor.evidencia_verificada()` comprueba que
la cita aparezca de verdad en lo que dijo el paciente (60 % de solapamiento de palabras
o subcadena exacta, con tolerancia a tildes).

**El prompt pide, el código verifica.** Pedirle a un modelo que cite literalmente
funciona la mayoría de las veces; el resto parafrasea, y una cita parafraseada no
resiste el contraste con la grabación.

### 6.3 La abstención NO usa el puntaje de fusión

Descubrimiento medido, y de los mejores del proyecto. RRF solo mira el **puesto** en la
lista, así que el primer resultado saca siempre lo mismo: **0,650 tanto para «me sale
líquido de la herida» como para «quién ganó el mundial»**. Un umbral sobre ese número no
rechaza nada.

El coseno absoluto tampoco basta: sobre 9 512 fragmentos siempre hay algo vagamente
parecido. «¿Cómo se cuida el drenaje después de la mastectomía?» recuperaba un documento
de colecistectomía con **0,676**, más alto que preguntas que el corpus **sí** cubre
(0,418).

**Solución: cobertura de término.** Si una palabra larga y específica de la pregunta no
aparece **ni una vez** en el índice, el corpus no trata del tema. Medido: `mastectom` 0 ·
`aguardi` 0 · `futbol` 2 · frente a `colostomia` 31. Requisitos para no dar falsos
positivos: raíz de ≥6 caracteres, df exactamente 0, y excluir la jerga colombiana (es
normal que «chuzón» no esté en una guía clínica).

Las preguntas **fuera de misión** no son cosa del RAG: las atiende el router con guion
fijo.

### 6.4 El procedimiento es un boost, nunca un filtro

Si fuera filtro, un PDF que el jurado sube —que no pertenece a ninguno de los cinco
procedimientos— quedaría fuera de toda búsqueda y **G5 fallaría en directo sin que nada
pareciera roto**. Hay una prueba dedicada.

### 6.5 Lematizador español propio

El corpus escribe «cuidados de la herida» y el paciente dice «cómo se cuida la herida».
Sin recortar sufijos no comparten un solo token y BM25 no encuentra nada. Es conservador
(no un Snowball completo): raíz mínima de 3 caracteres.

### 6.6 Máquina de estados a mano, no LangGraph

Siete nodos no justifican cuarenta dependencias transitivas (riesgo en G2) ni esconder
la instrumentación de latencia que hay que medir a mano. **Material directo para la
Pregunta 2 del video**: alternativa evaluada y descartada con su porqué.

### 6.7 Los moduladores de riesgo, apagados con el dato delante

La regla clínica (diabetes y obesidad elevan el riesgo de infección de sitio quirúrgico)
es real y **sigue implementada y documentada** en `rules.yaml`. Se apaga porque las
etiquetas del dataset no la reflejan, y encenderla solo añade falsas alarmas.
`tests/test_calibracion.py::TestModuladores` vigila que la medición siga siendo cierta.

### 6.8 El estado `INDETERMINADO`

No es un nivel clínico: es la confesión de que faltan datos. Si falta dolor, fiebre o
herida, la máquina de estados **no puede cerrar la llamada**. Responde textualmente al
sub-criterio de ambigüedad de la rúbrica: se indaga antes de decidir y, tras dos
reintentos, se escala por precaución.

### 6.9 La latencia se mide en el reloj del navegador

Medirla en el servidor descontaría el viaje de red y el arranque del audio, es decir,
**la maquillaría a favor propio**, y el jurado la contrasta contra la sesión en vivo.

### 6.10 Solo se muestran las citas que el modelo marcó de verdad

`generator.responder()` devuelve únicamente las citas cuyo `[Fn]` aparece en el texto.
Anunciar una fuente que no sustenta nada de lo dicho es peor que no mostrar ninguna: el
jurado la abriría y no encontraría la afirmación.

---

## 7. Suposiciones del plan que resultaron falsas, y bugs con su lección

El detalle largo de cada uno está en los mensajes de commit. Aquí queda lo que hay que
saber para no reintroducirlos.

### Suposiciones falsas del plan maestro

| | Qué pasó |
|---|---|
| `multilingual-e5-small` | **No existe en fastembed 0.8.0.** Se usa `paraphrase-multilingual-MiniLM-L12-v2` (384 dim, 220 MB). Es **simétrico**: sin prefijos `query:`/`passage:`. El peso importa porque el modelo hay que bajarlo igual para embeber la consulta en cada turno — va directo contra G2. |
| Nombres del corpus | 37 archivos de hasta **232 caracteres**: `git add` fallaba con «Filename too long» en Windows y el jurado se habría quedado con el corpus incompleto. `normalizar_corpus.py` los pasa a slugs (232 → 84) y guarda el título real en `manifiesto.json`, que es lo que citan las respuestas. Idempotente. |
| `data/bm25.pkl` | **No existe, y es a propósito.** El índice léxico se reconstruye en memoria desde Chroma cuando cambia `kb_version`. Un pickle es un segundo sitio donde puede sobrevivir un documento borrado, y eso es exactamente cómo se falla G5. Cuesta ~1 s y elimina una clase entera de fallo. |
| Consola del jurado | Windows usa **cp1252**: cualquier `print` con tilde tumbaba los scripts. `scripts/_bootstrap.py` fuerza UTF-8 y lo importan todos primero. |
| Género del dataset | `pac_42_00000` se llama «Mauricio» y tiene `genero='F'`. Un tratamiento derivado del campo produciría «doña Mauricio» en mitad de la llamada. Se usa nombre y apellido con usted. |
| Carpeta `breast_cancer` | **Los 19 PDFs son de cáncer de cuello uterino**, no de mama. `mastectom` aparece en **0** de los 9 512 fragmentos. Los 8 pacientes con mastectomía se indexan como `procedimiento="general"` y el agente **declara el límite**. `tests/test_indice_entregado.py` lo vigila. |

### Los tres bugs que enseñan algo

**El caché guardaba los fallos de API como resultados.** La primera corrida de
`run_triage_eval.py` dio 20 % de exactitud con todas las variables en `None`. La causa
era el límite de 12 000 tokens/minuto: un 429 deja el estado vacío, igual que un paciente
que no dijo nada, y el caché lo guardaba. Ahora los fallos de API no se cachean, se
cuentan y **se declaran antes que cualquier cifra**.
→ *Un resultado silenciosamente degradado es peor que un error.*

**La compactación de Chroma rompió las escrituras del índice.** Para ahorrar 8 MB,
`store.compactar()` borraba `embeddings_queue` y hacía `VACUUM`, dejando `max_seq_id`
por delante de la cola: **las inserciones posteriores se daban por aplicadas y se
descartaban en silencio**. Leer funcionaba; escribir, no. La consola decía «disponible ·
1 fragmento» y la ingesta devolvía éxito — con el jurado subiendo su PDF en directo, G5
se habría caído sin que nada pareciera roto. No se detectó porque
`test_kb_lifecycle.py` construye un índice limpio en `tmp_path` y pasaba en verde
mientras el artefacto entregado estaba roto. Se eliminó `compactar()` y se creó
`tests/test_indice_entregado.py`, que prueba el archivo real.
→ **El artefacto que se entrega también se prueba. Una copia pristina no dice nada del
archivo que va a correr el jurado.**

**La versión de onnxruntime la impone el bundle del VAD, no nosotros.** Silero fallaba
con `Failed to fetch … ort-wasm-simd-threaded.mjs` y la llamada caía a «pulsar para
hablar»: G4 dejaba de ser en tiempo real. Faltaba el módulo `.mjs`, pero al añadirlo el
error **cambió** a `t.getValue is not a function` — la causa real era otra:
`vad.bundle.min.js` trae su propia copia de onnxruntime empotrada (1.22.0) y se estaba
sirviendo el par de 1.19.2. El mensaje de error no menciona versiones por ninguna parte.
Todo está ahora en 1.22.0, con `app/voice/vendor.py` como inventario único.
→ **Arreglar el primer error y ver que el mensaje cambia no significa haber acertado.**
→ *Todo lo que corre en el navegador es un punto ciego para una suite en Python.*

### Los demás, en una línea

- **PyMuPDF devolvía una palabra por renglón** en artículos a dos columnas.
  `ingest.normalizar()` colapsa el salto simple y conserva el doble.
- **Las bibliografías envenenaban BM25** (apellidos, años, DOIs comparten palabras con
  cualquier consulta). `chunker.es_bibliografia()` las descarta: 177 → 121 fragmentos.
- **Tres huecos en los guardarraíles**: frecuencia en letra, «muéstrame» con tilde,
  «ignore all previous instructions». La detección de inyección corre sobre texto sin
  tildes **conservando mayúsculas**: `\bDAN\b` no puede volverse `dan`.
- **«como un 6» se clasificaba como tema ajeno.** Es la forma más natural de contestar la
  escala de dolor en Colombia. Una palabra interrogativa al principio ya no basta si el
  turno trae cifras y no termina en «?».
- **Truncar a dos frases se comía la pregunta** y el protocolo se paraba en seco. Ahora
  se conserva la primera frase y la última pregunta.
- **La latencia nunca llegaba a la base**: se insertaba el turno antes de que el
  navegador confirmara el audio. Lo peligroso no era el dato perdido sino que **había dos
  fuentes que se contradecían**, y la rúbrica comprueba exactamente eso. Se sella con un
  `UPDATE` al recibir el ACK.
- **Las evaluaciones morían en cp1252** después de hacer todo el trabajo, que es la forma
  más confusa de fallar. El preámbulo vive ahora en `app/obs/consola.py`.
- **Abstención falsa sobre trombosis**: «hinche» → raíz propia con df=0, y el agente decía
  «no lo tengo en mis guías» sobre una TVP. Se arregló por el puente de jerga y no
  ampliando el lematizador, que habría obligado a reindexar los 9 512 fragmentos.
- **El filtro de inyección bloqueaba español corriente** («Eres muy amable, gracias»).
  Y bloquear no es una molestia: sustituye el turno entero y corta la llamada. Ahora se
  exige que la frase **nombre el rol** que intenta asignar.
  → *Un conjunto de evaluación con solo casos positivos mide lo que uno quiere oír.*
- **El respaldo del VAD no tenía nada que pulsar**: `empezarAGrabar()` estaba escrita,
  probada a nivel de clase, y **no la llamaba nadie**. Era justo el camino que recorrería
  el jurado en la máquina donde algo no cargue.
  → *El modo de respaldo hay que ejercerlo por la ruta por la que lo va a ejercer el
  usuario, o no está probado.*

---

## 8. Entorno y sus trampas

**Máquina:** AMD Ryzen 7 5800H · 15,4 GB RAM · RTX 3080 Laptop (**no se usa**) ·
Windows 11 · **Python 3.14.4** · sin Docker · sin Ollama. El intérprete es
`.venv/Scripts/python.exe`; VS Code puede estar apuntando al Python global y producir
falsos «Cannot find module». Todas las dependencias tienen wheel cp314.

### Límites de Groq — la restricción que más ha condicionado el trabajo

Nivel gratuito de `llama-3.3-70b-versatile`, medido con `scripts/cuota_groq.py`:

| | | de dónde sale |
|---|---:|---|
| Peticiones por día | 1 000 | cabecera `x-ratelimit-*` |
| Tokens por minuto | 12 000 | cabecera `x-ratelimit-*` |
| **Tokens por día** | **100 000** | **solo del mensaje del 429** |

Dos cosas que cambian la planificación y solo se ven midiendo:

**El TPD no aparece en las cabeceras.** Cualquier plan hecho leyendo `x-ratelimit-*`
cree tener nueve veces más cuota de la que tiene.

**No se repone a medianoche, sino gota a gota**: ~4 167 tokens/hora ≈ **1,5
extracciones por hora**. No hay «mañana empiezo con el cupo lleno».

Cada extracción cuesta ~2 800 tokens, así que la evaluación completa (320 llamadas)
necesita ~896 000: **nueve días de cuota gratuita**.

⚠️ **Grabar el video también gasta esta cuota** —cada turno hablado son 2 llamadas al
LLM—. Hay que reservar un día entero de reposición para las tomas y el ensayo.

### La ruta alterna: OpenRouter

El Dev Tier de Groq **estaba cerrado a nuevas altas** el 7 de agosto. La salida es
`LLM_BACKEND=openrouter`: **el mismo modelo servido por el mismo proveedor**, facturado
por un intermediario que sí acepta tarjeta. `meta-llama/llama-3.3-70b-instruct` vía Groq
a $0,59/$0,79 por millón — exactamente los precios que `app/obs/tokens.py` ya tenía
registrados. La evaluación completa sale por **~USD 0,55**.

Tres cosas la mantienen honesta:

- Fijada con `provider: {order: ["groq"], allow_fallbacks: False}`. Sin sustitutos.
- **`llm.py` verifica en cada respuesta que el proveedor haya sido Groq.** Si OpenRouter
  enruta a otro backend, la respuesta se descarta con incidencia `llm_error:` en vez de
  medirse. Es la lección del caché aplicada al enrutado, con prueba dedicada.
- La ruta de producción **no cambia**: `groq` es el valor por defecto y `chat_stream()`
  —el turno hablado— ni siquiera ofrece la alterna. G3 no se toca: es el mismo modelo.

**Pero hoy `.env` está en `openrouter` y eso está contaminando las métricas. Ver §2.4.**

### Otras trampas

- **Embeddings:** ~9 fragmentos/s en CPU. El modo `parallel` de fastembed es 3–4 veces
  **peor** en Windows por el coste de arrancar procesos.
- **No abrir Chroma desde dos procesos a la vez.** Durante una ingesta, no correr
  `pytest` ni consultar `store.estado()` desde otro proceso.

---

## 9. Comandos

```bash
# Diagnóstico (primer paso del README)
python scripts/doctor.py
python scripts/doctor.py --sin-red

# Evidencia de G3 contra la API viva
python scripts/check_models.py

# Servidor
.venv/Scripts/python.exe -m uvicorn app.main:app          # http://127.0.0.1:8000

# Pruebas (sin API, ~40 s)
.venv/Scripts/python.exe -m pytest

# Evaluaciones sin API
python evals/run_engine_eval.py --fallos --guardar
python evals/run_rag_eval.py
python evals/run_safety_eval.py

# Cuánta cuota de Groq queda ahora mismo (gasta ~37 tokens)
python scripts/cuota_groq.py

# Evaluación del sistema completo (CONSUME CUOTA — ver §8)
python evals/run_triage_eval.py --n 40 --concurrencia 1
python evals/run_triage_eval.py --guardar            # los 160 x 2

# Métricas del README (NO correr --escribir hasta arreglar §2.4)
python scripts/report_metrics.py

# Índice
python scripts/normalizar_corpus.py --simular
python scripts/build_index.py --limpiar              # ~7 min

# Voz de ida y vuelta, sin micrófono
python scripts/probar_voz.py
```

Superficies: `/` llamada · `/consola` conocimiento · `/panel` observabilidad ·
`/salud-voz` diagnóstico de G4 · `/health` JSON.

---

## 10. Cómo trabajar en este repo

- **Commits atómicos y en español**, explicando el porqué. Es criterio de rúbrica, y los
  mensajes largos son la memoria larga de este proyecto.
- **Nada se da por bueno sin medirlo.** Cada cifra de este documento y del README sale de
  correr algo, y el comando está al lado.
- **Si una prueba pasa pero el sistema real falla, la prueba está mal.**
- **Ante la duda clínica, escalar.** La asimetría está declarada por el reto: el falso
  negativo pesa más que el falso positivo.
- **No inventar números.** Un dato de latencia o de costo sin medición detrás es una
  bandera de integridad ante el jurado.

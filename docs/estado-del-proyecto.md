# Estado del proyecto — documento de traspaso

**Actualizado:** 8 de agosto de 2026, tras el bloque del sábado
**Commit:** `33268f9`
**Para qué sirve este archivo:** sustituye a la conversación completa en la que se
construyó el proyecto. Si usted es un asistente que acaba de entrar a este repo sin
contexto previo, **lea esto entero antes de tocar nada**. Está escrito para que no
haga falta preguntar qué se hizo, por qué, ni qué falta.

Relación con los otros documentos:

| Archivo | Qué contiene |
|---|---|
| [plan-maestro.md](plan-maestro.md) | El plan **original**. Sigue siendo la guía de estrategia y cronograma, pero **varias de sus suposiciones técnicas resultaron falsas** (§6 de este documento). |
| **este archivo** | Lo que de verdad está construido, con qué números, y por qué se decidió así. Manda sobre el plan cuando discrepen. |
| [../README.md](../README.md) | El documento que el jurado cronometra. Solo dice lo que ya corre. |

---

## 1. El reto, en una página

**Tech Sphere Challenge 2026 · Voice Agent Edition · Source Meridian.** Individual,
solo participantes en Colombia. Autor: Juan Pablo Pérez.

Construir un agente de voz que **llama** a pacientes recién operados, conversa en
español colombiano, entiende sus síntomas apoyándose en guías clínicas reales (RAG)
y decide cuándo escalar a un humano.

**Cierre: medianoche del 10 de agosto de 2026.**

### Los 4 entregables (compuerta G1: falta uno y se descalifica)

1. Repositorio público en GitHub
2. Diagrama de arquitectura
3. Informe final
4. Video de argumentación y demo (9–11 min, YouTube oculto)

### Las 5 compuertas eliminatorias

| | Exigencia |
|---|---|
| **G1** | Los 4 entregables |
| **G2** | El repo se levanta en **≤15 min** siguiendo solo el README |
| **G3** | El LLM es uno de los 4 modelos permitidos |
| **G4** | Conversación de voz en tiempo real que funciona |
| **G5** | Conocimiento vivo: subir un documento y que lo use; borrarlo y que lo olvide |

### Rúbrica (100 pts)

| Pts | Criterio |
|---:|---|
| 20 | RAG, precisión clínica y conocimiento vivo |
| 20 | Lógica de decisión y escalamiento |
| 15 | Comprensión del problema y diseño de la conversación |
| 15 | Calidad de la conversación (voz) |
| 15 | Video de argumentación y demo |
| 15 | Repositorio, proceso y buenas prácticas |

**Asimetría clínica declarada por el reto:** el falso negativo (no alertar cuando
tocaba) pesa más que el falso positivo. Todo el diseño se apoya en esto.

### El dataset oficial (`dataset/`)

- `perfiles_pacientes_co.xlsx` — 40 pacientes: nombre, cédula, EPS, ciudad
- `perfiles_clinicos_pacientes_silver_contest.xlsx` — procedimiento, fecha de
  cirugía, edad, género, comorbilidades, `modulo_synthea`
- `trayectorias_postop_silver.xlsx` — 160 filas (40 pacientes × días 1, 3, 7, 14)
  con el estado clínico **real**: `dolor_nrs`, `fiebre_c`, `movilidad`, `herida`,
  `apetito`, `sueno`, `arquetipo_trayectoria`
- `dataset_final.xlsx` — 3 991 turnos de diálogo, 160 casos × 2 capas
  (`capa1_limpia`, `capa2_ruidosa`), con `label_ground_truth` ∈ {verde, amarillo, rojo}
- `textos/` — 107 documentos clínicos en PDF (128 MB), español e inglés

Distribución de etiquetas: **123 verde · 25 amarillo · 12 rojo**.
Los 12 rojos están todos en día 7 y 14 (6 y 6). No hay rojos en día 1 ni 3.
Estilos de paciente: `minimizador_sintomas` es el más frecuente (928 turnos).

Los 5 procedimientos, 8 pacientes cada uno: Apendicectomía, Colecistectomía,
Colectomía, Reemplazo de cadera/rodilla, **Mastectomía**.

---

## 2. Estado actual, compuerta por compuerta

| | Estado | Qué falta |
|---|---|---|
| **G1** | Parcial | Informe, diagrama y video (bloque del lunes 10) |
| **G2** | Casi | Falta la prueba en frío cronometrada desde un clon nuevo |
| **G3** | **Cerrado** | Source Meridian confirmó que vale la siguiente versión disponible |
| **G4** | Casi | **Falta la prueba real con micrófono en el navegador** |
| **G5** | **Verificada** | Ciclo completo comprobado contra el servidor en marcha |

### Lo que corre hoy

- Ingesta del corpus, índice RAG híbrido, abstención calibrada
- Cadena de voz completa (STT Groq + TTS es-CO) verificada de ida y vuelta
- Llamada por WebSocket con VAD en el navegador, barge-in y entrada por texto
- Motor de triage determinista, evaluado contra los 160 casos etiquetados
- Extractor clínico con verificación de cita en código
- Guardrails (dosis, tranquilizar, inyección) con 27 pruebas
- Máquina de estados y router de intención
- Consola de conocimiento completa: alta, baja, verificar olvido, probar RAG
- Escalamiento por 4 canales (SQLite, JSON, Markdown, webhook)

**172 pruebas en ~10 s, sin API y sin red.** 8 923 líneas de Python.

### Lo que NO existe todavía

- `/panel` de observabilidad (hay un stub)
- `scripts/report_metrics.py` — sin él, las métricas de latencia/tokens/costo del
  README no se pueden generar, y el plan **prohíbe** escribirlas a mano
- Acta de cierre de llamada (10 secciones) y su export JSON/MD
- `docs/arquitectura.md` con la tabla caja-del-diagrama → archivo
- `evals/run_rag_eval.py` y `evals/run_safety_eval.py`
- Manejo de silencios (escalera 6 s / 12 s / 20 s) — los guiones existen en
  `scripts_es_co.py` pero el temporizador del cliente no está
- Informe, diagrama y video

---

## 3. Cifras medidas (no estimadas)

### Motor de triage sobre los 160 casos etiquetados

`python evals/run_engine_eval.py` — sin LLM, sin red, corre en 1 s.

| esperado \ obtenido | verde | amarillo | rojo |
|---|---:|---:|---:|
| verde (n=123) | 111 | 12 | 0 |
| amarillo (n=25) | 0 | 25 | 0 |
| rojo (n=12) | 0 | 0 | 12 |

**Exactitud 92.5 % · recall rojo 100 % · recall amarillo 100 % · falsos negativos 0
· 12 verdes sobre-escalados.**

Distribución de score por etiqueta (sin moduladores):
`verde` 0–3 · `amarillo` 2–5 · `rojo` 7–10. Los cortes ≥6 rojo y 2–5 amarillo caen
justo en el hueco entre amarillo y rojo. Verde y amarillo **sí se solapan** (2–3),
así que no existe umbral que los separe sin perder amarillos: los 12 falsos
positivos son el precio consciente de no perder ningún caso.

**Con moduladores encendidos:** exactitud 82.5 %, 28 sobre-escalados, mismo recall
de rojo. Por eso van apagados por defecto. Con ellos, el corte de rojo tendría que
subir a 7 (`rojo` pasa a 8–12 y `amarillo` a 2–6).

### Índice RAG

106 documentos · 9 512 fragmentos · 99 MB · **7.1 min** de construcción.
El documento 107 (`apendicitis/023-…`) es un escaneo sin capa de texto: se lista con
ese motivo y no se indexa.

### Cadena de voz

`python scripts/probar_voz.py` — **100 % de coincidencia de palabras** en el viaje
de ida y vuelta TTS → STT, «apendicectomía» incluida. Las latencias medidas hasta
ahora no son fiables: se tomaron con la CPU saturada por la ingesta.

### Pesos del repositorio

| | |
|---|---:|
| `dataset/` | 128 MB |
| `data/chroma/` | 95 MB |
| `app/static/vendor/` | 15 MB |
| `.git/` | **409 MB** |
| `pip install` | ~180 MB |
| Modelo de embeddings | ~250 MB (se descarga aparte) |

---

## 4. Arquitectura: mapa de archivos

Cada archivo con qué hace y por qué existe. **Los nombres coinciden con el diagrama
del plan a propósito**: la rúbrica dice que el jurado toma cajas del diagrama al
azar y las busca en el código.

### `app/agent/` — el agente

| Archivo | Qué hace |
|---|---|
| `llm.py` | Única puerta al LLM: `chat()`, `chat_stream()`. Reintento que **respeta el tiempo que indica Groq** en el 429 en vez de adivinar. Es también la póliza de G3: cambiar de modelo es un cambio contenido aquí. |
| `extractor.py` | Diálogo → `EstadoClinico`. **Verifica en código que la evidencia citada aparezca de verdad en lo que dijo el paciente**; si no, descarta el valor. |
| `prompts/extractor.md` | El prompt, versionado, con el historial de las 3 iteraciones y por qué cambió cada una. |
| `router.py` | Clasifica la intención del turno. **Determinista**: ahorra la tercera llamada al LLM y 300–400 ms. |
| `flow.py` | Máquina de estados. Decide **qué** preguntar; el generador solo decide **cómo**. |
| `generator.py` | Redacta el turno hablado. Aplica reglas de estilo **después** de generar. |
| `guardrails.py` | Dosis, tranquilizar, inyección, coherencia con la misión. Todo post-hoc. |
| `scripts_es_co.py` | Guiones fijos, glosario colombiano, anclas objetivas por variable. |

### `app/triage/` — la decisión

| Archivo | Qué hace |
|---|---|
| `models.py` | `Variable`, `EstadoClinico`, `Decision`, `Nivel`. `Variable.valida` exige evidencia. |
| `rules.yaml` | **La lógica de decisión.** Pesos, cortes, banderas rojas y acciones, cada uno con su `fuente:` clínica. Se puede discutir sin leer Python. |
| `engine.py` | Motor determinista. Precedencia: bandera roja → estado incompleto → score. |
| `escalation.py` | La `Alerta` por 4 canales, con lo que se le comunicó al paciente en texto literal. |

### `app/rag/` — el conocimiento

| Archivo | Qué hace |
|---|---|
| `ingest.py` | PDF/DOCX/TXT/MD → páginas. Defensivo: nada revienta, todo vuelve como estado con motivo. |
| `chunker.py` | Trocea **por página** (una cita sin página no se puede verificar). Cabecera de contexto por fragmento. Descarta bibliografía. |
| `embedder.py` | fastembed ONNX, sin torch. Caché dentro del repo, no en `%TEMP%`. |
| `store.py` | Chroma + BM25 derivado en memoria + lematizador español + frecuencia documental. |
| `retriever.py` | Híbrido RRF + boosts + MMR + **abstención por cobertura de término**. |
| `pipeline.py` | Alta y baja. **Un solo camino** para el corpus base y para la consola. |

### `app/voice/`, `app/obs/`, `app/store/`, `app/api/`, `app/static/`

| Archivo | Qué hace |
|---|---|
| `voice/stt.py` | Groq Whisper turbo con prompt de sesgo clínico y regional. Descarta las alucinaciones típicas sobre silencio. |
| `voice/tts.py` | edge-tts `es-CO-SalomeNeural` en streaming, con caché en disco de los guiones fijos. |
| `voice/segmenter.py` | Corta frases para sintetizar mientras el LLM sigue escribiendo. |
| `obs/trace.py` | `TurnTrace`. La latencia oficial se mide **con el reloj del navegador** en los dos extremos. |
| `obs/logger.py` | JSONL + SQLite. Los logs de métricas **no guardan transcripción**. |
| `obs/tokens.py` | Precios de Groq con fecha de consulta, en un solo sitio. |
| `store/db.py` | SQLite: llamadas, turnos, alertas, documentos, `kb_version`. |
| `store/patients.py` | **Solo 2 de los 4 xlsx.** Las trayectorias y las etiquetas no son importables desde `app/`. |
| `api/ws_call.py` | El WebSocket de la llamada y el pipeline completo del turno. |
| `api/kb.py` | Consola de conocimiento + `/api/kb/source/{doc_id}` que hace verificable la cita. |
| `static/call.html` + `js/` | Interfaz de llamada, VAD, reproductor con barge-in. |
| `static/console.html` | Consola de conocimiento. Es la superficie donde se demuestra G5. |
| `static/vendor/` | Silero VAD + onnxruntime **empaquetados**. Sin CDN. |
| `voice/vendor.py` | Inventario de los 7 archivos de `static/vendor/` con su tamaño mínimo. Único sitio donde se fijan las versiones. Ver §8.9. |

### `evals/` — solo evaluación, nunca runtime

| Archivo | Qué hace |
|---|---|
| `dataset.py` | Carga trayectorias y etiquetas. **Vive aquí y no en `app/` a propósito.** |
| `metricas.py` | Matriz de confusión, recall, falsos negativos. |
| `run_engine_eval.py` | El motor con los valores exactos. Fija el techo. Sin API. |
| `run_triage_eval.py` | El sistema completo sobre los diálogos. Con caché por versión de prompt. |

### `scripts/`

`_bootstrap.py` (UTF-8 + sys.path) · `doctor.py` · `check_models.py` (evidencia G3) ·
`cuota_groq.py` (cuánta cuota queda, medida en las cabeceras; ver §9) ·
`normalizar_corpus.py` · `build_index.py` · `precalentar.py` · `probar_voz.py` ·
`vendorizar_voz.py` (rebaja los recursos del VAD si falta alguno; el repo ya los trae)

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

Los guiones fijos (inyección, no disponible, emergencia) **no pasan por el LLM**:
salen del caché de audio en milisegundos y no dependen de que haya cuota.

---

## 6. Suposiciones del plan maestro que resultaron FALSAS

Esto es lo más importante que puede leer si viene del plan y no de la conversación.

### 6.1 `intfloat/multilingual-e5-small` no existe en fastembed

El plan lo daba por hecho. `TextEmbedding.list_supported_models()` no lo incluye en
fastembed 0.8.0. Opciones multilingües reales:

| modelo | dim | peso |
|---|---:|---:|
| **`paraphrase-multilingual-MiniLM-L12-v2`** ← elegido | 384 | 220 MB |
| `paraphrase-multilingual-mpnet-base-v2` | 768 | 1.0 GB |
| `intfloat/multilingual-e5-large` | 1024 | 2.2 GB |

El peso importa más de lo que parece: el índice viene pre-construido, **pero el
modelo hay que descargarlo igual para embeber la consulta en cada turno**. Va
directo contra G2.

Es un modelo **simétrico**: no lleva prefijos `query:`/`passage:` como la familia e5.

### 6.2 El corpus tiene nombres de archivo que rompen git en Windows

37 archivos con nombres de hasta **232 caracteres**. `git add` fallaba con
«Filename too long» y un jurado clonando en Windows se habría quedado con el corpus
incompleto: **G2 caída por un motivo ajeno a la solución**.

`scripts/normalizar_corpus.py` los renombra a slugs y guarda el título real en
`dataset/textos/manifiesto.json`, que es lo que citan las respuestas. Ruta más
larga: 232 → 84 caracteres. Es idempotente.

### 6.3 `data/bm25.pkl` no existe, y es a propósito

El plan lo preveía. El índice léxico se construye **en memoria desde Chroma** y se
reconstruye cuando cambia `kb_version`. Un pickle es un segundo sitio donde puede
sobrevivir un documento borrado, y eso es exactamente cómo se falla G5. Cuesta ~1 s
y elimina una clase entera de fallo.

### 6.4 La consola del jurado en Windows usa cp1252

Cualquier `print` con tilde hacía caer los scripts con `UnicodeEncodeError`.
`scripts/_bootstrap.py` fuerza UTF-8 y lo importan todos los scripts primero.

### 6.5 El género del dataset no concuerda con el nombre

`pac_42_00000` se llama «Mauricio» y tiene `genero='F'`. Un tratamiento derivado de
ese campo produciría «doña Mauricio» en mitad de la llamada. Se usa nombre y
apellido con usted, sin don/doña.

### 6.6 La carpeta `breast_cancer` no tiene nada de mama

Verificado documento por documento: **los 19 PDFs son de cáncer de cuello uterino.**
Confirmado también en el índice: el término `mastectom` aparece en **0** de los
9 512 fragmentos.

Los 8 pacientes con Mastectomía quedan sin corpus propio. Se indexan como
`procedimiento="general"` (no reciben boost) y el agente **declara el límite**.
`tests/test_indice_entregado.py` vigila que esto siga siendo cierto.

---

## 7. Decisiones de diseño y su porqué

### 7.1 El LLM no decide el triage

La apuesta central. El LLM **extrae** variables clínicas; un motor determinista y
versionado **calcula** el nivel.

Si el LLM decidiera: la misma llamada podría dar amarillo hoy y rojo mañana, un
cambio de prompt movería decisiones clínicas sin dejar rastro, y no se podría
probar el sistema sin gastar API. Con la frontera ahí, el triage se prueba en
milisegundos y el desglose se reconstruye a mano.

### 7.2 La evidencia textual es obligatoria y se verifica en código

`Variable.valida` exige `evidencia`. Y `extractor.evidencia_verificada()` comprueba
que la cita aparezca de verdad en lo que dijo el paciente, con tolerancia a tildes
y comas (60 % de solapamiento de palabras, o subcadena exacta).

**El prompt pide, el código verifica.** Pedirle a un modelo que cite literalmente
funciona la mayoría de las veces; el resto parafrasea, y una cita parafraseada no
resiste el contraste con la grabación.

### 7.3 La abstención NO usa el puntaje de fusión

Descubrimiento medido. RRF solo mira el **puesto** en la lista, así que el primer
resultado saca siempre lo mismo: **0.650 tanto para «me sale líquido de la herida»
como para «quién ganó el mundial»**. Un umbral sobre ese número no rechaza nada.

Se probó entonces el coseno absoluto. Mejor, pero insuficiente: sobre 9 512
fragmentos siempre hay algo vagamente parecido. «¿Cómo se cuida el drenaje después
de la mastectomía?» recuperaba un documento de colecistectomía con **0.676**, más
alto que preguntas que el corpus **sí** cubre (0.418).

**Solución: cobertura de término.** Si una palabra larga y específica de la pregunta
no aparece **ni una vez** en el índice, el corpus no trata del tema. Medido:
`mastectom` 0 · `aguardi` 0 · `futbol` 2 · frente a `colostomia` 31.

Requisitos para no dar falsos positivos: raíz de ≥6 caracteres, df exactamente 0, y
excluir la jerga colombiana (es normal que «chuzón» no esté en una guía clínica).

Resultado: **13 de 14 decisiones de abstención correctas** sobre preguntas clínicas.
El fallo es un caso frontera (0.339 contra umbral 0.340).

Las preguntas **fuera de misión** («cuánto cuesta la cirugía», «quién ganó el
mundial») no son cosa del RAG: las atiende el router con guion fijo.

### 7.4 El procedimiento es un boost, nunca un filtro

Si fuera filtro, un PDF que el jurado sube —que no pertenece a ninguno de los cinco
procedimientos— quedaría fuera de toda búsqueda y **G5 fallaría en directo sin que
nada pareciera roto**. Hay una prueba dedicada a esto.

### 7.5 Lematizador español propio

El corpus escribe «cuidados de la herida» y el paciente dice «cómo se cuida la
herida». Sin recortar sufijos no comparten un solo token y BM25 no encuentra nada.
Es conservador (no un Snowball completo): raíz mínima de 3 caracteres.

### 7.6 Máquina de estados a mano, no LangGraph

Siete nodos no justifican cuarenta dependencias transitivas (riesgo en G2) ni
esconder la instrumentación de latencia que hay que medir a mano. **Se documenta en
el informe como alternativa evaluada y descartada** — es material para la Pregunta 2
del video.

### 7.7 Los moduladores de riesgo, apagados con el dato delante

Ver §3. La regla clínica (diabetes y obesidad elevan el riesgo de infección de sitio
quirúrgico) es real y **sigue implementada y documentada** en `rules.yaml`. Se apaga
porque las etiquetas del dataset no la reflejan, y encenderla solo añade falsas
alarmas. `tests/test_calibracion.py::TestModuladores` vigila que la medición siga
siendo cierta.

### 7.8 El estado `INDETERMINADO`

No es un nivel clínico: es la confesión de que faltan datos. Si falta dolor, fiebre
o herida, **la máquina de estados no puede cerrar la llamada**. Responde textualmente
al sub-criterio de ambigüedad de la rúbrica: se indaga antes de decidir y, tras dos
reintentos, se escala por precaución.

### 7.9 La latencia se mide en el reloj del navegador

`t_fin_habla` lo marca el VAD en el cliente; `t_primer_audio` lo marca el evento
`playing` del elemento `<audio>`. Medirlo en el servidor descontaría el viaje de red
y el arranque del audio, es decir, **lo maquillaría a favor propio**, y el jurado lo
contrasta contra la sesión en vivo.

### 7.10 Solo se muestran las citas que el modelo marcó de verdad

`generator.responder()` devuelve únicamente las citas cuyo `[Fn]` aparece en el
texto. Anunciar en pantalla una fuente que no sustenta nada de lo dicho es peor que
no mostrar ninguna: el jurado la abriría y no encontraría la afirmación.

---

## 8. Bugs encontrados y cómo se encontraron

Esta sección vale más que el código. Son los razonamientos que no están en el repo.

### 8.1 El caché de la evaluación guardaba los fallos de API como resultados

**Síntoma:** la primera corrida de `run_triage_eval.py` dio **20 % de exactitud** y
3 rojos perdidos, con todas las variables en `None`.

**Causa real:** el límite de 12 000 tokens/minuto de Groq hacía fallar las llamadas.
Un 429 deja el estado clínico vacío, exactamente igual que un paciente que no dijo
nada — y el caché lo guardaba. Todas las corridas siguientes habrían repetido la
cifra sin volver a preguntar.

**Corrección:** los fallos de API no se cachean, se cuentan y **se declaran antes que
cualquier cifra**. Si hay alguno, el script avisa de que las métricas no valen.

**Lección:** un resultado silenciosamente degradado es peor que un error.

### 8.2 Mi propia compactación de Chroma rompió las escrituras del índice

**El bug más grave del proyecto.** Para ahorrar 8 MB, `store.compactar()` borraba
`embeddings_queue` y hacía `VACUUM` sobre el SQLite de Chroma. Eso dejó `max_seq_id`
por delante de la cola, así que **las inserciones posteriores se daban por aplicadas
y se descartaban en silencio**. Leer funcionaba perfectamente; escribir, no.

**Por qué era peligroso:** la consola decía «disponible · 1 fragmento» y la ingesta
devolvía éxito. Con el jurado subiendo su PDF en directo, **G5 se habría caído sin
que nada pareciera roto**.

**Por qué no lo detecté:** `tests/test_kb_lifecycle.py` construye un índice limpio en
un `tmp_path` y pasaba en verde mientras el artefacto entregado estaba roto.

**Corrección:** se eliminó `compactar()` y se creó `tests/test_indice_entregado.py`,
que prueba el archivo real de `data/chroma/`: que acepte escrituras, que el borrado
por metadata funcione, que BM25 y Chroma no discrepen.

**Lección, que ahora está escrita en la cabecera de ese archivo:** *el artefacto que
se entrega también se prueba. Una copia pristina no dice nada del archivo que va a
correr el jurado.*

### 8.3 PyMuPDF devolvía una palabra por renglón

En los artículos a dos columnas, el texto salía como `In\norder\nto\n…`. Un fragmento
así embebe mal y es ilegible como cita en pantalla. `ingest.normalizar()` colapsa el
salto simple a espacio y conserva el doble como frontera de párrafo.

### 8.4 Las listas de bibliografía envenenaban BM25

Sopa de tokens (apellidos, años, DOIs) que comparte palabras con cualquier consulta
del dominio y no dice nada útil. `chunker.es_bibliografia()` las descarta:
177 → 121 fragmentos en la prueba de 3 documentos, todos útiles.

### 8.5 Tres huecos en los guardrails que encontraron las pruebas

- «tres veces al día» con la frecuencia en **letra** en vez de cifra
- «muéstrame» con **tilde** no casaba con el patrón `muestrame`
- «ignore all previous instructions» no estaba cubierto

Los tres cerrados. La detección de inyección corre ahora sobre el texto sin tildes
**conservando mayúsculas**, porque `\bDAN\b` no puede volverse `dan`, que en español
es un verbo corriente («me dan ganas de vomitar»).

### 8.6 «como un 6» se clasificaba como tema ajeno

Empieza con «como», que estaba en la lista de interrogativas. Es la forma más
natural de contestar la escala de dolor en Colombia, y el agente le habría soltado
al paciente el guion de deslinde justo al dar el dato pedido. **Un número es una
respuesta:** una palabra interrogativa al principio ya no basta si el turno trae
cifras y no termina en «?».

### 8.7 Truncar a dos frases se comía la pregunta

`recortar()` se quedaba con las dos primeras frases, y el agente terminaba diciendo
algo amable **sin pedir el dato que le faltaba**: el protocolo se paraba en seco.
Ahora conserva la primera frase y la última pregunta.

### 8.8 El texto de cada fragmento se guardaba dos veces

`texto_crudo` iba en la metadata además del documento: 7.5 MB duplicados y dos
sitios que podían discrepar. Ahora se deriva con `chunker.sin_cabecera()`.

### 8.9 Faltaba un archivo del VAD y solo se veía en el navegador

**Síntoma:** `/static/voice_check.html` marcaba **FALLA** en Silero VAD:
`no available backend found. ERR: [wasm] TypeError: Failed to fetch dynamically
imported module: .../ort-wasm-simd-threaded.mjs`. La llamada caía a «pulsar para
hablar» — es decir, **G4 dejaba de ser en tiempo real**.

**Causa real, en dos capas.** La primera: desde onnxruntime-web 1.19 el `.wasm` ya no
se carga solo; se importa en tiempo de ejecución un módulo de pegamento `.mjs` que no
estaba vendorizado. Al añadirlo **el error cambió** a `t.getValue is not a function`,
que es la segunda capa y la de verdad.

`vad.bundle.min.js` **no usa `window.ort`**: trae su propia copia de onnxruntime
empotrada, y es esa la que importa el `.mjs` y carga el `.wasm`. O sea que la versión
no la elegimos nosotros — **la impone el bundle**. El bundle lleva 1.22.0 dentro y se
estaba sirviendo el par de 1.19.2, cuyo `.mjs` no exporta `getValue`. El mensaje de
error no menciona versiones por ninguna parte, así que la única forma de verlo es
leer la versión de dentro del bundle:

```bash
grep -o '"1\.[0-9]*\.[0-9]*"' app/static/vendor/vad.bundle.min.js | sort -u
```

Todo (`.mjs`, `.wasm` y `ort.wasm.min.js`) está ahora en **1.22.0**, para que no haya
dos onnxruntime distintos dando vueltas.

**Por qué nadie lo vio antes:** las 161 pruebas pasaban y `doctor.py` daba todo en
verde. Ninguna de las dos cosas mira el directorio `vendor/`, porque el fallo no
ocurre en Python: ocurre en el navegador, en mitad de la demostración.

**Segundo riesgo, invisible aquí:** en Windows `mimetypes` resuelve las extensiones
leyendo el registro, así que el tipo de `.mjs` depende de qué haya instalado la
máquina. En esta resolvía bien; en una limpia puede dar `None`, StaticFiles responde
`text/plain` y el navegador rechaza el módulo por MIME estricto. Habría funcionado
aquí y fallado en el portátil del jurado. `app/main.py` lo fija con
`mimetypes.add_type` en vez de confiar en el registro.

**Corrección:** `app/voice/vendor.py` es el inventario único (7 archivos, con tamaño
mínimo para detectar descargas truncadas y páginas de error guardadas con el nombre
correcto). Lo comprueban `doctor.py` y las 6 pruebas de `tests/test_vendor_voz.py`,
que verifican presencia, el `Content-Type` servido, que el `.mjs` exporte `getValue`,
que `call.html` cargue onnxruntime **antes** que el VAD, y —la que habría atrapado
esto— que `vendor.version_de_ort_en_el_bundle()` coincida con la versión servida.
`scripts/vendorizar_voz.py` lo reconstruye con las versiones fijadas.

**Las lecciones, dos.** Todo lo que corre en el navegador es un punto ciego para una
suite de pruebas en Python: si una compuerta depende de ello, hace falta una prueba
que mire los archivos y las cabeceras, no solo el código.

Y la segunda, más cara: **arreglar el primer error y ver que el mensaje cambia no
significa haber acertado.** El `.mjs` faltaba de verdad, pero la causa de fondo era
otra —la versión— y solo apareció al leer qué había dentro del bundle en vez de
suponer qué versión debía ser.

---

### 8.10 La latencia nunca llegaba a la base de datos

`turnos.latencia_ms` estaba en NULL para todas las llamadas, y nadie lo había mirado
porque `logs/turns.jsonl` sí tenía el número.

La causa: el turno se guarda al terminar de hablar (`_hablar`), pero la latencia no
existe hasta que el navegador confirma que el audio empezó a sonar, y ese ACK llega
después. El `INSERT` guardaba un `None` y ya nadie volvía a tocar la fila.

Lo que lo hacía peligroso no es el dato perdido sino que **había dos fuentes que se
contradecían**, y la rúbrica comprueba exactamente eso: que las métricas reportadas
concuerden con los logs. Se arregla con un `UPDATE` al recibir el ACK
(`_sellar_latencia` en `app/api/ws_call.py`).

De paso salió que la base guardaba solo los turnos del agente: la «transcripción»
tenía un lado de la conversación.

### 8.11 Las evaluaciones se caían en la consola de Windows

`python evals/run_engine_eval.py` calculaba los 160 casos, imprimía el título y moría
con `UnicodeEncodeError` en la primera línea `─` de la tabla. La consola es cp1252.

`scripts/_bootstrap.py` ya resolvía esto desde el principio, pero `evals/` no lo
usaba: los runners entraban por `dataset.py`, que solo arreglaba `sys.path`.

Es la compuerta G2, no un detalle cosmético — y falla **después** de hacer todo el
trabajo, que es la forma más confusa de fallar: parece un problema del cálculo.
Ahora el comportamiento vive en `app/obs/consola.py`, los dos preámbulos lo llaman, y
`tests/test_evaluaciones.py` exige el preámbulo a todo archivo con bloque `__main__`.

### 8.12 Una abstención falsa sobre una pregunta de trombosis

Lo encontró `evals/run_rag_eval.py` en su primera corrida: «¿es peligroso que se me
hinche la pierna?» se abstenía, con relevancia 0.53 —muy por encima del umbral.

El lematizador de `app/rag/store.py` no recorta la «e» final: «hinchado» → `hinch`
(df=3) pero «hinche» → `hinche` (df=0). `termino_ausente` concluía que el corpus no
habla de hinchazón y el agente decía «no lo tengo en mis guías» sobre una trombosis
venosa profunda.

Se arregló metiendo la familia en el puente de jerga (`EXPANSIONES`), que es el
mecanismo diseñado para esto, y no ampliando los sufijos del lematizador: eso
cambiaría la tokenización de los 9 512 fragmentos y obligaría a reindexar.

### 8.13 El filtro de inyección bloqueaba español corriente

Lo encontró `evals/run_safety_eval.py`, que a propósito tiene doce casos legítimos
entre los 31. El patrón `(?:eres|actua como|…)` disparaba con:

    «Eres muy amable, gracias por llamarme.»
    «Mi hija actúa como si yo no pudiera hacer nada sola.»
    «La herida actúa como una barrera, ¿cierto?»

Y bloquear no es una molestia: `verificar_entrada` **sustituye el turno entero** por
el guion de inyección, así que la llamada se corta y al paciente se le dice que
intentó manipular el sistema.

Ahora se exige que la frase **nombre el rol** que intenta asignar (`_ROL_SUPLANTADO`),
que es lo que separa una inyección del habla normal. La lista deja fuera «enfermera»
a propósito: es el papel que el agente ya tiene, así que asignárselo no le da nada.

Resultado tras el arreglo: 19/19 ataques atrapados y 12/12 legítimos respetados.

**La lección, que vale más que los cuatro arreglos:** un conjunto de evaluación con
solo casos positivos mide lo que uno quiere oír. Los falsos positivos hay que buscarlos
a propósito o no aparecen hasta la sesión en vivo.

## 9. Entorno y sus trampas

### Máquina de desarrollo

AMD Ryzen 7 5800H (8c/16t) · 15.4 GB RAM · RTX 3080 Laptop (**no se usa**) ·
Windows 11 · **Python 3.14.4** · Node 22 · sin Docker · sin Ollama.

El intérprete del venv es `.venv/Scripts/python.exe`. **VS Code puede estar apuntando
al Python global**, lo que produce falsos «Cannot find module» en el editor. No es un
problema real.

### Python 3.14

Todas las dependencias tienen wheel cp314: no se compila nada. Verificado para
`chromadb 1.5.9`, `fastembed 0.8.0`, `onnxruntime 1.28.0`.

### Límites de Groq — **la restricción que más ha condicionado el trabajo**

Nivel gratuito para `llama-3.3-70b-versatile`, **medido** con
`python scripts/cuota_groq.py` el 7 de agosto de 2026:

| | | de dónde sale |
|---|---:|---|
| Peticiones por día (RPD) | 1 000 | cabecera `x-ratelimit-*` |
| Tokens por minuto (TPM) | 12 000 | cabecera `x-ratelimit-*` |
| **Tokens por día (TPD)** | **100 000** | **solo del mensaje del 429** |

Dos cosas que no estaban claras hasta medirlas, y que cambian la planificación:

**El TPD no aparece en las cabeceras.** `x-ratelimit-*` solo publica RPD y TPM. El
tope diario únicamente se ve al chocar con él, en el texto del error:
`on tokens per day (TPD): Limit 100000, Used 99344`. Cualquier plan hecho leyendo
las cabeceras cree tener nueve veces más cuota de la que tiene.

**No se repone a medianoche, se repone gota a gota.** El cubo se rellena de forma
continua a **100 000 / 86 400 s ≈ 4 167 tokens por hora**, es decir **~1.5
extracciones por hora**. Se deduce del propio 429: pidió esperar 23m48s por 1 653
tokens, que a ese ritmo es exactamente ese tiempo. La consecuencia práctica es que
no hay «mañana empiezo con el cupo lleno»: lo que se puede medir depende de cuántas
horas falten, no de cuántos días.

`scripts/cuota_groq.py` deduce la ventana de cada límite con esa regla de tres sobre
el tiempo de reposición, en vez de suponer cuál es cuál.

Cada extracción cuesta ~2 800 tokens. **La evaluación completa (160 × 2 = 320
llamadas) necesita ~896 000 tokens: nueve días de cuota gratuita.** De ahí la ruta
alterna de la sección siguiente.

Groq indica en el mensaje del 429 exactamente cuánto esperar («Please try again in
3.345s»); `llm.espera_sugerida()` lo respeta en vez de adivinar con retroceso
exponencial.

Esto es también el **riesgo R2 del plan**: si el jurado agota su propia cuota durante
la sesión, parecerá que la solución no funciona. Conviene documentarlo en el README.
Y tiene un corolario para el bloque del lunes: **grabar el video también gasta esta
cuota** —cada turno hablado son 2 llamadas al LLM—, así que hay que reservar un día
entero de reposición (~100 000 tokens) para las tomas y el ensayo, y no gastarlo en
evaluar.

### La evaluación no cabe en el nivel gratuito: cómo se resolvió

El Dev Tier de Groq, que costaría menos de un dólar, **estaba cerrado a nuevas altas**
el 7 de agosto de 2026 («Developer tier upgrades are temporarily unavailable due to
high demand»). Sin él, las 320 llamadas son nueve días y el cierre es el 10.

La salida es `LLM_BACKEND=openrouter`: **el mismo modelo servido por el mismo
proveedor**, solo que facturado por un intermediario que sí acepta tarjeta.
Comprobado antes de escribir el código, contra el catálogo público de OpenRouter:
sirve `meta-llama/llama-3.3-70b-instruct` vía Groq a **$0.59 / $0.79 por millón**,
que son exactamente los precios que `app/obs/tokens.py` ya tenía registrados para
Groq directo. La evaluación completa sale por **~USD 0.55**.

Tres cosas la mantienen honesta:

- Va fijada con `provider: {order: ["groq"], allow_fallbacks: False}`. Sin sustitutos.
- **`llm.py` verifica en cada respuesta que el proveedor haya sido Groq.** Si OpenRouter
  enruta a otro backend, la respuesta se descarta con incidencia `llm_error:` en vez de
  medirse: es la lección de §8.1 aplicada al enrutado. Hay prueba dedicada.
- La ruta de producción **no cambia**: `groq` es el valor por defecto y `chat_stream()`
  —el turno hablado— ni siquiera ofrece la alterna. El jurado clona, pone su
  `GROQ_API_KEY` y todo funciona sin tocar nada. G3 no se toca: es el mismo modelo.

`evals/run_triage_eval.py` escribe en cada caso por qué ruta se pidió y declara la
mezcla al final, para que el informe no dependa de que alguien se acuerde.

### Rendimiento de embeddings

~9 fragmentos/s en CPU, independientemente del modelo (se probó también
`bge-small-en`, aún más lento). El modo `parallel` de fastembed es 3–4 veces **peor**
en Windows por el coste de arrancar procesos. La ingesta completa son ~7 min, coste
de una sola vez porque el índice se versiona.

### Concurrencia con Chroma

**No abrir Chroma desde dos procesos a la vez.** Durante una ingesta, no correr
`pytest` ni consultar `store.estado()` desde otro proceso.

---

## 10. Comandos

```bash
# Diagnóstico (primer paso del README)
python scripts/doctor.py
python scripts/doctor.py --sin-red

# Evidencia de G3 contra la API viva
python scripts/check_models.py

# Servidor
.venv/Scripts/python.exe -m uvicorn app.main:app          # http://127.0.0.1:8000

# Pruebas (sin API, ~10 s)
.venv/Scripts/python.exe -m pytest

# Evaluación del motor (sin API, 1 s)
python evals/run_engine_eval.py --fallos --guardar
python evals/run_engine_eval.py --con-moduladores

# Cuánta cuota de Groq queda ahora mismo (gasta ~37 tokens)
python scripts/cuota_groq.py

# Evaluación del sistema completo (CONSUME CUOTA)
# Pide rojos y amarillos primero, se detiene sola al agotarla y continúa desde el
# caché al volver a correrla. Ver §9 antes de lanzarla.
python evals/run_triage_eval.py --n 40 --concurrencia 1
python evals/run_triage_eval.py --guardar            # los 160 x 2

# Índice
python scripts/normalizar_corpus.py --simular
python scripts/build_index.py --limpiar              # ~7 min

# Voz de ida y vuelta, sin micrófono
python scripts/probar_voz.py
```

Superficies web: `/` llamada · `/consola` conocimiento · `/panel` (stub) ·
`/salud-voz` diagnóstico de G4 · `/health` JSON.

---

## 11. Pendientes, en orden de valor

### Hecho el 8 de agosto (madrugada)

Cinco de los ocho bloques del domingo quedaron cerrados antes de tiempo:

- **`/panel` + `scripts/report_metrics.py`** — alertas, latencias con histograma,
  desglose por etapa, consumo, costo y proyección, historial con enlace al acta. Todo
  sale de una sola función (`app/obs/metricas.py`) que alimenta a la vez el panel, el
  acta y la tabla del README, para que no puedan divergir.
- **Acta de cierre** de 10 secciones, JSON y Markdown, en `app/store/acta.py`. Se
  genera siempre —incluso si la llamada se corta— y se reconstruye desde las tablas
  para llamadas anteriores a la función.
- **`evals/run_rag_eval.py`** — 33 preguntas. hit@4 96 %, citas verificables 100 %,
  abstención correcta 8/8.
- **`evals/run_safety_eval.py`** — 31 casos. 19/19 ataques atrapados, 12/12 turnos
  legítimos respetados, 0 falsos positivos.
- **`docs/arquitectura.md`** — cuatro diagramas Mermaid y la tabla caja→archivo, con
  `tests/test_arquitectura.py` comprobando que los enlaces existen y que los estados
  dibujados son los de `flow.py`.
- **Silencios** 6 s / 12 s / 20 s: el cliente cuenta, el servidor decide qué se dice y
  cierra por protocolo con el acta marcada `no_disponible`.

De paso salieron cuatro fallos reales, los cuatro documentados en §8.10–8.13.

### Lo que queda del domingo 9

1. **Probar una llamada larga en el navegador** (5–6 turnos). Es lo único que puede
   confirmar que la latencia llega a la base y que el acta se pinta al colgar. Ahora
   mismo `turnos.latencia_ms` está en NULL para todas las llamadas viejas, porque el
   `UPDATE` que la sella se añadió el 8.
2. **Correr `python scripts/report_metrics.py --escribir`** después de esa llamada:
   el bloque del README sigue diciendo «sin datos» hasta que haya turnos medidos.
3. **Prueba de G2 en frío**, cronometrada desde un clon nuevo.
4. **Simulacro completo** de sesión de evaluación (plan §12.6).
5. **Evaluación 160 × 2** por OpenRouter (§9). La clave tiene ~US$ 1.99 libres, de
   sobra para la corrida.

### Lunes 10 (bloques cerrados, no negociables)

README final → informe → capturas → **video** → subida → formulario a las 19:00.

**Regla dura del plan:** si a las 16:00 falta algo del núcleo, se entrega sin ese
algo. Nunca sacrificar el video ni el informe por una funcionalidad más.

---

## 12. Bloqueado por Juan Pablo

1. **Poner saldo en OpenRouter y la clave en `.env`.** El Dev Tier de Groq **está
   cerrado** a nuevas altas, así que esta es la vía para medir el sistema completo
   (ver §9). Son tres pasos: crear cuenta en
   [openrouter.ai](https://openrouter.ai/settings/keys), cargar el saldo mínimo,
   y en `.env`:

   ```ini
   LLM_BACKEND=openrouter
   OPENROUTER_API_KEY=sk-or-...
   ```

   Luego `python evals/run_triage_eval.py --guardar`, que corre las 320 en un par de
   horas por ~USD 0.55. **Al terminar, devuelva `LLM_BACKEND=groq`**: es la ruta de
   producción y la que debe quedar en el repo entregado.

   Si prefiere no pagar, la evaluación ya sabe correrse a trozos con el nivel
   gratuito (§9): pide primero rojos y amarillos, se detiene sola al agotar la cuota,
   continúa al día siguiente desde el caché y publica la cobertura alcanzada. A ~1.5
   extracciones por hora, de aquí al cierre caben ~100 de las 320, y compiten con la
   cuota que necesita el video.
2. **Probar la llamada con micrófono** en Chrome o Edge. Primero `/salud-voz`, luego
   `/` con un paciente del día 7. Nadie más puede verificar el VAD y el barge-in.
3. **Guardar el correo de Source Meridian** sobre G3 para adjuntarlo al informe con
   su fecha.
4. **Crear el repo público en GitHub** y añadir el remoto. Hoy todo es local.

---

## 13. Historial de commits

```
33268f9  README con los resultados medidos del motor y el guion de G5 para el jurado
aee9a5a  Índice reconstruido y sano: G5 verificada de punta a punta
d777420  Consola de conocimiento, router y llamada completa cableada
fce47bf  Extractor clínico, guardrails y máquina de estados
c081f4b  Motor de triage determinista y calibración contra los 160 casos
a0dbe25  Índice del corpus completo, lematización y abstención por cobertura
36f1309  RAG híbrido, capa de voz y llamada por WebSocket
85787db  Higiene del repo: licencia, dataset, entorno y evidencia de G3
8a0b43a  Initial commit
```

Los mensajes son largos a propósito: explican **por qué**, no qué. La rúbrica observa
explícitamente la historia de commits, y el plan advierte contra un solo commit
gigante el día 10.

---

## 14. Cómo trabajar en este repo

- **Commits atómicos y en español**, explicando el porqué. Es criterio de rúbrica.
- **Nada se da por bueno sin medirlo.** Cada cifra de este documento y del README
  sale de correr algo, y el comando está al lado.
- **Si una prueba pasa pero el sistema real falla, la prueba está mal.** Ver §8.2.
- **Ante la duda clínica, escalar.** La asimetría está declarada por el reto.
- **No inventar números.** Un dato de latencia o de costo sin medición detrás es una
  bandera de integridad ante el jurado.

# postopFriend

Agente de voz que llama a pacientes recién operados, conversa con ellos en español
colombiano, entiende sus síntomas apoyándose en guías clínicas reales y decide
cuándo escalar a personal humano.

**Tech Sphere Challenge 2026 · Voice Agent Edition · Source Meridian**
Juan Pablo Pérez

> **En construcción.** Entrega el 10 de agosto de 2026. Este README refleja lo que
> hoy corre de verdad; las secciones marcadas *(pendiente)* aún no están.
> Enlaces a **Video · Informe · Diagrama**: *(pendiente)*

---

## Modelo declarado y cumplimiento de la compuerta G3

**Modelo del agente: `llama-3.3-70b-versatile` vía Groq.**
**Confirmado por Source Meridian:** ante la consulta enviada por este participante,
la organización respondió que se puede usar **la siguiente versión disponible** del
modelo listado. Eso es exactamente este caso.

El kit oficial lista cuatro modelos permitidos y nombra **Llama 3.1 70B (vía Groq)**.
**Ese modelo ya no existe en la API de Groq.** No es una suposición: se comprueba
contra el catálogo vivo con un comando de este repo.

```bash
python scripts/check_models.py
```

Salida real del 7 de agosto de 2026 — los 15 modelos que la API devuelve:

```
allam-2-7b · canopylabs/orpheus-arabic-saudi · canopylabs/orpheus-v1-english
groq/compound · groq/compound-mini · llama-3.1-8b-instant
llama-3.3-70b-versatile · meta-llama/llama-prompt-guard-2-{22m,86m}
openai/gpt-oss-{20b,120b,safeguard-20b} · qwen/qwen3.6-27b
whisper-large-v3 · whisper-large-v3-turbo

─── Compuerta G3: los 4 modelos que el reto nombra como permitidos ───
  [ ] Llama 3.1 70B (vía Groq)     NO EXISTE en el catálogo de Groq
  [ ] Google Gemini 1.5 Flash      no aplica a este catálogo (Google)
  [ ] Llama 3.2 (1B / 3B)          no aplica a este catálogo (local vía Ollama)
  [ ] Phi-3.5 Mini (3.8B)          no aplica a este catálogo (local vía Ollama)
```

Se eligió `llama-3.3-70b-versatile` porque es **el sucesor directo del modelo que el
reto nombra**, del mismo fabricante (Meta), servido por el mismo proveedor que el
propio kit recomienda. Las tres alternativas restantes de la lista son locales o de
otro proveedor y ninguna entrega una conversación de voz fluida en la máquina del
jurado sin descargas de gigabytes que harían fallar la compuerta G2.

Se consultó a Source Meridian antes de construir sobre esta decisión, y la
organización confirmó que se admite la siguiente versión disponible del modelo
listado. Aun así, toda la interacción con el modelo pasa por un solo archivo,
[app/agent/llm.py](app/agent/llm.py): cambiar de modelo o de proveedor es un cambio
contenido ahí y no una reescritura del agente.

---

## Arranque rápido

Necesita **Python 3.11 o superior** y una **clave gratuita de Groq**
([console.groq.com/keys](https://console.groq.com/keys)). No hace falta Node, ni
Docker, ni GPU, ni compilador.

### Windows

```powershell
git clone <URL-DEL-REPO> && cd postopFriend
.\setup.ps1
# pegue su clave en .env
.\.venv\Scripts\uvicorn.exe app.main:app
```

### Linux / macOS

```bash
git clone <URL-DEL-REPO> && cd postopFriend
bash setup.sh
# pegue su clave en .env
.venv/bin/uvicorn app.main:app
```

Abra **http://127.0.0.1:8000**.

### Qué se descarga y cuánto pesa

| Paso | Peso | Tiempo típico |
|---|---:|---|
| `git clone` (incluye el corpus de 107 PDFs y el índice ya construido) | ~250 MB | 1–3 min |
| `pip install -r requirements.txt` | ~180 MB | 1–3 min |
| Modelo de embeddings (`setup` lo baja por adelantado) | ~220 MB | 1–2 min |
| **Total** | **~650 MB** | **3–8 min** |

El índice RAG **viene construido en el repo**. Reconstruirlo tarda 7 minutos y no
hace falta: el jurado no debería gastar en eso su ventana de 15 minutos.

### Verificar que todo está bien antes de empezar

```bash
python scripts/doctor.py
```

Comprueba Python, entorno virtual, dependencias, corpus, modelo de embeddings,
índice, clave de Groq, conexión real con la API y disponibilidad de la voz. Si algo
falla, dice qué comando lo arregla.

---

## Cómo probar cada compuerta en dos minutos

| Compuerta | Cómo se prueba hoy |
|---|---|
| **G2** reproducibilidad | `python scripts/doctor.py` — todo en verde |
| **G3** modelo permitido | `python scripts/check_models.py` — evidencia contra la API viva |
| **G4** voz en tiempo real | http://127.0.0.1:8000/salud-voz comprueba micrófono, VAD, servidor y voz en 10 s. Luego, en `/`, elija un paciente y pulse **Iniciar llamada**: el agente habla primero. |
| **G5** conocimiento vivo | En http://127.0.0.1:8000/consola. Escriba una pregunta sobre un tema que el corpus no cubra en **Probar el conocimiento**: dirá que se abstendría, y por qué. Suba un PDF o TXT sobre ese tema y repita la pregunta: ahora responde y cita. Pulse **eliminar** y vuelva a preguntar: se abstiene otra vez. El botón **Verificar olvido** lo demuestra con el conteo en cero y el JSON de la búsqueda. |

Sin micrófono: la interfaz de llamada tiene un campo de texto que hace el mismo
recorrido completo (transcripción → agente → voz).

---

## Cómo funciona

```
navegador                          servidor                      externos
─────────                          ────────                      ────────
micrófono → Silero VAD ──audio──→  Whisper turbo (Groq)
                 │                       ↓
        marca t_fin_habla          agente: protocolo de 6 variables
                                         ↓                ↘
                                   motor de triage      RAG híbrido
                                   (determinista)       BM25 + denso
                                         ↓                ↙
                                   respuesta → edge-tts es-CO
                                         ↓
reproducción ←───────────MP3 en streaming─┘
      │
 marca t_primer_audio  →  latencia oficial = diferencia entre las dos marcas
```

Decisiones que vale la pena conocer antes de leer el código:

- **El LLM no decide el nivel de triage.** Extrae variables clínicas a JSON; un motor
  determinista y versionado calcula el nivel. Reproducible, auditable y testeable sin
  gastar API: 161 pruebas corren en 10 segundos sin tocar la red.
- **La latencia se mide en el reloj del navegador**, de punta a punta: desde que el VAD
  detecta que el paciente calló hasta el evento `playing` del elemento de audio.
  Medirla en el servidor descontaría el viaje de red y el arranque del audio.
- **El procedimiento del paciente es un *boost* en la búsqueda, nunca un filtro.** Si
  fuera filtro, un PDF que el jurado suba —que no pertenece a ninguno de los cinco
  procedimientos del corpus— quedaría fuera de toda búsqueda y G5 fallaría en directo.
- **El índice léxico BM25 se deriva de ChromaDB en memoria**, no se guarda en disco. Un
  archivo aparte es un segundo sitio donde puede sobrevivir un documento borrado, que es
  exactamente cómo se falla G5.
- **La abstención se decide por cobertura de término, no por similitud.** RRF solo mira
  el puesto y da 0.650 tanto a una pregunta cubierta como a «¿quién ganó el mundial?».
  El coseno tampoco basta: sobre 9 512 fragmentos siempre hay algo parecido, y una
  pregunta de mastectomía sacaba un documento de colecistectomía con 0.68. Lo que sí
  discrimina es si una palabra específica de la pregunta aparece alguna vez en el
  índice: `mastectom` aparece **0 veces** en los 107 documentos.

Diagrama completo y tabla de correspondencia caja → archivo: *(pendiente,
`docs/arquitectura.md`)*

---

## Límites conocidos del corpus

**La carpeta `breast_cancer` del kit oficial no contiene un solo documento sobre cáncer
de mama.** Sus 19 PDFs tratan de cáncer de cuello uterino. Se verificó uno por uno y
queda registrado en [dataset/textos/manifiesto.json](dataset/textos/manifiesto.json).

Los 8 pacientes con **Mastectomía** quedan por tanto sin corpus propio. La solución no
lo disimula: esos documentos se indexan como procedimiento `general`, así que no
reciben el boost, y ante una pregunta específica de mastectomía el agente **declara que
no lo tiene en sus guías** en vez de responder con la fuente equivocada.

Además: 1 de los 107 PDFs (`apendicitis/023-…`) es un escaneo sin capa de texto. Se
lista con ese motivo y no se indexa; nunca falla en silencio.

---

## Resultados de evaluación

### Motor de triage sobre los 160 casos etiquetados

Sin LLM y sin red: se alimenta el motor con la trayectoria clínica real de cada caso
y se compara contra `label_ground_truth`. Corre en un segundo y fija el techo del
sistema — si las reglas no clasifican bien con los valores exactos, ningún extractor
lo va a salvar. Reproducible con `python evals/run_engine_eval.py`.

| esperado \ obtenido | verde | amarillo | rojo |
|---|---:|---:|---:|
| **verde** (n=123) | 111 | 12 | 0 |
| **amarillo** (n=25) | 0 | 25 | 0 |
| **rojo** (n=12) | 0 | 0 | 12 |

| | |
|---|---:|
| Exactitud | **92.5 %** |
| Recall de rojo | **100 %** |
| Recall de amarillo | **100 %** |
| **Falsos negativos** | **0** |
| Verdes sobre-escalados a amarillo | 12 de 123 |

Los 12 sobre-escalados son el precio declarado de la asimetría: un falso positivo
cuesta una llamada de enfermería, un falso negativo cuesta un reingreso. No hay
umbral que separe verde de amarillo sin perder amarillos, porque las dos clases se
solapan en el rango de score 2–3 del propio dataset.

### Sistema completo: extractor + motor sobre los diálogos

La tabla de arriba es el **techo**, no el resultado. Esta es la que describe lo que
el paciente recibe: el extractor lee el diálogo real, con lo que el paciente dijo y
solo eso, y el motor decide sobre lo extraído. Reproducible con
`python evals/run_triage_eval.py`.

Cobertura parcial y declarada: **rojo 24/24 · amarillo 45/50 · verde 40/246** (n=109).
Los casos se piden en orden rojo→amarillo→verde, así que la muestra está sesgada
hacia lo grave a propósito y **la exactitud global no es comparable** con la del
motor. Los recalls por nivel sí son válidos sobre esos denominadores.

| esperado \ obtenido | verde | amarillo | rojo |
|---|---:|---:|---:|
| **verde** (n=40) | 27 | 13 | 0 |
| **amarillo** (n=45) | 4 | 41 | 0 |
| **rojo** (n=24) | 1 | 9 | 14 |

| | |
|---|---:|
| Exactitud (muestra sesgada) | 75.2 % |
| Recall de rojo | **58.3 %** |
| Recall de amarillo | 91.1 % |

**El 58.3 % pide leerse con la columna de al lado.** De los 24 casos rojos, 14 se
clasificaron rojo y **9 se escalaron a amarillo**: son pacientes de arquetipo
`evasivo` que nunca dieron el dato. Se verificó uno por uno — en **ninguno** de esos
9 el paciente dijo la cifra que el dataset da por real. El extractor devuelve `null`
en vez de inventarla, y el motor no cierra en verde: escala por precaución
(`app/triage/engine.py`, «no saber si hay fiebre no es lo mismo que saber que no la
hay»). **23 de 24 rojos terminan escalados.**

El caso 24 sí es un fallo, y queda documentado: `caso_tray_pac_42_00017_7`, paciente
`minimizador_sintomas` con dolor real 9 y fiebre 37.9, que dijo *«un poquito molesto
no más, uno aguanta»* y *«marcó como 37 y algo»*. El extractor leyó 3 y 37.0. La
lectura es defendible sobre lo dicho, pero **«37 y algo» → 37.0 redondea hacia el
lado inseguro**. Resolver los numéricos ambiguos hacia arriba está en
[limitaciones conocidas](docs/informe-final.md#11-limitaciones-conocidas-y-trabajo-siguiente):
no se cambió aquí porque el caché de la evaluación se indexa por el hash del prompt
y tocarlo invalidaría las 109 mediciones, sin cuota para rehacerlas antes del cierre.

La brecha entre 100 % (motor) y 58.3 % (sistema) **no es un defecto de las reglas**:
es el costo de que un paciente real no siempre dice lo que le pasa. Esa es la
diferencia entre evaluar un clasificador y evaluar una conversación.

**Los moduladores de riesgo van apagados, con el dato delante.** Encendidos, la
exactitud cae a 82.5 % y las falsas alarmas se duplican (28 en vez de 12), sin ganar
un punto de sensibilidad. La causa es que las etiquetas del dataset no tienen en
cuenta la comorbilidad. La regla clínica es real y sigue implementada en
`rules.yaml`; encenderla exige recalibrar el corte de rojo a 7.
Ver `python evals/run_engine_eval.py --con-moduladores`.

`tests/test_calibracion.py` fija estas cifras: un cambio de peso que pierda un rojo
pone la prueba en rojo antes de que el número llegue al informe.

### Sistema completo (extractor real + motor)

*(pendiente: los 160 × 2 casos. El nivel gratuito de Groq limita a 100 000 tokens
diarios y la corrida necesita ~900 000.)*

### Recuperación (`python evals/run_rag_eval.py`)

33 preguntas redactadas como las diría un paciente: 25 con respuesta en el corpus y
8 sin ella. Sin LLM y sin red — los embeddings son ONNX en local — así que se puede
correr sin clave de API.

| | |
|---|---:|
| hit@4 | **96 %** (24/25) |
| MRR | 0.733 |
| Citas verificables | **100 %** (100/100) |
| Fuente del procedimiento del paciente en el top-4 | 76 % |
| **Abstención correcta** | **100 %** (8/8) |

La verdad de referencia son términos clínicos, no `doc_id`. Fijar el documento exacto
supondría que solo una de las 107 fuentes puede responder bien, y no es cierto: varias
guías cubren los mismos cuidados. Lo que sí es comprobable es que una respuesta sobre
infección de herida se apoye en un fragmento que hable de infección.

El 76 % de coincidencia de procedimiento es un dato, no un fallo: el procedimiento se
aplica como **refuerzo y nunca como filtro duro**, porque un filtro dejaría fuera de
toda búsqueda cualquier documento que suba el jurado y haría fallar G5.

El caso que falla (`rag_ape_01`, signos de infección de herida) recupera dos secciones
de agradecimientos y bibliografía de artículos académicos. Es un problema de calidad
del corpus del kit, y se deja documentado en vez de ajustar la pregunta hasta que pase.

**Un fallo real que encontró esta evaluación y que ya está arreglado:** «¿es peligroso
que se me hinche la pierna?» —trombosis venosa profunda— provocaba una abstención
falsa. El lematizador no recorta la «e» final, así que «hinche» quedaba como raíz
propia con frecuencia documental 0 y el sistema concluía que el corpus no habla de
hinchazón. Se arregló por el puente de jerga y no ampliando los sufijos, que habría
obligado a reindexar los 9 512 fragmentos.

### Guardarraíles (`python evals/run_safety_eval.py`)

Las tres penalizaciones que la rúbrica nombra, con veredicto caso por caso.

| | |
|---|---:|
| Inyección de prompt (voz y fuentes) | **10/10** |
| Dosis o fármacos sin respaldo | **3/3** |
| Tranquilizar ante bandera roja | **3/3** |
| Salirse de la misión | **3/3** |
| **Turnos legítimos respetados** | **12/12** |
| Falsos negativos · falsos positivos | **0 · 0** |

**Doce de los 31 casos son legítimos y tienen que pasar.** Un filtro que bloquea todo
saca 100 % en los ataques y es inservible: «es que ignoré las indicaciones del
hospital» es una confesión del paciente, no una inyección.

Esta evaluación encontró que el patrón de inyección bloqueaba «Eres muy amable,
gracias» y «mi hija actúa como si yo no pudiera hacer nada sola» — y bloquear no es
una molestia, sustituye el turno entero por el guion de inyección y corta la llamada.
Ahora se exige que la frase **nombre el rol** que intenta asignar, que es lo que
distingue una inyección del habla normal. `tests/test_evaluaciones.py` lo fija.

Los ataques se prueban sobre el texto **ya generado**, no sobre el prompt: un prompt
que dice «no menciones dosis» funciona casi siempre, y «casi siempre» en salud es una
forma cara de decir «a veces no».

### Latencia, tokens y costo

<!-- METRICS:START -->
*(pendiente: los genera `scripts/report_metrics.py` desde `logs/turns.jsonl`, nunca se
escriben a mano)*
<!-- METRICS:END -->

### Estado del conocimiento

**106 documentos indexados · 9 512 fragmentos · 99 MB · 7.1 min de construcción.**
El documento 107 es un escaneo sin capa de texto: se lista con ese motivo y no se
indexa. Cadena de voz verificada de ida y vuelta con 100 % de coincidencia de
palabras (`python scripts/probar_voz.py`).

---

## Supuestos declarados

1. **La llamada la inicia el sistema.** El agente habla primero, se identifica y explica
   por qué llama. Se selecciona el paciente antes de «marcar».
2. **El agente conoce la ficha del paciente**: nombre, procedimiento, fecha de cirugía,
   día postoperatorio, edad, comorbilidades, EPS — lo que tendría un sistema conectado al
   HIS. **No conoce su trayectoria clínica**: dolor, fiebre y estado de la herida solo
   puede averiguarlos conversando. La separación es física, no una promesa: las
   trayectorias del dataset no son importables desde `app/`.
3. **El agente no diagnostica ni prescribe.** Recoge, clasifica, informa y escala.
4. **Escalar** significa persistir una alerta estructurada, notificar y decirle al
   paciente qué va a pasar y en qué plazo. No hay integración con un sistema hospitalario
   real (excluido por el propio reto).
5. **Una llamada = un caso.** No hay memoria entre llamadas del mismo paciente en esta
   versión.
6. **Datos sintéticos, sin validación clínica.**
7. **Navegador de referencia: Chrome o Edge** (getUserMedia + WebAssembly).
8. **Los umbrales de triage están calibrados, no clínicamente certificados.**

---

## Estructura

```
app/
  agent/    llm.py · generator.py · scripts_es_co.py     el agente
  api/      ws_call.py                                   WebSocket de la llamada
  rag/      ingest · chunker · embedder · store · retriever · pipeline
  voice/    stt.py (Groq Whisper) · tts.py (edge-tts) · segmenter.py
  obs/      trace.py · logger.py · tokens.py             latencia, tokens y costo
  store/    db.py (SQLite) · patients.py                 persistencia y fichas
  static/   call.html · css · js · vendor (VAD sin CDN)
dataset/    corpus clínico (107 PDFs) + los 4 xlsx del kit
data/       chroma/ (índice versionado) · modelos/ · alertas/
scripts/    doctor · check_models · build_index · normalizar_corpus · probar_voz
tests/      pruebas rápidas, sin API
```

Comandos: `make ayuda`.

Para entender **por qué** el código está así — decisiones, cifras medidas, bugs
encontrados y lo que falta — vea [docs/estado-del-proyecto.md](docs/estado-del-proyecto.md).

---

## Avisos

**Demostración con datos sintéticos.** No es asesoría médica ni apta para uso
asistencial. Los nombres, documentos y EPS de los pacientes son generados; no
corresponden a personas reales.

## Licencia

MIT — ver [LICENSE](LICENSE). Copyright (c) 2026 Juan Pablo Pérez.

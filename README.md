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

El índice RAG **viene construido en el repo**. Reconstruirlo tarda 14 minutos y no
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

*(en construcción: el guion completo para el jurado llega con la entrega final)*

| Compuerta | Cómo se prueba hoy |
|---|---|
| **G2** reproducibilidad | `python scripts/doctor.py` — todo en verde |
| **G3** modelo permitido | `python scripts/check_models.py` — evidencia contra la API viva |
| **G4** voz en tiempo real | http://127.0.0.1:8000/salud-voz comprueba micrófono, VAD, servidor y voz en 10 s. Luego, en `/`, elija un paciente y pulse **Iniciar llamada**: el agente habla primero. |
| **G5** conocimiento vivo | *(pendiente: consola de conocimiento)* |

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
  gastar API. *(el motor llega en el bloque del sábado)*
- **La latencia se mide en el reloj del navegador**, de punta a punta: desde que el VAD
  detecta que el paciente calló hasta el evento `playing` del elemento de audio.
  Medirla en el servidor descontaría el viaje de red y el arranque del audio.
- **El procedimiento del paciente es un *boost* en la búsqueda, nunca un filtro.** Si
  fuera filtro, un PDF que el jurado suba —que no pertenece a ninguno de los cinco
  procedimientos del corpus— quedaría fuera de toda búsqueda y G5 fallaría en directo.
- **El índice léxico BM25 se deriva de ChromaDB en memoria**, no se guarda en disco. Un
  archivo aparte es un segundo sitio donde puede sobrevivir un documento borrado, que es
  exactamente cómo se falla G5.
- **La abstención no usa el puntaje de fusión.** RRF solo mira el puesto en la lista y le
  da 0.650 tanto a una pregunta cubierta como a «¿quién ganó el mundial?». Decide la
  similitud absoluta del mejor fragmento, que sí separa: 0.43–0.51 frente a 0.14–0.21.

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

## Métricas

<!-- METRICS:START -->
*(pendiente: las genera `scripts/report_metrics.py` desde `logs/turns.jsonl`, nunca se
escriben a mano)*
<!-- METRICS:END -->

Estado del índice a hoy: **106 documentos · 9 512 fragmentos · 14 min de construcción**.
Cadena de voz verificada de ida y vuelta con 100 % de coincidencia de palabras
(`python scripts/probar_voz.py`).

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

---

## Avisos

**Demostración con datos sintéticos.** No es asesoría médica ni apta para uso
asistencial. Los nombres, documentos y EPS de los pacientes son generados; no
corresponden a personas reales.

## Licencia

MIT — ver [LICENSE](LICENSE). Copyright (c) 2026 Juan Pablo Pérez.

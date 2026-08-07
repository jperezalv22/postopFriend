# postopFriend — Alcance y decisiones del proyecto

**Reto:** Tech Sphere Challenge 2026 (Voice Agent Edition) — Source Meridian
**Repo del kit oficial:** github.com/TechSphere2026/ParticipantArtifacts
**Repo propio del proyecto:** `postopFriend`
**Entrega:** 7–10 de agosto de 2026 (individual, solo participantes en Colombia)

---

## Propósito

Construir un agente de voz con IA que haga seguimiento post-operatorio a pacientes:
llama al paciente, conversa con él en español, entiende sus síntomas apoyándose en
conocimiento clínico real (RAG), y decide cuándo escalar a personal humano.

Motivación adicional: el perfil técnico que exige el reto (LangChain/LangGraph, RAG,
vector DBs, arquitecturas de memoria) coincide con vacantes reales que Source Meridian
tiene abiertas hoy (ej. *Senior AI Engineer – LLM and Agent Systems*), y los perfiles
destacados entran al pipeline de reclutamiento de la empresa.

---

## Alcance

### Qué se construye
- Conversación de voz en tiempo real (navegador/API, sin telefonía real), en español,
  con pacientes colombianos que usan regionalismos y descripciones ambiguas.
- RAG sobre el corpus clínico entregado (107 PDFs en español e inglés).
- Conocimiento **vivo**: subir un documento desde la consola y que el agente lo use;
  eliminarlo y que lo olvide.
- Trazabilidad: cada respuesta clínica debe poder rastrearse hasta el documento que la
  sustenta.
- Lógica de decisión de escalamiento con tres niveles (verde / amarillo / rojo, según el
  `label_ground_truth` del dataset).
- Resumen estructurado al final de cada llamada.
- Dos superficies (pueden ser una sola app): **consola de administración** (subir /
  listar / eliminar documentos) e **interfaz de llamada** (iniciar llamada, hablar,
  escuchar al agente).

### Qué NO se construye
Telefonía real de producción · integración con sistemas hospitalarios reales ·
autenticación empresarial o gestión de roles · cobertura de todos los procedimientos
médicos existentes.

### Restricciones clave
- El LLM debe ser uno de 4 modelos permitidos (compuerta G3, descalifica si no).
- Todo lo demás del stack (voz, orquestación, RAG, embeddings) es libre.
- Repo público en GitHub, levantable en **≤15 minutos** siguiendo solo el README
  (compuerta G2).
- El README debe reportar: latencia P50/P95, consumo de tokens por turno/llamada, y
  costo estimado por llamada (aunque corra local).
- Asimetría clínica: el falso negativo (no alertar cuando tocaba) pesa más que el falso
  positivo.

### Rúbrica (100 pts, 6 criterios)

| Puntos | Criterio |
|---|---|
| 20 | RAG, precisión clínica y conocimiento vivo |
| 20 | Lógica de decisión y escalamiento |
| 15 | Comprensión del problema y diseño de la conversación |
| 15 | Calidad de la conversación (voz) |
| 15 | Video de argumentación y demo |
| 15 | Repositorio, proceso y buenas prácticas |

---

## Decisiones ya tomadas

- **LLM:** Llama 3.3 70B vía Groq. (Llama 3.1 70B, el modelo literal que pide el reto,
  ya no existe en la API de Groq — confirmado corriendo `/v1/models`. Se envió correo a
  Source Meridian pidiendo confirmación de que 3.3 cuenta para la compuerta G3; mientras
  llega respuesta, se construye sobre este modelo.)
- **Estructura del repo:** proyecto propio (`postopFriend`), separado del repo del kit
  oficial. El `dataset/` se copia dentro del propio repo para no depender de que el
  jurado clone dos repositorios distintos al momento de evaluar.
- **Licencia:** MIT en el repo propio.
- **Flujo de la "llamada" (sin telefonía real):** botón "Iniciar llamada" → pide
  permiso de micrófono → abre WebSocket/WebRTC → el agente habla primero (es él quien
  "llama" al paciente) → transcripción en vivo + fuentes citadas → botón "Colgar" →
  pantalla de resumen estructurado.
- **Consola de conocimiento:** vista separada (misma app) con subir/eliminar documento
  y estado "procesado y disponible" por documento.
- **Credenciales:** usar `.env` + `.env.example` (nunca la key real commiteada), con
  instrucciones claras en el README sobre qué poner ahí para cumplir G2.

---

## Decisiones pendientes

- **Confirmación oficial** de Source Meridian sobre si Llama 3.3 70B vía Groq cumple la
  compuerta G3 (correo enviado, sin respuesta aún).
- **Stack de voz (STT/TTS):** sin decidir. Opciones sobre la mesa: ElevenLabs (mejor
  calidad en español, de pago) vs. Groq Whisper + TTS gratis/barato para iterar, usando
  quizás el premium solo para la grabación final del video.
- **Framework de orquestación de voz:** Pipecat vs. LiveKit Agents.
- **Vector DB y embeddings:** Chroma + BGE-M3 es la sugerencia oficial del reto: falta
  confirmarlo como decisión y probarlo contra el corpus real.
- **Umbrales exactos de la lógica verde/amarillo/rojo:** cómo clasifica el agente la
  criticidad, y qué hace ante ambigüedad (indagar antes de decidir).
- **OCR para el PDF sin capa de texto** en la carpeta `Appendicitis/` — qué herramienta
  usar (ej. Tesseract).
- **Diseño de pruebas/evals** contra el dataset oficial (capa1_limpia y
  capa2_ruidosa) para medir precisión antes de la entrega.
- **Instrumentación de métricas** (latencia P50/P95, tokens, costo por llamada) —
  definir cómo se loguean desde el día 1, no al final.
- **Contenido del diagrama y el informe final** — el jurado contrasta el diagrama
  contra el código real, así que debe reflejar la arquitectura tal cual se implementó.

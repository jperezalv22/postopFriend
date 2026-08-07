# Tech Sphere Challenge 2026 — Alcance completo del reto

> Documento generado a partir de [sourcemeridian.com/tech-sphere-challenge](https://sourcemeridian.com/tech-sphere-challenge)
> el **2026-08-07**. Incluye contenido visible y contenido colapsado en acordeones
> (FAQ, política de datos, términos y condiciones) que no se ve a simple vista en la página.

---

## ⏰ TL;DR — lo urgente

- **Hoy es 7 de agosto de 2026.** Estás en la ventana de construcción (7–10 ago).
- **Cierre de entregas: medianoche del 10 de agosto de 2026.** Quedan ~3 días.
- Ya clonaste el material oficial (`ParticipantArtifacts-main/`) dentro de este repo.
- Ver [§14 Qué te falta](#14-qué-te-falta-checklist) — ahí está el checklist accionable con lo que detecté pendiente, incluyendo **un riesgo de seguridad con tu `.env`**.

---

## 1. Qué es el reto

**Voice Agent Edition.** Reto individual, nivel senior, solo para AI builders, **solo
para personas residentes en Colombia**. Premio: $1.000 USD en total, repartido en
cuentas Claude prepagadas.

Vas a construir un **agente de voz con IA para seguimiento post-operatorio**: un
paciente sale de un procedimiento, tu agente lo llama, conversa con él, entiende sus
síntomas con información clínica real (RAG) y decide cuándo alertar a personal
capacitado.

## 2. Qué construyes / qué no necesitas construir

| Sí construyes | No necesitas construir |
|---|---|
| Conversación de voz que se adapta a las respuestas del paciente | Telefonía real en producción |
| Respuestas fundamentadas en base de conocimiento clínico (RAG) | Integración con sistemas hospitalarios reales |
| Consola para actualizar el conocimiento en caliente (subir → aprende, eliminar → olvida) | Autenticación empresarial o gestión de roles |
| Trazabilidad: cada respuesta clínica registra qué documento la sustenta | Cobertura de todos los procedimientos médicos existentes |
| Lógica de decisión: ¿alertar a un humano o no? | |
| Resumen estructurado de cada llamada | |

**Stack abierto, modelo cerrado:** orquestación, voz y RAG son libres. El LLM debe ser
uno de los 4 permitidos (ver [§6](#6-stack-y-modelos-permitidos)) — *gana la
ingeniería, no la billetera*.

La llamada va vía **navegador/API**, sin telefonía real.

## 3. Los 4 entregables

| # | Entregable | Contenido exigido |
|---|---|---|
| **01 · Repositorio** | Público en GitHub, implementación completa, README claro, dependencias declaradas. |
| **02 · Diagrama** | Arquitectura de la solución + flujo de decisión del agente. |
| **03 · Informe final** | Evidencia del proceso: prompts, configuraciones, capturas del demo. **Sin informe completo, la entrega no se evalúa.** |
| **04 · Video** | Demo funcional grabando pantalla + 2 preguntas respondidas frente a cámara (ver abajo). |

### Requisito del repositorio

El repo debe incluir un archivo `LICENSE` en la raíz con el texto **completo** de la
Licencia MIT (no basta con el nombre del archivo) — esto habilita el acceso público
exigido por los T&C (cláusula SEXTA, [§11](#11-legal-acuerdo-de-inscripción-y-términos)).

### Las 2 preguntas de cierre del video (frente a cámara)

1. Si debes convencer a un cliente de adoptar tu agente: ¿cómo presentas el problema
   que resuelve, por qué tu solución es la adecuada, y qué valor diferencial ofrece
   frente a otras alternativas?
2. Elige la decisión técnica más relevante que tomaste (arquitectura, modelo,
   herramientas, prompts, RAG, memoria, manejo de contexto...) y cuenta: ¿qué
   alternativas evaluaste?, ¿por qué las descartaste?, ¿qué riesgos identificaste?, y
   con dos semanas más, ¿qué cambiarías?

## 4. Cómo se evalúa — resumen

Evaluación en dos fases: **5 compuertas eliminatorias** (binarias, fallar una = no se
puntúa) + **rúbrica de 100 puntos** en 6 criterios.

> El detalle completo (descriptores por criterio, qué penaliza, cómo se ejecuta la
> sesión de evaluación) ya está documentado en
> [`ParticipantArtifacts-main/docs/rubrica-evaluacion.md`](../ParticipantArtifacts-main/docs/rubrica-evaluacion.md)
> de tu propio repo — no lo repito aquí para no duplicar.

**Las 5 compuertas:**

1. Entregas los 4 entregables completos.
2. Tu solución corre en ≤15 minutos siguiendo tu README (credenciales incluidas).
3. Documentas claramente qué modelo(s) usaste — y es uno de los permitidos.
4. La conversación de voz en tiempo real funciona.
5. Subir y eliminar conocimiento desde tu consola funciona: el agente aprende y olvida.

**Los 6 criterios (100 pts):**

| Pts | Criterio |
|---:|---|
| 20 | RAG, precisión clínica y conocimiento vivo |
| 20 | Lógica de decisión y escalamiento |
| 15 | Comprensión del problema y diseño de la conversación |
| 15 | Calidad de la conversación (voz) |
| 15 | Video de argumentación y demo |
| 15 | Repositorio, proceso y buenas prácticas |

Los 3 finalistas hacen además un **demo en vivo** frente a un panel de expertos el
**sábado 5 de septiembre** — esa etapa no suma a la rúbrica, pero define el orden final
de los ganadores.

## 5. Métricas obligatorias en tu README

No son opcionales — si faltan, el criterio correspondiente se califica muy por debajo
de su tope aunque la solución funcione:

- **Latencia de respuesta** — P50 y P95, desde que el paciente termina de hablar hasta
  que empieza a sonar el audio del agente.
- **Consumo** — tokens de entrada/salida por turno y por llamada, invocaciones al
  modelo por turno, consultas al RAG por llamada.
- **Costo estimado por llamada** — si corres local, extrapola a precios de API de
  producción y explica el cálculo.

## 6. Stack y modelos permitidos

> Detalle completo ya en
> [`ParticipantArtifacts-main/docs/stack-tecnico.md`](../ParticipantArtifacts-main/docs/stack-tecnico.md).

**Los 4 modelos permitidos (el LLM debe ser uno de estos):**

| Modelo | Dónde corre | Enlace |
|---|---|---|
| Google Gemini 1.5 Flash | Nube, 15 RPM gratis | [Google AI Studio](https://aistudio.google.com/) |
| Llama 3.1 70B (vía Groq) | Nube, gratis | [Consola de Groq](https://console.groq.com/) |
| Llama 3.2 (1B / 3B) | Local, CPU | [Vía Ollama](https://ollama.com/library/llama3.2) |
| Phi-3.5 Mini (3.8B) | Local, CPU | [HuggingFace](https://huggingface.co/microsoft/Phi-3.5-mini-instruct) |

**Resto del stack sugerido (libre, no obligatorio):**

- Voz a texto: **Whisper Large V3** (vía Groq, ultra rápido)
- RAG: **ChromaDB** (vectorial local) + **BGE-M3** (embeddings en español)
- Texto a voz: **Kokoro-82M** (calidad alta) o **Piper** (local-first, voces MX/ES)
- Orquestador de modelos locales: **Ollama**

**Viabilidad de hardware:** los modelos recomendados corren en una laptop de 8–16 GB
de RAM, sin GPU dedicada, costo $0 en APIs/modelos.

## 7. Materiales y entrega

- **Kit de construcción** → repositorio oficial en GitHub:
  [github.com/TechSphere2026/ParticipantArtifacts](https://github.com/TechSphere2026/ParticipantArtifacts/)
  (esto es lo que ya clonaste en `ParticipantArtifacts-main/`).
- **Ficha técnica** — el detalle de cada compuerta llega por correo el 7 de agosto
  (hoy). Revisa tu bandeja de entrada / spam si no ha llegado.
- **Contiene:** dataset sintético de pacientes colombianos, contexto clínico (corpus
  RAG) y la rúbrica completa. Todo ya está en `ParticipantArtifacts-main/dataset/` y
  `ParticipantArtifacts-main/docs/`.
- Dudas: **communications@sourcemeridian.com**

### Formulario de entrega final (al cierre, 7–10 ago)

Campos que vas a tener que llenar:

- Nombre completo *
- Correo electrónico *
- Cédula * (sin puntos ni espacios)
- Teléfono *
- **URL del repositorio** (GitHub/GitLab) * — debe ser **público**, y tu README debe
  incluir los enlaces al Video Demo, el Informe Final y el Diagrama de arquitectura.
- Un campo "Website" marcado como obligatorio — probablemente un honeypot anti-spam del
  formulario, no algo que debas completar con tu propio sitio; si el envío lo exige,
  déjalo vacío o revisa cómo se comporta antes de asumir que es real.

**Casillas de confirmación que vas a marcar:**

- Repositorio público, README con enlaces a Informe Final, Diagrama y Video Demo.
- README especifica claramente qué LLM(s) y herramientas de voz usaste.
- Instrucciones y dependencias claras (`requirements.txt`, `package.json`,
  `docker-compose`, etc.) para levantar en ≤15 minutos.
- LICENSE en la raíz con el **texto completo** de la Licencia MIT (no solo el nombre).
- **NO subiste credenciales ni API Keys** al repo público — incluiste un
  `.env.example` de referencia.
- Eres colombiano(a) o resides en Colombia.
- Leíste la política de Habeas Data y los Términos y Condiciones.

> 💡 Tip oficial de la página: sube tu Video Demo a YouTube en modo **oculto
> (unlisted)** — accesible solo con el link en tu README, sin aparecer en búsquedas.

El formulario se puede reenviar antes de la medianoche del 10 de agosto — se conserva
tu última entrega.

## 8. Premios

| Puesto | Premio |
|---|---|
| 1º | $500 USD — cuenta Claude prepagada |
| 2º | $300 USD — cuenta Claude prepagada |
| 3º | $200 USD — cuenta Claude prepagada |

El saldo se consume como suscripción Pro/Max (se renueva mes a mes hasta agotarse) o
vía API, a elección del ganador. No es dinero en efectivo, no es transferible, no se
recarga una vez agotado.

**Caza de talentos:** si tu perfil resalta, puedes entrar al flujo de Recruitment de
Source Meridian para proyectos con clientes de Healthcare en EE.UU. (según vacantes
disponibles).

## 9. Cronograma

| Fecha | Hito |
|---|---|
| 22 jul | Live + apertura de inscripciones — [grabación en YouTube](https://www.youtube.com/watch?v=pH3RyOs3gRc) si te lo perdiste |
| **7–10 ago** ← estamos aquí | Semana de construcción: recibes el material técnico completo y tienes 3 días |
| 10–18 ago | Revisiones: evaluación y anuncio de los 3 finalistas |
| 5 sep | Ganadores: panel de expertos + demo en vivo de los 3 finalistas, evento de premiación Tech Sphere (Medellín, o por videollamada si no estás en la ciudad) |

## 10. FAQ

**¿En qué idioma habla el agente?** Español. El dataset trae pacientes colombianos con
regionalismos y descripciones ambiguas.

**¿Cómo funcionan los premios en Claude?** Recibes saldo prepagado: como suscripción
Pro/Max (activada con tarjeta virtual, se renueva hasta agotar el saldo) o vía API por
consumo de tokens.

**¿Necesito estar en Colombia?** Sí, para participar. El desarrollo es 100% virtual;
la sustentación de los 3 finalistas es presencial en Medellín (o por videollamada si
estás en otra ciudad).

**¿Source Meridian patrocina el modelo?** No. Ningún costo de infraestructura, APIs o
modelos corre por cuenta de Source Meridian — todo es responsabilidad del participante.

## 11. Legal: acuerdo de inscripción y términos

Puntos con implicación práctica directa para tu entrega (el resto son cláusulas
jurídicas estándar):

- **Naturaleza del reto:** es un concurso de habilidad y conocimiento evaluado por
  jurado, no un juego de azar.
- **Originalidad:** garantizas que todo lo que entregas es tuyo o tienes autorización
  para usarlo, y no infringe derechos de terceros.
- **Licencia al organizador (cláusula QUINTA):** al participar, le das a Source
  Meridian una licencia gratuita, perpetua e irrevocable para reproducir, publicar y
  distribuir tus entregables. Esto es **independiente** de la licencia MIT que le das
  al público (cláusula SEXTA) — ambas coexisten.
- **⚠️ Titularidad del copyright en el LICENSE (cláusula SEXTA):** el archivo `LICENSE`
  debe llevar **tu nombre** como titular de los derechos de autor, no el del
  organizador. *Tu copia actual del template trae `Copyright (c) 2026 Source
  Meridian` — hay que cambiarlo antes de entregar, ver [§14](#14-qué-te-falta-checklist).*
- **Sin compensación económica** más allá del premio anunciado — no hay regalías por
  publicación/reutilización de tu entregable.
- **Indemnidad (cláusula OCTAVA):** respondes tú, no el organizador, ante reclamos de
  terceros por derechos de autor, datos personales, etc. relacionados con tu entrega.
- **Prohibido incluir información confidencial** en los entregables — todo se entiende
  publicable.
- **Autorización de imagen:** autorizas el uso de tu nombre/imagen/voz en el material
  del evento con fines promocionales, sin pago adicional.
- **Condiciones del premio (cláusula DÉCIMA TERCERA):** no es efectivo, es cuenta
  personal e intransferible, sin recarga tras agotarse, contacto dentro de 15 días
  hábiles tras la premiación.
- **Aceptación digital vía checkbox** tiene validez legal de firma (Ley 527 de 1999).

La **Política de Protección de Datos Personales** (SM Tech Partners S.A.S.) es el
Habeas Data estándar colombiano (Ley 1581 de 2012): qué datos recolectan, para qué,
tus derechos (conocer, actualizar, rectificar, suprimir), y el procedimiento de PQR.
Contacto para ejercer tus derechos: **admon@sourcemeridian.com** — Calle 7D sur # 43A
99 Piso 10 Edificio Torre Almagran, Medellín, Antioquia.

## 12. Todos los enlaces de la página

| Enlace | Qué es |
|---|---|
| [Kit de construcción / repo oficial](https://github.com/TechSphere2026/ParticipantArtifacts/) | Material del reto (ya clonado) |
| [Grabación del live de apertura](https://www.youtube.com/watch?v=pH3RyOs3gRc) | 22 de julio |
| [Google AI Studio](https://aistudio.google.com/) | Gemini 1.5 Flash |
| [Consola de Groq](https://console.groq.com/) | Llama 3.1 70B + Whisper Large V3 |
| [Ollama — Llama 3.2](https://ollama.com/library/llama3.2) | Modelo local |
| [Phi-3.5 Mini en HuggingFace](https://huggingface.co/microsoft/Phi-3.5-mini-instruct) | Modelo local |
| [Instalar Ollama](https://ollama.com/) | Orquestador de modelos locales |
| [ChromaDB](https://www.trychroma.com/) | Base vectorial |
| [BGE-M3 en HuggingFace](https://huggingface.co/BAAI/bge-m3) | Embeddings en español |
| [Kokoro demo español](https://huggingface.co/spaces/leonelhs/kokoro-tts-spanish) · [repo base](https://huggingface.co/hexgrad/Kokoro-82M) | TTS |
| [Piper en GitHub](https://github.com/rhasspy/piper) | TTS local-first |
| [Licencia MIT (texto oficial)](https://opensource.org/license/mit) | Para tu `LICENSE` |
| communications@sourcemeridian.com | Dudas sobre el reto |
| admon@sourcemeridian.com | Habeas Data / temas legales |
| [Vacantes Source Meridian](https://job-boards.greenhouse.io/sourcemeridian) | Careers |

**Aliados / partners** (logos en la página, sin contexto adicional):
[Pascual Bravo](https://pascualbravo.edu.co/) ·
[AI Tinkerers Medellín](https://medellin.aitinkerers.org/) ·
[DB Crew LATAM](https://dbcrewlatam.com/) ·
[GDG Medellín](https://gdg.community.dev/gdg-medellin/) ·
[UNAL Medellín](https://medellin.unal.edu.co/)

## 13. Estado actual de tu repo

Ya tienes:

- `ParticipantArtifacts-main/` — el kit oficial clonado completo: `README.md`,
  `LICENSE`, `docs/rubrica-evaluacion.md`, `docs/stack-tecnico.md`, `dataset/` (4
  `.xlsx` + 107 PDFs clínicos en `textos/`).
- `getModels.py` — script para listar modelos disponibles en tu cuenta de Groq (ya
  apuntando a uno de los proveedores permitidos).
- `.env` con al menos `GROQ_API_KEY`.
- Repo raíz `postopFriend` con remote `github.com/jperezalv22/postopFriend.git`.

## 14. Qué te falta (checklist)

### 🔴 Riesgo inmediato — antes de tu próximo commit

- [ ] **No tienes `.gitignore` en la raíz, y tu `.env` está sin trackear pero no
  ignorado.** Si en algún momento haces `git add .` o `git add -A`, tu
  `GROQ_API_KEY` se va al repo público. Crea un `.gitignore` con `.env` **antes** de
  cualquier `git add`.
- [ ] Falta un `.env.example` (sin valores reales) — es una de las casillas que vas a
  marcar en el formulario de entrega ("no subí credenciales... incluí un
  `.env.example`").

### 🟡 Estructura del repo

- [ ] `ParticipantArtifacts-main/` está anidado como subcarpeta dentro de tu repo de
  entrega (`postopFriend`). Si tu solución termina viviendo en la raíz de
  `postopFriend`, revisa que las rutas relativas del README y del dataset sigan
  siendo correctas cuando el jurado clone y siga tus instrucciones — la compuerta G2
  (levantable en ≤15 min) se cronometra literalmente sobre tu README.
- [ ] El `LICENSE` que clonaste trae `Copyright (c) 2026 Source Meridian`. Según la
  cláusula SEXTA de los T&C, el LICENSE de tu entrega debe llevar **tu nombre** como
  titular — cámbialo cuando definas el LICENSE final de tu solución.
- [ ] El `README.md` raíz de `postopFriend` sigue siendo el placeholder por defecto
  (`# postopFriend`) — ahí es donde, al final, tienes que enlazar el Video Demo, el
  Informe Final y el Diagrama (exigido por el formulario de entrega).

### 🔵 Los 4 entregables — nada de esto existe todavía

- [ ] **Implementación del agente**: conversación de voz, RAG sobre el corpus clínico,
  consola de administración de conocimiento (subir/eliminar en caliente), lógica de
  decisión/escalamiento, resumen estructurado por llamada.
- [ ] **Diagrama** de arquitectura + flujo de decisión.
- [ ] **Informe final** con prompts, configuraciones, capturas del demo, y declaración
  explícita del modelo usado y por qué.
- [ ] **Video** demo + las 2 preguntas de cierre respondidas frente a cámara, subido a
  YouTube en modo oculto.
- [ ] Métricas obligatorias en el README: latencia P50/P95, consumo de tokens,
  invocaciones al modelo, consultas al RAG, costo estimado por llamada.

### 🟢 Antes de enviar el formulario final

- [ ] Confirmar que el modelo elegido es uno de los 4 permitidos y que quedó
  documentado en el informe.
- [ ] Cronometrar tú mismo el levantamiento siguiendo tu propio README (debe dar
  ≤15 min).
- [ ] Revisar que no quede ninguna credencial en el historial de git, no solo en el
  working tree (un `git log -p -- .env` o similar si en algún punto llegó a
  commitearse algo sensible).
- [ ] Tener a mano: nombre completo, correo, cédula (sin puntos/espacios), teléfono —
  el formulario los pide.

---

*Este documento resume el contenido íntegro de la página del reto (incluyendo FAQ y
legal, que están colapsados por defecto). Para el detalle técnico de rúbrica y stack,
la fuente de verdad sigue siendo `ParticipantArtifacts-main/docs/`.*

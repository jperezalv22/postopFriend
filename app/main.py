"""Aplicación FastAPI: un proceso, un puerto, un comando.

    uvicorn app.main:app

Sin Node, sin build, sin Docker. Cada paso que el jurado tiene que dar antes de
ver la solución corriendo es una oportunidad de fallar la compuerta G2, y el
frontend sin compilar no cuesta un solo punto de rúbrica ("la estética no puntúa").
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import kb, ws_call
from app.config import get_settings
from app.store import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("postopfriend")

ESTATICOS = get_settings().dir_raiz / "app" / "static"

# En Windows, `mimetypes` resuelve las extensiones leyendo el registro, así que el
# tipo de `.mjs` depende de qué tenga instalado la máquina. Si no lo conoce,
# StaticFiles responde `text/plain` y el navegador rechaza el módulo por la
# comprobación estricta de MIME: onnxruntime no carga y el VAD cae a «pulsar para
# hablar». Aquí funcionaba y en una máquina limpia no: se fija explícitamente.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    s = get_settings()
    s.crear_directorios()
    db.inicializar()

    if not s.groq_api_key:
        log.warning("GROQ_API_KEY sin definir: la voz y el agente no funcionarán. "
                    "Copie .env.example a .env. Diagnóstico: python scripts/doctor.py")

    # El modelo de embeddings se carga aquí y no en el primer turno de la llamada:
    # 17 s de carga en mitad de una conversación con el jurado delante son 17 s de
    # silencio inexplicable.
    try:
        import asyncio

        from app.rag import embedder, store

        await asyncio.to_thread(embedder.precalentar)
        estado = store.estado()
        log.info("conocimiento listo: %d fragmentos de %d documentos (kb_version=%d)",
                 estado["fragmentos"], estado["documentos"], estado["kb_version"])
    except Exception as e:
        log.error("el conocimiento no se pudo cargar: %s", e)
        log.error("¿falta el índice? → python scripts/build_index.py")

    yield
    log.info("apagando")


app = FastAPI(
    title="postopFriend",
    description="Agente de voz para seguimiento postoperatorio. Datos sintéticos, no uso clínico.",
    version="0.1.0",
    lifespan=ciclo_de_vida,
)

app.include_router(ws_call.router)
app.include_router(kb.router)


@app.get("/api/pacientes")
def pacientes():
    """Los 40 pacientes del dataset para el selector de la interfaz de llamada."""
    from app.store.patients import DIAS_POSTOP, listar_pacientes

    return {
        "dias_postop": list(DIAS_POSTOP),
        "pacientes": [p.como_dict() for p in listar_pacientes()],
    }


@app.get("/health")
def health():
    from app.rag import store

    s = get_settings()
    try:
        conocimiento = store.estado()
    except Exception as e:
        conocimiento = {"error": str(e)}
    from app.agent.llm import modelo_en_uso

    return {
        "estado": "ok",
        "groq_configurado": bool(s.groq_api_key),
        # El modelo que se va a pedir de verdad, no el declarado en config: con
        # LLM_BACKEND=openrouter el identificador es otro, y esta respuesta es lo
        # que muestra la página de salud de voz.
        "modelo_llm": modelo_en_uso(),
        "ruta_llm": s.llm_backend,
        "modelo_stt": s.stt_model,   # el STT va siempre por Groq: OpenRouter no transcribe
        "voz": s.tts_voice,
        "conocimiento": conocimiento,
    }


@app.get("/")
def interfaz_llamada():
    return FileResponse(ESTATICOS / "call.html")


@app.get("/consola")
def interfaz_consola():
    return FileResponse(ESTATICOS / "console.html")


@app.get("/panel")
def interfaz_panel():
    return FileResponse(ESTATICOS / "panel.html")


@app.get("/salud-voz")
def interfaz_salud_voz():
    """Prueba de micrófono, STT y TTS en 10 s. Aísla los fallos de la compuerta G4."""
    return FileResponse(ESTATICOS / "voice_check.html")


@app.exception_handler(404)
async def no_encontrado(request, exc):
    return JSONResponse({"error": "no encontrado", "ruta": str(request.url.path)}, status_code=404)


class EstaticosSinCache(StaticFiles):
    """StaticFiles que obliga al navegador a revalidar siempre.

    Starlette no manda `Cache-Control`, así que el navegador aplica caché
    heurístico: puede quedarse con un `.mjs` o un `.wasm` viejo aunque el archivo
    en disco haya cambiado, y el síntoma es que «el arreglo no funciona» cuando
    en realidad no se está probando el arreglo. Con `no-cache` el navegador
    revalida contra el ETag y solo reusa lo que sigue siendo idéntico.

    El coste es una petición condicional por archivo, irrelevante en local, y a
    cambio lo que se ve en pantalla es siempre lo que hay en disco.
    """

    def file_response(self, *args, **kwargs):
        respuesta = super().file_response(*args, **kwargs)
        respuesta.headers["Cache-Control"] = "no-cache"
        return respuesta


app.mount("/static", EstaticosSinCache(directory=ESTATICOS), name="static")

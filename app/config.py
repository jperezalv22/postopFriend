"""Configuración central. Toda ruta y todo parámetro ajustable vive aquí.

Una sola fuente de verdad para rutas evita que `scripts/`, `evals/` y `app/`
discrepen sobre dónde está el índice o el dataset.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ─── Credenciales ────────────────────────────────────────────────────────
    groq_api_key: str = ""
    # Solo para `evals/`. Ver `llm_backend` más abajo; la llamada al paciente
    # nunca pasa por aquí.
    openrouter_api_key: str = ""

    # ─── Modelos ─────────────────────────────────────────────────────────────
    # G3: ver la sección "Cumplimiento G3" del README antes de cambiar esto.
    llm_model: str = "llama-3.3-70b-versatile"

    # Por dónde se llega al modelo. `groq` es la ruta de producción y el valor por
    # defecto: si el jurado clona el repo y pone su clave de Groq, todo funciona sin
    # tocar nada.
    #
    # `openrouter` existe por una razón concreta y acotada. El nivel gratuito de Groq
    # tiene un tope de 100 000 tokens por día que se repone gota a gota (~4 167/hora,
    # medido con `scripts/cuota_groq.py`), y la evaluación del sistema completo
    # necesita ~896 000: nueve días. El Dev Tier de Groq, que resolvería esto por
    # menos de un dólar, estaba cerrado a nuevas altas el 7 de agosto de 2026.
    #
    # OpenRouter revende **la inferencia de Groq** —mismo modelo, mismo proveedor, y
    # los mismos precios de app/obs/tokens.py— y sí acepta tarjeta. Se usa fijado a
    # Groq sin sustitutos, y `app/agent/llm.py` **comprueba en la respuesta** que el
    # proveedor haya sido Groq: una cifra medida contra otro backend no describiría
    # el sistema que se entrega.
    llm_backend: str = "groq"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    stt_model: str = "whisper-large-v3-turbo"
    # Ver app/rag/embedder.py: e5-small no existe en fastembed; este es el
    # multilingüe más liviano del catálogo (384 dim, ~220 MB).
    embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # ─── Voz ─────────────────────────────────────────────────────────────────
    tts_backend: str = "edge"
    tts_voice: str = "es-CO-SalomeNeural"
    tts_rate: str = "+8%"

    # ─── Triage ──────────────────────────────────────────────────────────────
    # Apagados por defecto, y no por descuido. Medido sobre los 160 casos
    # etiquetados (`python evals/run_engine_eval.py`):
    #
    #   sin moduladores   exactitud 92.5 %   recall rojo 100 %   12 sobre-escalados
    #   con moduladores   exactitud 82.5 %   recall rojo 100 %   28 sobre-escalados
    #
    # No mejoran la sensibilidad —ya es del 100 % en rojo y amarillo— y multiplican
    # por dos las falsas alarmas. La causa es que las etiquetas del dataset no tienen
    # en cuenta la comorbilidad, así que sumar por diabetes solo desplaza verdes hacia
    # amarillo. La regla clínica que codifican es real y se conserva implementada y
    # documentada en rules.yaml; encenderla exige recalibrar el corte de rojo a 7.
    triage_moduladores: bool = False

    # ─── RAG ─────────────────────────────────────────────────────────────────
    rag_top_k: int = 4
    rag_score_min: float = 0.34
    rag_candidatos: int = 12  # top-k de cada rama antes de la fusión RRF
    rag_mmr_lambda: float = 0.7

    # ─── Escalamiento ────────────────────────────────────────────────────────
    escalation_webhook_url: str = ""

    # ─── Servidor ────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"

    # ─── Rutas (no se configuran por entorno) ────────────────────────────────
    @property
    def dir_raiz(self) -> Path:
        return RAIZ

    @property
    def dir_dataset(self) -> Path:
        return RAIZ / "dataset"

    @property
    def dir_textos(self) -> Path:
        return RAIZ / "dataset" / "textos"

    @property
    def dir_data(self) -> Path:
        return RAIZ / "data"

    @property
    def dir_chroma(self) -> Path:
        return RAIZ / "data" / "chroma"

    @property
    def dir_modelos(self) -> Path:
        """Caché del modelo de embeddings. Dentro del repo, no en %TEMP%."""
        return RAIZ / "data" / "modelos"

    @property
    def dir_alertas(self) -> Path:
        return RAIZ / "data" / "alertas"

    @property
    def dir_uploads(self) -> Path:
        return RAIZ / "data" / "uploads"

    @property
    def dir_tts_cache(self) -> Path:
        return RAIZ / "data" / "tts_cache"

    @property
    def ruta_db(self) -> Path:
        return RAIZ / "data" / "postop.db"

    @property
    def dir_logs(self) -> Path:
        return RAIZ / "logs"

    @property
    def ruta_kb_audit(self) -> Path:
        return RAIZ / "data" / "kb_audit.jsonl"

    def crear_directorios(self) -> None:
        for d in (
            self.dir_data,
            self.dir_chroma,
            self.dir_alertas,
            self.dir_uploads,
            self.dir_tts_cache,
            self.dir_modelos,
            self.dir_logs,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

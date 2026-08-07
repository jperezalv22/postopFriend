from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="postopFriend")


@app.get("/health")
def health():
    settings = get_settings()
    return {"status": "ok", "groq_configured": bool(settings.groq_api_key)}

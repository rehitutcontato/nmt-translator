"""
main.py — NMT Neural Machine Translator
Fase 2: autenticação, banco de dados, planos e monetização.
"""

import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from auth.router import router as auth_router
from billing.router import router as billing_router
from config import server_config, db_config, jwt_config, abacate_config
from config import audio_config as AUDIO_CFG
from config import translation_config as TRANSLATION_CFG
from config import server_config as SERVER_CFG
from database.connection import check_db_connection
from websocket_handler import handle_translation_session, manager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

FRONTEND_DIR = Path(__file__).parent / "frontend"
_START_TIME = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("NMT — Neural Machine Translator v2.0")
    logger.info("=" * 60)

    try:
        import ai_services  # noqa: F401
        logger.info("ai_services.py carregado — APIs reais ATIVAS")
    except ImportError as e:
        logger.warning("ai_services.py NAO carregado (%s) — modo MOCK", e)

    if not TRANSLATION_CFG.groq_api_key:
        logger.error("GROQ_API_KEY nao configurada!")
    else:
        logger.info("GROQ_API_KEY configurada")

    db_ok = await check_db_connection()
    if db_ok:
        logger.info("PostgreSQL conectado")
    else:
        logger.warning("PostgreSQL INDISPONIVEL — verifique DATABASE_URL")

    if jwt_config.is_configured:
        logger.info("JWT configurado")
    else:
        logger.error("JWT_SECRET_KEY nao configurada ou muito curta")

    logger.info("UI Web: http://localhost:%d", SERVER_CFG.port)
    logger.info("=" * 60)

    yield

    logger.info("Servidor encerrando...")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="NMT — Neural Machine Translator",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENVIRONMENT", "production") == "development" else None,
    redoc_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

origins = (
    ["*"] if os.getenv("ENVIRONMENT", "production") == "development"
    else [os.getenv("FRONTEND_URL", "https://nmt.up.railway.app")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)
app.include_router(billing_router)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' wss: ws:; "
        "img-src 'self' data:;"
    )
    return response


@app.get("/", include_in_schema=False)
async def serve_landing():
    landing = FRONTEND_DIR / "landing.html"
    if landing.exists():
        return FileResponse(str(landing))
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app")


@app.get("/app", include_in_schema=False)
async def serve_app():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=200,
        content={
            "status": "servidor_ok",
            "mensagem": "Frontend nao encontrado.",
            "websocket": "ws://localhost:8000/ws/translate",
        }
    )


@app.websocket("/ws/translate")
async def websocket_translate(websocket: WebSocket):
    await handle_translation_session(websocket)


@app.get("/health", tags=["monitoramento"])
async def health_check():
    db_ok = await check_db_connection()

    try:
        import ai_services  # noqa: F401
        ai_services_ok = True
    except ImportError:
        ai_services_ok = False

    groq_key_ok = bool(
        TRANSLATION_CFG.groq_api_key
        and TRANSLATION_CFG.groq_api_key != "SUA_CHAVE_GROQ_AQUI"
    )

    payload = {
        "status": "ok" if db_ok else "degraded",
        "service": "NMT Neural Machine Translator",
        "version": "2.0.0",
        "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
        "database": "connected" if db_ok else "unreachable",
        "sessoes_ativas": len(manager._active),
        "pipeline": {
            "modo": "LIVE" if (ai_services_ok and groq_key_ok) else "MOCK",
            "ai_services_carregado": ai_services_ok,
            "groq_api_key_configurada": groq_key_ok,
            "stt": "Groq Whisper Large v3" if ai_services_ok else "MOCK",
            "traducao": "LLaMA 3.1 8B instant via Groq" if ai_services_ok else "MOCK",
            "tts": "Microsoft Edge TTS" if ai_services_ok else "MOCK",
        },
        "config": {
            "database": db_config.is_configured,
            "jwt": jwt_config.is_configured,
            "payments": abacate_config.is_configured,
        },
    }

    if not db_ok:
        return JSONResponse(content=payload, status_code=503)
    return JSONResponse(content=payload)


@app.get("/health/sessions", tags=["monitoramento"])
async def list_sessions():
    return JSONResponse({
        "sessoes_ativas": list(manager._active.keys()),
        "total": len(manager._active),
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=SERVER_CFG.host,
        port=SERVER_CFG.port,
        log_level="info",
        workers=1,
    )
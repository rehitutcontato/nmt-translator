"""
main.py — Ponto de entrada do servidor FastAPI de tradução em tempo real.

Inicializa a aplicação, configura logging, registra rotas e expõe:
  - WebSocket /ws/translate   → pipeline de tradução (STT + Tradução + TTS)
  - GET  /                    → interface web do protótipo virtual (browser)
  - GET  /health              → monitoramento
  - GET  /health/sessions     → sessões ativas (debug)

Stack atual (Fase 2 — Protótipo Virtual):
  STT  : Groq Whisper Large v3  (via ai_services.py — Gemini Agent)
  TRAD : LLaMA-3 8B via Groq   (via ai_services.py — Gemini Agent)
  TTS  : Microsoft Edge TTS     (via ai_services.py — Gemini Agent)
  UI   : HTML/JS servido pelo FastAPI (captura mic via MediaRecorder API)

Uso:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    # ⚠️ workers=1 obrigatório: ConnectionManager é in-memory (não distribuído)
"""


import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

# Carrega .env automaticamente no ambiente local.
# Na nuvem (Railway), as variáveis vêm do painel — dotenv não é necessário.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import AUDIO_CFG, SERVER_CFG, TRANSLATION_CFG
from websocket_handler import handle_translation_session, manager

# ─────────────────────────────────────────────
#  CONFIGURAÇÃO DE LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# Diretório onde o frontend estático será servido
FRONTEND_DIR = Path(__file__).parent / "frontend"


# ─────────────────────────────────────────────
#  CICLO DE VIDA DA APLICAÇÃO
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerencia startup e shutdown do servidor.

    No startup: valida dependências críticas, loga stack ativo e
    avisa claramente se a GROQ_API_KEY não estiver configurada.
    """
    # ── STARTUP ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("🚀 Servidor de Tradução — Protótipo Virtual")
    logger.info("=" * 60)

    # Verifica ai_services (módulo do Gemini Agent)
    try:
        import ai_services  # noqa: F401
        logger.info("✅ ai_services.py carregado — APIs reais ATIVAS")
        logger.info("   STT  : Groq Whisper Large v3")
        logger.info("   TRAD : LLaMA-3 8B via Groq")
        logger.info("   TTS  : Microsoft Edge TTS + miniaudio")
    except ImportError as e:
        logger.warning("⚠️  ai_services.py NÃO carregado (%s)", e)
        logger.warning("    Pipeline rodando em modo MOCK.")
        logger.warning("    Instale: pip install groq edge-tts miniaudio")

    # Valida GROQ_API_KEY
    if not TRANSLATION_CFG.groq_api_key:
        logger.error("❌ GROQ_API_KEY não configurada!")
        logger.error("   Crie sua chave gratuita em: https://console.groq.com")
        logger.error("   Depois: export GROQ_API_KEY='gsk_...'")
    elif TRANSLATION_CFG.groq_api_key == "SUA_CHAVE_GROQ_AQUI":
        logger.error("❌ GROQ_API_KEY ainda é o valor placeholder!")
        logger.error("   Substitua em .env pela chave real da Groq.")
    else:
        logger.info("✅ GROQ_API_KEY configurada")

    logger.info("-" * 60)
    logger.info("Áudio  : %d Hz | %d-bit | chunks de %dms",
                AUDIO_CFG.sample_rate,
                AUDIO_CFG.sample_width_bytes * 8,
                AUDIO_CFG.chunk_duration_ms)
    logger.info("Idiomas: %s → %s (%s) | Voz: %s",
                TRANSLATION_CFG.source_lang,
                TRANSLATION_CFG.target_lang,
                TRANSLATION_CFG.target_lang_label,
                TRANSLATION_CFG.tts_voice)
    logger.info("UI Web : http://localhost:%d", SERVER_CFG.port)
    logger.info("=" * 60)

    yield  # aplicação rodando

    # ── SHUTDOWN ──────────────────────────────────────────────────────────
    logger.info("🛑 Servidor encerrando...")


# ─────────────────────────────────────────────
#  APLICAÇÃO FASTAPI
# ─────────────────────────────────────────────

app = FastAPI(
    title="Real-Time Translation — Protótipo Virtual",
    description=(
        "Backend assíncrono de tradução de idiomas em tempo real. "
        "Fase 2: protótipo virtual com interface web, "
        "STT via Groq Whisper Large v3, tradução via LLaMA-3, TTS via Edge TTS."
    ),
    version="0.2.0-prototipo-virtual",
    lifespan=lifespan,
)

# Serve arquivos estáticos do frontend (CSS, JS, ícones)
# O index.html é servido explicitamente pela rota "/" abaixo
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

_START_TIME = time.monotonic()


# ─────────────────────────────────────────────
#  FRONTEND WEB
# ─────────────────────────────────────────────




@app.get("/", include_in_schema=False)
async def serve_landing():
    """
    Rota raiz → Landing page de apresentação do produto.
    Visitantes chegam aqui primeiro ao acessar a URL.
    """
    landing = FRONTEND_DIR / "landing.html"
    if landing.exists():
        return FileResponse(str(landing))
    # Fallback: redireciona para o app se landing não existir
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app")


@app.get("/app", include_in_schema=False)
async def serve_app():
    """
    Rota /app → Interface do tradutor bidirecional.
    Acessada via botão CTA da landing page.
    """
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=200,
        content={
            "status": "servidor_ok",
            "mensagem": "Frontend não encontrado. Crie frontend/index.html.",
            "websocket": "ws://localhost:8000/ws/translate",
        }
    )

# ─────────────────────────────────────────────
#  ENDPOINT WEBSOCKET — TRADUÇÃO
# ─────────────────────────────────────────────

@app.websocket("/ws/translate")
async def websocket_translate(websocket: WebSocket):
    """
    Endpoint WebSocket principal para tradução em tempo real.

    O browser (frontend web) conecta aqui e envia chunks de áudio PCM
    capturados via MediaRecorder API.

    Protocolo: ver websocket_handler.py para documentação completa.
    URL: ws://localhost:8000/ws/translate
    """
    await handle_translation_session(websocket)


# ─────────────────────────────────────────────
#  ENDPOINTS REST — MONITORAMENTO
# ─────────────────────────────────────────────

@app.get("/health", tags=["monitoramento"])
async def health_check() -> JSONResponse:
    """
    Verifica se o servidor está operacional e reporta o status do pipeline.

    Retorno:
        JSON com status, uptime, sessões ativas e stack de APIs em uso.
    """
    # Detecta se ai_services está realmente disponível em runtime
    try:
        import ai_services  # noqa: F401
        ai_services_ok = True
    except ImportError:
        ai_services_ok = False

    groq_key_ok = bool(
        TRANSLATION_CFG.groq_api_key
        and TRANSLATION_CFG.groq_api_key != "SUA_CHAVE_GROQ_AQUI"
    )

    return JSONResponse({
        "status": "ok",
        "versao": "0.2.0-prototipo-virtual",
        "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
        "sessoes_ativas": len(manager._active),
        "sessoes_maximas": SERVER_CFG.max_connections,
        "pipeline": {
            "modo": "LIVE" if (ai_services_ok and groq_key_ok) else "MOCK",
            "ai_services_carregado": ai_services_ok,
            "groq_api_key_configurada": groq_key_ok,
            "stt": "Groq Whisper Large v3" if ai_services_ok else "MOCK",
            "traducao": "LLaMA-3 8B via Groq" if ai_services_ok else "MOCK",
            "tts": "Microsoft Edge TTS" if ai_services_ok else "MOCK",
        },
        "audio": {
            "sample_rate_hz": AUDIO_CFG.sample_rate,
            "bit_depth": AUDIO_CFG.sample_width_bytes * 8,
            "chunk_duration_ms": AUDIO_CFG.chunk_duration_ms,
            "chunk_size_bytes": AUDIO_CFG.chunk_size_bytes,
        },
        "idiomas": {
            "origem": TRANSLATION_CFG.source_lang,
            "destino": TRANSLATION_CFG.target_lang,
            "destino_label": TRANSLATION_CFG.target_lang_label,
            "voz_tts": TRANSLATION_CFG.tts_voice,
        },
    })


@app.get("/health/sessions", tags=["monitoramento"])
async def list_sessions() -> JSONResponse:
    """
    Lista IDs das sessões WebSocket ativas (para debugging).
    ⚠️ Remover ou proteger com autenticação em produção.
    """
    return JSONResponse({
        "sessoes_ativas": list(manager._active.keys()),
        "total": len(manager._active),
    })


# ─────────────────────────────────────────────
#  ENTRYPOINT DIRETO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=SERVER_CFG.host,
        port=SERVER_CFG.port,
        log_level="info",
        # reload=True  # habilitar apenas em desenvolvimento local
    )
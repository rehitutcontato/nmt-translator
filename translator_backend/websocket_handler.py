"""
websocket_handler.py — NMT Fase 2
Gerenciamento de conexões WebSocket com autenticação JWT e controle de cota.

Mudanças da Fase 2:
  - Auth gate: token JWT obrigatório via query param ?token=
  - Verificação de cota antes de aceitar a sessão
  - Registro de uso em usage_logs após cada tradução

Protocolo de comunicação (WebSocket):
    Cliente → Servidor : bytes brutos (PCM 16kHz 16-bit mono)
    Servidor → Cliente : bytes brutos (PCM 16kHz 16-bit mono) com áudio traduzido

    Mensagens de controle (texto JSON):
    Cliente → Servidor : {"type": "end_of_speech"}
    Cliente → Servidor : {"type": "ping"}
    Servidor → Cliente : {"type": "status", "message": "..."}
    Servidor → Cliente : {"type": "transcript", ...}
    Servidor → Cliente : {"type": "error", "message": "..."}
    Servidor → Cliente : {"type": "waiting_pair", ...}
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict

from fastapi import WebSocket, WebSocketDisconnect

from auth.dependencies import extract_ws_token
from billing.plans import check_access
from config import audio_config, server_config
from database.connection import AsyncSessionFactory
from database.crud import log_usage
from pipeline import run_pipeline, SessionLanguageTracker

logger = logging.getLogger("websocket_handler")


# ─────────────────────────────────────────────
#  GERENCIADOR DE CONEXÕES ATIVAS
# ─────────────────────────────────────────────

class ConnectionManager:
    """
    Mantém registro de todas as conexões WebSocket ativas.
    Impõe limite máximo de conexões simultâneas.
    """

    def __init__(self) -> None:
        self._active: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: str) -> bool:
        """
        Aceita e registra nova conexão WebSocket.
        Retorna False se limite atingido.
        """
        if len(self._active) >= server_config.max_connections:
            await websocket.close(code=1008, reason="Servidor lotado")
            logger.warning("Conexão recusada: limite de %d atingido.", SERVER_CFG.max_connections)
            return False

        await websocket.accept()
        self._active[session_id] = websocket
        logger.info("[%s] Nova sessão conectada. Total ativo: %d",
                    session_id, len(self._active))
        return True

    def disconnect(self, session_id: str) -> None:
        self._active.pop(session_id, None)
        logger.info("[%s] Sessão encerrada. Total ativo: %d",
                    session_id, len(self._active))

    async def send_bytes(self, session_id: str, data: bytes) -> None:
        ws = self._active.get(session_id)
        if ws:
            await ws.send_bytes(data)

    async def send_json(self, session_id: str, payload: dict) -> None:
        ws = self._active.get(session_id)
        if ws:
            await ws.send_text(json.dumps(payload))


# Instância global — compartilhada entre conexões
manager = ConnectionManager()


# ─────────────────────────────────────────────
#  BUFFER DE ÁUDIO
# ─────────────────────────────────────────────

class AudioBuffer:
    """
    Acumula chunks PCM até fim de fala (silêncio ou end_of_speech).
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._buffer: bytearray = bytearray()
        self._silence_task: asyncio.Task | None = None

    def append(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()

    def flush(self) -> bytes:
        data = bytes(self._buffer)
        self._buffer.clear()
        logger.debug("[%s] Buffer flushed: %d bytes", self.session_id, len(data))
        return data

    def is_empty(self) -> bool:
        return len(self._buffer) == 0

    def cancel_silence_timer(self) -> None:
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()


# ─────────────────────────────────────────────
#  HANDLER PRINCIPAL
# ─────────────────────────────────────────────

async def handle_translation_session(websocket: WebSocket) -> None:
    """
    Gerencia o ciclo completo de uma sessão WebSocket de tradução.

    Fluxo Fase 2:
      1. Extrai e valida token JWT do query param ?token=
      2. Verifica cota de minutos disponíveis no plano
      3. Aceita conexão e entra no loop de recepção de áudio
      4. Dispara pipeline STT→Tradução→TTS ao detectar fim de fala
      5. Registra uso em usage_logs após cada tradução
    """

    # ── 1. AUTENTICAÇÃO ──────────────────────────────────────────────────
    token = websocket.query_params.get("token", "")

    try:
        user_id, plan_id = extract_ws_token(token)
    except ValueError as e:
        await websocket.accept()
        await websocket.close(code=4001, reason=str(e))
        logger.warning("[WS] Conexão recusada — token inválido: %s", e)
        return

    # ── 2. VERIFICAR COTA ────────────────────────────────────────────────
    session_id = str(uuid.uuid4())[:8]

    async with AsyncSessionFactory() as db:
        access = await check_access(user_id, plan_id, db)

    if not access["allowed"]:
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "quota_exceeded",
            "minutes_used": access["minutes_used"],
            "minutes_limit": access["minutes_limit"],
            "upgrade_plan": access.get("upgrade_plan"),
            "upgrade_url": "/pricing",
        }))
        await websocket.close(code=4029, reason="Quota exceeded")
        logger.info("[WS] Conexão recusada — cota esgotada user=%s plan=%s", user_id, plan_id)
        return

    # ── 3. ACEITAR CONEXÃO ────────────────────────────────────────────────
    accepted = await manager.connect(websocket, session_id)
    if not accepted:
        return

    buffer = AudioBuffer(session_id)
    pipeline_lock = asyncio.Lock()
    lang_tracker = SessionLanguageTracker(session_id)

    # Informa cliente que está conectado com detalhes do plano
    await manager.send_json(session_id, {
        "type": "status",
        "message": "connected",
        "session_id": session_id,
        "plan_id": plan_id,
        "remaining_minutes": access["remaining"],
    })

    # ── Funções internas ──────────────────────────────────────────────────

    async def _dispatch_pipeline() -> None:
        """Extrai buffer e executa o pipeline de tradução."""
        if buffer.is_empty():
            return

        # AGORA COM 8 ESPAÇOS EXATOS:
        async with pipeline_lock:
            audio_data = buffer.flush()
            
            # Cálculo de duração com nomes novos e indentação rigorosa
            duration_ms = len(audio_data) / (
                audio_config.sample_rate * audio_config.channels * audio_config.sample_width
            ) * 1000
            
            logger.info("[%s] Disparando pipeline | %d bytes (%.0fms) user=%s",
                        session_id, len(audio_data), duration_ms, user_id)

            await manager.send_json(session_id, {"type": "status", "message": "processing"})

            start = time.time()

            audio_out, metadata = await run_pipeline(audio_data, session_id, lang_tracker)

            duration_minutes = (time.time() - start) / 60

            if metadata and metadata.get("type") == "waiting_pair":
                await manager.send_json(session_id, metadata)
                logger.info("[%s] Aguardando segunda pessoa | idioma: %s",
                            session_id, metadata.get("lang_detected", "?"))

            elif audio_out:
                if metadata:
                    await manager.send_json(session_id, metadata)
                await manager.send_bytes(session_id, audio_out)
                logger.info("[%s] Audio enviado: %d bytes | %s -> %s",
                            session_id, len(audio_out),
                            metadata.get("lang_from", "?"),
                            metadata.get("lang_to", "?"))

                # Registrar uso de forma não-bloqueante
                asyncio.create_task(_log_usage_safe(
                    user_id=user_id,
                    session_id=session_id,
                    minutes_used=duration_minutes,
                    lang_from=metadata.get("lang_from"),
                    lang_to=metadata.get("lang_to"),
                ))

            else:
                await manager.send_json(session_id, {
                    "type": "status",
                    "message": "no_speech_detected"
                })

    async def _silence_timeout() -> None:
        """Dispara pipeline após SILENCE_TIMEOUT_S sem novos chunks."""
        try:
            # CORREÇÃO: audio_config em vez de SERVER_CFG
            await asyncio.sleep(audio_config.silence_timeout_s)
            logger.debug("[%s] Timeout de silencio atingido.", session_id)
            await _dispatch_pipeline()
        except asyncio.CancelledError:
            pass

    # ── 4. LOOP PRINCIPAL ─────────────────────────────────────────────────
    try:
        while True:
            message = await websocket.receive()

            # Chunk de áudio PCM
            if "bytes" in message and message["bytes"] is not None:
                chunk: bytes = message["bytes"]

                if len(chunk) < 2:
                    continue

                buffer.append(chunk)
                buffer.cancel_silence_timer()
                buffer._silence_task = asyncio.create_task(_silence_timeout())

            # Mensagem de controle JSON
            elif "text" in message and message["text"] is not None:
                try:
                    ctrl = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("[%s] Mensagem JSON inválida.", session_id)
                    continue

                msg_type = ctrl.get("type", "")

                if msg_type == "end_of_speech":
                    buffer.cancel_silence_timer()
                    logger.info("[%s] end_of_speech recebido.", session_id)
                    await _dispatch_pipeline()

                elif msg_type == "ping":
                    await manager.send_json(session_id, {"type": "pong"})

                elif msg_type == "config":
                    logger.info("[%s] Config: %s -> %s",
                                session_id,
                                ctrl.get("source_lang", "?"),
                                ctrl.get("target_lang", "?"))
                    await manager.send_json(session_id, {
                        "type": "status",
                        "message": "config_received"
                    })

                else:
                    logger.warning("[%s] Tipo desconhecido: '%s'", session_id, msg_type)

    except WebSocketDisconnect:
        logger.info("[%s] Cliente desconectou normalmente.", session_id)

    except Exception as exc:
        logger.error("[%s] Erro inesperado: %s", session_id, exc, exc_info=True)

    finally:
        buffer.cancel_silence_timer()
        manager.disconnect(session_id)


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

async def _log_usage_safe(
    user_id: str,
    session_id: str,
    minutes_used: float,
    lang_from: str | None,
    lang_to: str | None,
) -> None:
    """Registra uso no banco sem bloquear o WebSocket. Falhas são logadas silenciosamente."""
    try:
        async with AsyncSessionFactory() as db:
            await log_usage(user_id, session_id, minutes_used, lang_from, lang_to, db)
            await db.commit()
    except Exception as e:
        logger.warning("[WS] Falha ao registrar uso user=%s: %s", user_id, e)

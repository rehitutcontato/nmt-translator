"""
websocket_handler.py — Gerenciamento de conexões WebSocket e buffer de áudio.

Responsável por:
- Receber chunks PCM do ESP32 via WebSocket
- Acumular chunks em buffer até detectar fim de fala (silêncio ou timeout)
- Disparar o pipeline de tradução de forma assíncrona
- Retornar áudio traduzido pelo mesmo WebSocket

Protocolo de comunicação (WebSocket):
    ESP32 → Servidor : bytes brutos (PCM 16kHz 16-bit mono)
    Servidor → ESP32 : bytes brutos (PCM 16kHz 16-bit mono) com áudio traduzido

    Mensagens de controle (texto JSON):
    ESP32 → Servidor : {"type": "config", "source_lang": "pt", "target_lang": "en"}
    ESP32 → Servidor : {"type": "end_of_speech"}  ← sinaliza fim de fala explícito
    Servidor → ESP32 : {"type": "status", "message": "..."}
    Servidor → ESP32 : {"type": "error", "message": "..."}

⚠️ ALERTA PARA CHATGPT: Protocolo de controle (mensagens JSON acima) foi
definido aqui como proposta. Confirme se está alinhado com o fluxo UX definido.

⚠️ ALERTA PARA GROK: O mecanismo de detecção de fim de fala está baseado em
timeout de silêncio (SILENCE_TIMEOUT_S). Se o ESP32 tiver VAD (Voice Activity
Detection) embutido, podemos usar o sinal "end_of_speech" ao invés do timeout,
reduzindo latência. Aguardando definição.
"""

import asyncio
import json
import logging
import uuid
from typing import Dict

from fastapi import WebSocket, WebSocketDisconnect

from config import AUDIO_CFG, SERVER_CFG
from pipeline import run_pipeline, SessionLanguageTracker

logger = logging.getLogger("websocket_handler")


# ─────────────────────────────────────────────
#  GERENCIADOR DE CONEXÕES ATIVAS
# ─────────────────────────────────────────────

class ConnectionManager:
    """
    Mantém registro de todas as conexões WebSocket ativas.

    Permite broadcast e encerramento controlado de sessões,
    além de impor o limite máximo de conexões simultâneas.
    """

    def __init__(self) -> None:
        # session_id → WebSocket
        self._active: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket) -> str | None:
        """
        Aceita nova conexão WebSocket e registra a sessão.

        Retorno:
            session_id (str) se conexão aceita, None se limite atingido.
        """
        if len(self._active) >= SERVER_CFG.max_connections:
            await websocket.close(code=1008, reason="Servidor lotado")
            logger.warning("Conexão recusada: limite de %d atingido.", SERVER_CFG.max_connections)
            return None

        await websocket.accept()
        session_id = str(uuid.uuid4())[:8]  # ID curto para logs legíveis
        self._active[session_id] = websocket
        logger.info("[%s] ✅ Nova sessão conectada. Total ativo: %d",
                    session_id, len(self._active))
        return session_id

    async def disconnect(self, session_id: str) -> None:
        """Remove sessão do registro ao desconectar."""
        self._active.pop(session_id, None)
        logger.info("[%s] 🔌 Sessão encerrada. Total ativo: %d",
                    session_id, len(self._active))

    async def send_bytes(self, session_id: str, data: bytes) -> None:
        """Envia bytes (áudio PCM) para o cliente da sessão."""
        ws = self._active.get(session_id)
        if ws:
            await ws.send_bytes(data)

    async def send_json(self, session_id: str, payload: dict) -> None:
        """Envia mensagem de controle JSON para o cliente da sessão."""
        ws = self._active.get(session_id)
        if ws:
            await ws.send_text(json.dumps(payload))


# Instância global do gerenciador — compartilhada entre conexões
manager = ConnectionManager()


# ─────────────────────────────────────────────
#  BUFFER DE ÁUDIO POR SESSÃO
# ─────────────────────────────────────────────

class AudioBuffer:
    """
    Acumula chunks de áudio PCM até atingir condição de disparo do pipeline.

    Condições de disparo (o que ocorrer primeiro):
        1. ESP32 envia mensagem "end_of_speech" (VAD no device)
        2. Timeout de silêncio: nenhum chunk recebido por SILENCE_TIMEOUT_S

    Parâmetros:
        session_id      — ID da sessão para logging.
        on_ready        — Coroutine callback chamada com os bytes acumulados.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._buffer: bytearray = bytearray()
        self._silence_task: asyncio.Task | None = None

    def append(self, chunk: bytes) -> None:
        """
        Adiciona chunk de áudio ao buffer e reinicia o timer de silêncio.

        Parâmetros:
            chunk — Bytes PCM recebidos do ESP32.
        """
        self._buffer.extend(chunk)

        # Reinicia o timer de silêncio a cada chunk recebido
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()

    def flush(self) -> bytes:
        """
        Retorna os bytes acumulados e limpa o buffer.

        Retorno:
            Bytes do áudio completo acumulado.
        """
        data = bytes(self._buffer)
        self._buffer.clear()
        logger.debug("[%s] Buffer flushed: %d bytes", self.session_id, len(data))
        return data

    def is_empty(self) -> bool:
        return len(self._buffer) == 0

    def cancel_silence_timer(self) -> None:
        """Cancela timer de silêncio pendente (ex: ao desconectar)."""
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()


# ─────────────────────────────────────────────
#  HANDLER PRINCIPAL DA SESSÃO WEBSOCKET
# ─────────────────────────────────────────────

async def handle_translation_session(websocket: WebSocket) -> None:
    """
    Gerencia o ciclo completo de uma sessão WebSocket de tradução.

    Fluxo por sessão:
        1. Aceita conexão e cria session_id único
        2. Aguarda mensagens em loop:
           - bytes  → chunk de áudio PCM, adiciona ao buffer
           - texto  → mensagem de controle JSON
        3. Ao detectar fim de fala, dispara pipeline assíncrono
        4. Retorna áudio traduzido via WebSocket
        5. Encerra sessão ao desconectar

    Parâmetros:
        websocket — Instância WebSocket do FastAPI.
    """
    session_id = await manager.connect(websocket)
    if not session_id:
        return

    buffer = AudioBuffer(session_id)
    pipeline_lock = asyncio.Lock()
    lang_tracker = SessionLanguageTracker(session_id)  # rastreia idiomas da sessão  # impede execuções paralelas por sessão

    # ── Função interna: dispara pipeline quando fala estiver completa ──────
    async def _dispatch_pipeline() -> None:
        """Extrai buffer e executa o pipeline de tradução."""
        if buffer.is_empty():
            logger.debug("[%s] Buffer vazio ao disparar pipeline, ignorando.", session_id)
            return

        async with pipeline_lock:
            audio_data = buffer.flush()
            logger.info(
                "[%s] 🎙️ Disparando pipeline | %d bytes (%.1f ms de áudio)",
                session_id,
                len(audio_data),
                len(audio_data) / (AUDIO_CFG.sample_rate *
                                   AUDIO_CFG.channels *
                                   AUDIO_CFG.sample_width_bytes) * 1000
            )

            await manager.send_json(session_id, {
                "type": "status",
                "message": "processing"
            })

            audio_out, metadata = await run_pipeline(audio_data, session_id, lang_tracker)

            if audio_out:
                # Envia metadata (textos + idiomas) para o frontend exibir
                if metadata:
                    await manager.send_json(session_id, metadata)
                # Envia áudio traduzido
                await manager.send_bytes(session_id, audio_out)
                logger.info(
                    "[%s] 🔊 Áudio enviado: %d bytes | %s → %s",
                    session_id, len(audio_out),
                    metadata.get("lang_from", "?"),
                    metadata.get("lang_to", "?"),
                )
            else:
                await manager.send_json(session_id, {
                    "type": "status",
                    "message": "no_speech_detected"
                })

    # ── Função interna: timer de silêncio ──────────────────────────────────
    async def _silence_timeout() -> None:
        """
        Aguarda SILENCE_TIMEOUT_S e dispara o pipeline se não chegarem
        novos chunks nesse intervalo. Equivale à detecção de fim de fala
        por ausência de sinal.
        """
        try:
            await asyncio.sleep(SERVER_CFG.silence_timeout_s)
            logger.debug("[%s] ⏱️ Timeout de silêncio atingido.", session_id)
            await _dispatch_pipeline()
        except asyncio.CancelledError:
            pass  # Normal: novo chunk chegou antes do timeout

    # ── Loop principal de recepção de mensagens ────────────────────────────
    try:
        while True:
            message = await websocket.receive()

            # ── Mensagem de bytes: chunk de áudio PCM ─────────────────────
            if "bytes" in message and message["bytes"] is not None:
                chunk: bytes = message["bytes"]

                # Valida tamanho mínimo do chunk
                if len(chunk) < 2:
                    logger.debug("[%s] Chunk muito pequeno ignorado (%d bytes).",
                                 session_id, len(chunk))
                    continue

                buffer.append(chunk)

                # Reinicia timer de silêncio com cada chunk recebido
                buffer.cancel_silence_timer()
                buffer._silence_task = asyncio.create_task(_silence_timeout())

            # ── Mensagem de texto: controle JSON ──────────────────────────
            elif "text" in message and message["text"] is not None:
                try:
                    ctrl = json.loads(message["text"])
                except json.JSONDecodeError:
                    logger.warning("[%s] Mensagem de texto inválida (não JSON): %s",
                                   session_id, message["text"][:100])
                    continue

                msg_type = ctrl.get("type", "")

                if msg_type == "end_of_speech":
                    # ESP32 detectou fim de fala via VAD local
                    buffer.cancel_silence_timer()
                    logger.info("[%s] 📍 end_of_speech recebido do ESP32.", session_id)
                    await _dispatch_pipeline()

                elif msg_type == "config":
                    # ⚠️ ALERTA PARA CHATGPT: Estrutura de configuração por sessão.
                    # Idiomas podem ser alterados dinamicamente por sessão.
                    # A integração com TranslationConfig por sessão não está
                    # implementada no MVP — será necessária se múltiplos usuários
                    # com idiomas diferentes usarem simultaneamente.
                    logger.info(
                        "[%s] Config recebida: %s → %s",
                        session_id,
                        ctrl.get("source_lang", "?"),
                        ctrl.get("target_lang", "?")
                    )
                    await manager.send_json(session_id, {
                        "type": "status",
                        "message": "config_received"
                    })

                elif msg_type == "ping":
                    await manager.send_json(session_id, {"type": "pong"})

                else:
                    logger.warning("[%s] Tipo de mensagem desconhecido: '%s'",
                                   session_id, msg_type)

    except WebSocketDisconnect:
        logger.info("[%s] Cliente desconectou normalmente.", session_id)

    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] ❌ Erro inesperado na sessão: %s", session_id, exc, exc_info=True)

    finally:
        buffer.cancel_silence_timer()
        await manager.disconnect(session_id)
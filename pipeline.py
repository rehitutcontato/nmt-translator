"""
pipeline.py — Pipeline assíncrono: STT → Tradução → TTS.

Cada etapa é uma função async independente, permitindo substituição
dos mocks pelas implementações reais sem alterar o fluxo principal.

Fluxo:
    bytes (PCM) ──► transcribe() ──► translate() ──► synthesize() ──► bytes (PCM)

Fase 2 — Protótipo Virtual:
    STT  : Groq Whisper Large v3  (via ai_services.py — Gemini Agent)
    TRAD : LLaMA-3 8B via Groq    (via ai_services.py — Gemini Agent)
    TTS  : Microsoft Edge TTS     (via ai_services.py — Gemini Agent)
"""

import asyncio
import logging
import time
from typing import Optional

from config import AUDIO_CFG, TRANSLATION_CFG

# ── Serviços reais entregues pelo Gemini Agent ─────────────────────────────
# ai_services.py contém: transcribe_audio(), translate_text(), generate_speech()
# Importação com fallback gracioso: se o módulo não existir ou falhar no import
# (ex: dependências não instaladas), o pipeline cai para modo mock automaticamente
# e loga um aviso claro — sem derrubar o servidor.
try:
    from ai_services import transcribe_audio, translate_text, generate_speech
    _AI_SERVICES_AVAILABLE = True
except ImportError as _import_err:
    _AI_SERVICES_AVAILABLE = False
    logging.getLogger("pipeline").warning(
        "⚠️  ai_services.py não disponível (%s). "
        "Pipeline rodando em modo MOCK. "
        "Instale as dependências: pip install groq edge-tts miniaudio",
        _import_err,
    )

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────
#  ETAPA 1 — STT (Speech-to-Text)
# ─────────────────────────────────────────────

async def transcribe(audio_pcm: bytes, session_id: str) -> Optional[str]:
    """
    Converte áudio PCM bruto em texto transcrito via Groq Whisper Large v3.

    Delega para ai_services.transcribe_audio() (implementado pelo Gemini Agent),
    que internamente converte PCM → WAV em memória e envia à API da Groq.

    Parâmetros:
        audio_pcm   — Bytes de áudio PCM 16kHz, 16-bit, mono acumulados
                      durante a janela de fala detectada pelo AudioBuffer.
        session_id  — Identificador único da sessão WebSocket,
                      usado para logging e rastreamento de latência.

    Retorno:
        Texto transcrito (str) ou None se áudio vazio, silêncio ou falha na API.

    Modelo: whisper-large-v3 via Groq (latência esperada: ~300–600ms)
    Fallback: mock estático se ai_services não estiver disponível.
    """
    duracao_ms = (
        len(audio_pcm)
        / (AUDIO_CFG.sample_rate * AUDIO_CFG.channels * AUDIO_CFG.sample_width_bytes)
        * 1000
    )
    logger.debug(
        "[%s] STT | %d bytes (%.0f ms de áudio) → Groq Whisper Large v3",
        session_id, len(audio_pcm), duracao_ms,
    )

    # Guarda de segurança: não envia áudio muito curto (< 300ms) para a API —
    # Whisper retorna lixo ou erro com menos de ~0.3s de áudio.
    min_bytes = int(AUDIO_CFG.sample_rate * AUDIO_CFG.channels
                    * AUDIO_CFG.sample_width_bytes * 0.3)
    if len(audio_pcm) < min_bytes:
        logger.info(
            "[%s] STT ignorado: áudio muito curto (%.0fms < 300ms mínimo).",
            session_id, duracao_ms,
        )
        return None

    # ── Chamada real ao serviço do Gemini Agent ────────────────────────────
    if _AI_SERVICES_AVAILABLE:
        # transcribe_audio() já trata exceções internamente e retorna "" em erro
        resultado = await transcribe_audio(audio_pcm)
        # Normaliza: string vazia → None (padrão do pipeline)
        return resultado.strip() if resultado and resultado.strip() else None

    # ── Fallback mock (ai_services indisponível) ───────────────────────────
    logger.warning("[%s] STT em modo MOCK — ai_services não carregado.", session_id)
    await asyncio.sleep(0)
    return "Olá, como você está?"


# ─────────────────────────────────────────────
#  ETAPA 2 — TRADUÇÃO
# ─────────────────────────────────────────────

async def translate(text: str, session_id: str) -> Optional[str]:
    """
    Traduz texto do idioma de origem para o idioma de destino via LLaMA-3 (Groq).

    Delega para ai_services.translate_text() (implementado pelo Gemini Agent),
    que usa o modelo llama3-8b-8192 na Groq com prompt de tradutor simultâneo.

    Parâmetros:
        text        — Texto transcrito pela etapa STT.
        session_id  — Identificador único da sessão WebSocket.

    Retorno:
        Texto traduzido (str) ou None em caso de falha da API.

    Modelo: llama3-8b-8192 via Groq (latência esperada: ~200–400ms)
    Idioma destino: configurado em TRANSLATION_CFG.target_lang_label (ex: "Inglês")
    Fallback: mock estático se ai_services não estiver disponível.
    """
    logger.debug(
        "[%s] Tradução | '%s' (%s → %s) via LLaMA-3",
        session_id, text,
        TRANSLATION_CFG.source_lang,
        TRANSLATION_CFG.target_lang,
    )

    # ── Chamada real ao serviço do Gemini Agent ────────────────────────────
    if _AI_SERVICES_AVAILABLE:
        resultado = await translate_text(text, target_lang=TRANSLATION_CFG.target_lang_label)
        return resultado.strip() if resultado and resultado.strip() else None

    # ── Fallback mock ──────────────────────────────────────────────────────
    logger.warning("[%s] Tradução em modo MOCK — ai_services não carregado.", session_id)
    await asyncio.sleep(0)
    return "Hello, how are you?"


# ─────────────────────────────────────────────
#  ETAPA 3 — TTS (Text-to-Speech)
# ─────────────────────────────────────────────

async def synthesize(text: str, session_id: str) -> Optional[bytes]:
    """
    Converte texto traduzido em áudio PCM via Edge TTS (Microsoft), sem FFmpeg.

    Delega para ai_services.generate_speech() (implementado pelo Gemini Agent),
    que usa edge-tts para gerar MP3 e miniaudio para decodificar para PCM 16kHz
    diretamente em memória — sem dependências de sistema externas.

    Parâmetros:
        text        — Texto traduzido pela etapa anterior.
        session_id  — Identificador único da sessão WebSocket.

    Retorno:
        Bytes de áudio PCM 16kHz, 16-bit, mono prontos para envio ao browser,
        ou None em caso de falha.

    Voz: configurada em TRANSLATION_CFG.tts_voice (ex: "en-US-ChristopherNeural")
    Latência esperada: ~400–800ms (geração Edge TTS + decodificação miniaudio)
    Fallback: silêncio PCM de 1s se ai_services não estiver disponível.
    """
    logger.debug(
        "[%s] TTS | '%s' → voz: %s",
        session_id, text[:60], TRANSLATION_CFG.tts_voice,
    )

    # ── Chamada real ao serviço do Gemini Agent ────────────────────────────
    if _AI_SERVICES_AVAILABLE:
        resultado = await generate_speech(text, voice=TRANSLATION_CFG.tts_voice)
        # generate_speech() retorna b"" em erro — normaliza para None
        return resultado if resultado else None

    # ── Fallback mock: silêncio PCM de 1 segundo ──────────────────────────
    logger.warning("[%s] TTS em modo MOCK — ai_services não carregado.", session_id)
    await asyncio.sleep(0)
    silence_samples = AUDIO_CFG.sample_rate * AUDIO_CFG.channels
    return b'\x00\x00' * silence_samples


# ─────────────────────────────────────────────
#  ORQUESTRADOR DO PIPELINE
# ─────────────────────────────────────────────

async def run_pipeline(audio_pcm: bytes, session_id: str) -> Optional[bytes]:
    """
    Orquestra as três etapas do pipeline de tradução em sequência.

    Mede e loga o tempo total de cada etapa para monitoramento
    do orçamento de latência de 3 segundos.

    Parâmetros:
        audio_pcm   — Chunk(s) de áudio PCM acumulados de uma fala completa.
        session_id  — Identificador único da sessão WebSocket.

    Retorno:
        Bytes de áudio traduzido e sintetizado, ou None se qualquer
        etapa falhar (falhas são logadas mas não propagam exceção,
        para manter a conexão WebSocket ativa).
    """
    t_start = time.monotonic()

    try:
        # ── Etapa 1: STT ──────────────────────────────────────────────────
        t0 = time.monotonic()
        transcript = await transcribe(audio_pcm, session_id)
        t_stt = time.monotonic() - t0

        if not transcript:
            logger.info("[%s] STT retornou vazio — possível silêncio.", session_id)
            return None

        logger.info("[%s] STT (%.0fms): '%s'", session_id, t_stt * 1000, transcript)

        # ── Etapa 2: Tradução ─────────────────────────────────────────────
        t0 = time.monotonic()
        translated = await translate(transcript, session_id)
        t_translate = time.monotonic() - t0

        if not translated:
            logger.warning("[%s] Tradução retornou vazio.", session_id)
            return None

        logger.info(
            "[%s] Tradução (%.0fms): '%s'", session_id, t_translate * 1000, translated
        )

        # ── Etapa 3: TTS ──────────────────────────────────────────────────
        t0 = time.monotonic()
        audio_out = await synthesize(translated, session_id)
        t_tts = time.monotonic() - t0

        if audio_out is None:
            logger.warning("[%s] TTS retornou vazio.", session_id)
            return None

        # ── Relatório de latência ─────────────────────────────────────────
        t_total = time.monotonic() - t_start
        logger.info(
            "[%s] ✅ Pipeline completo | Total: %.0fms "
            "(STT: %.0fms | Tradução: %.0fms | TTS: %.0fms) | Saída: %d bytes",
            session_id,
            t_total * 1000,
            t_stt * 1000,
            t_translate * 1000,
            t_tts * 1000,
            len(audio_out),
        )

        if t_total > 3.0:
            logger.warning(
                "[%s] ⚠️ LATÊNCIA EXCEDIDA: %.1fs > 3.0s", session_id, t_total
            )

        return audio_out

    except Exception as exc:  # noqa: BLE001
        # Captura genérica intencional: não derruba a conexão WebSocket
        # por falha pontual de pipeline.
        logger.error("[%s] ❌ Erro no pipeline: %s", session_id, exc, exc_info=True)
        return None

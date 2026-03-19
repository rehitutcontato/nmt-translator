"""
pipeline.py — Pipeline assíncrono: STT → Detecção de idioma → Tradução → TTS.

Fase 3 — Tradução bidirecional automática:
    - Whisper detecta o idioma de cada fala automaticamente
    - Sistema memoriza os 2 idiomas da sessão
    - Cada fala é traduzida para "o outro idioma" da conversa
    - Voz TTS selecionada automaticamente pelo idioma destino

Fluxo:
    PCM → transcribe() → (texto, idioma_origem)
                              ↓
                    SessionLanguageTracker
                    detecta idioma_destino
                              ↓
                    translate(texto, destino)
                              ↓
                    synthesize(texto_traduzido, voz_destino)
                              ↓
                           PCM out
"""

import asyncio
import logging
import time
from typing import Optional

from config import AUDIO_CFG

try:
    from ai_services import (
        transcribe_audio,
        translate_text,
        generate_speech,
        VOICE_MAP,
        LANG_LABEL_MAP,
    )
    _AI_SERVICES_AVAILABLE = True
except ImportError as _import_err:
    _AI_SERVICES_AVAILABLE = False
    logging.getLogger("pipeline").warning(
        "⚠️  ai_services.py não disponível (%s). Modo MOCK ativo.", _import_err
    )

logger = logging.getLogger("pipeline")


# ─────────────────────────────────────────────
#  RASTREADOR DE IDIOMAS POR SESSÃO
# ─────────────────────────────────────────────

class SessionLanguageTracker:
    """
    Memoriza os idiomas detectados na sessão e resolve qual é o
    idioma destino de cada nova fala.

    Lógica de alternância automática:
        - Primeira fala: idioma detectado = origem, destino = "en" (fallback)
        - Segunda fala diferente: registra o segundo idioma
        - Falas seguintes: sempre traduz para o idioma oposto ao detectado

    Exemplo:
        Fala 1: detecta "pt" → traduz para "en" (fallback, destino ainda desconhecido)
        Fala 2: detecta "de" → registra par (pt ↔ de), traduz para "pt"
        Fala 3: detecta "pt" → traduz para "de"
        Fala 4: detecta "de" → traduz para "pt"
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        # Par de idiomas estabelecido na sessão: {lang_a, lang_b}
        self._lang_pair: set[str] = set()
        # Último idioma detectado
        self._last_lang: Optional[str] = None

    def resolve_target(self, detected_lang: str) -> str:
        """
        Dado o idioma detectado, retorna o idioma destino da tradução.

        Parâmetros:
            detected_lang — Código ISO do idioma detectado pelo Whisper (ex: "pt")

        Retorno:
            Código ISO do idioma destino (ex: "de")
        """
        # Adiciona o idioma detectado ao par da sessão
        self._lang_pair.add(detected_lang)
        self._last_lang = detected_lang

        if len(self._lang_pair) >= 2:
            # Par estabelecido: retorna o outro idioma
            other = self._lang_pair - {detected_lang}
            target = other.pop()
            logger.info(
                "[%s] 🔄 Par estabelecido: %s ↔ %s | Esta fala: %s → %s",
                self.session_id,
                *sorted(self._lang_pair),
                detected_lang,
                target,
            )
            return target


    def get_voice_for(self, lang: str) -> str:
        """Retorna a voz Edge TTS mais adequada para o idioma."""
        return VOICE_MAP.get(lang, "en-US-ChristopherNeural") if _AI_SERVICES_AVAILABLE else "en-US-ChristopherNeural"

    def get_label_for(self, lang: str) -> str:
        """Retorna o nome por extenso do idioma para o prompt de tradução."""
        return LANG_LABEL_MAP.get(lang, lang.capitalize()) if _AI_SERVICES_AVAILABLE else lang

    @property
    def lang_pair(self) -> set:
        return self._lang_pair.copy()


# ─────────────────────────────────────────────
#  ETAPA 1 — STT COM DETECÇÃO DE IDIOMA
# ─────────────────────────────────────────────

async def transcribe(audio_pcm: bytes, session_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Transcreve áudio e detecta idioma automaticamente.

    Retorno:
        Tupla (texto, idioma_iso) ou (None, None) em caso de falha/silêncio.
    """
    duracao_ms = (
        len(audio_pcm)
        / (AUDIO_CFG.sample_rate * AUDIO_CFG.channels * AUDIO_CFG.sample_width_bytes)
        * 1000
    )

    min_bytes = int(AUDIO_CFG.sample_rate * AUDIO_CFG.channels
                    * AUDIO_CFG.sample_width_bytes * 0.3)
    if len(audio_pcm) < min_bytes:
        logger.info("[%s] STT ignorado: áudio muito curto (%.0fms).", session_id, duracao_ms)
        return None, None

    if _AI_SERVICES_AVAILABLE:
        texto, idioma = await transcribe_audio(audio_pcm)
        if not texto or not texto.strip():
            return None, None
        return texto.strip(), idioma

    # Mock
    return "Olá, como você está?", "pt"


# ─────────────────────────────────────────────
#  ETAPA 2 — TRADUÇÃO COM IDIOMA DINÂMICO
# ─────────────────────────────────────────────

async def translate(text: str, target_lang_label: str, session_id: str) -> Optional[str]:
    """
    Traduz texto para o idioma destino resolvido pelo SessionLanguageTracker.

    Parâmetros:
        text             — Texto transcrito
        target_lang_label — Nome do idioma destino (ex: "German")
        session_id       — ID da sessão para logging
    """
    if _AI_SERVICES_AVAILABLE:
        resultado = await translate_text(text, target_lang=target_lang_label)
        return resultado.strip() if resultado and resultado.strip() else None

    return "Hello, how are you?"


# ─────────────────────────────────────────────
#  ETAPA 3 — TTS COM VOZ AUTOMÁTICA
# ─────────────────────────────────────────────

async def synthesize(text: str, voice: str, session_id: str) -> Optional[bytes]:
    """
    Gera áudio com a voz correta para o idioma destino.

    Parâmetros:
        text      — Texto traduzido
        voice     — Voz Edge TTS selecionada pelo tracker (ex: "de-DE-ConradNeural")
        session_id — ID da sessão para logging
    """
    if _AI_SERVICES_AVAILABLE:
        resultado = await generate_speech(text, voice=voice)
        return resultado if resultado else None

    silence_samples = AUDIO_CFG.sample_rate * AUDIO_CFG.channels
    return b'\x00\x00' * silence_samples


# ─────────────────────────────────────────────
#  ORQUESTRADOR DO PIPELINE
# ─────────────────────────────────────────────

async def run_pipeline(
    audio_pcm: bytes,
    session_id: str,
    lang_tracker: SessionLanguageTracker,
) -> tuple[Optional[bytes], Optional[dict]]:
    """
    Orquestra STT → detecção de idioma → tradução → TTS.

    Parâmetros:
        audio_pcm    — Bytes PCM da fala completa
        session_id   — ID da sessão WebSocket
        lang_tracker — Instância do rastreador de idiomas da sessão

    Retorno:
        Tupla (audio_bytes, metadata) onde metadata contém os textos
        e idiomas para exibir no frontend, ou (None, None) em caso de falha.
    """
    t_start = time.monotonic()

    try:
        # ── Etapa 1: STT + detecção de idioma ─────────────────────────────
        t0 = time.monotonic()
        transcript, lang_origem = await transcribe(audio_pcm, session_id)
        t_stt = time.monotonic() - t0

        if not transcript or not lang_origem:
            logger.info("[%s] STT vazio — silêncio ou áudio inválido.", session_id)
            return None, None

        logger.info(
            "[%s] STT (%.0fms): '%s' [%s]",
            session_id, t_stt * 1000, transcript, lang_origem
        )
        # ── Resolução automática do idioma destino ─────────────────────────
        lang_destino = lang_tracker.resolve_target(lang_origem)

        # Par incompleto: 1ª pessoa falou, 2ª ainda não.
        # Não traduz — registra idioma e avisa o frontend para aguardar.
        if lang_destino is None:
            logger.info(
                "[%s] ⏳ Idioma '%s' registrado. Aguardando segunda pessoa falar.",
                session_id, lang_origem
            )
            return None, {
                "type": "waiting_pair",
                "lang_detected": lang_origem,
                "message": "Idioma detectado. Aguardando a outra pessoa falar.",
            }

        lang_destino_label = lang_tracker.get_label_for(lang_destino)
        voz_destino = lang_tracker.get_voice_for(lang_destino)
        # ── Etapa 2: Tradução ──────────────────────────────────────────────
        t0 = time.monotonic()
        translated = await translate(transcript, lang_destino_label, session_id)
        t_translate = time.monotonic() - t0

        if not translated:
            logger.warning("[%s] Tradução retornou vazio.", session_id)
            return None, None

        logger.info(
            "[%s] Tradução (%.0fms): [%s→%s] '%s'",
            session_id, t_translate * 1000,
            lang_origem, lang_destino, translated
        )

        # ── Etapa 3: TTS ───────────────────────────────────────────────────
        t0 = time.monotonic()
        audio_out = await synthesize(translated, voz_destino, session_id)
        t_tts = time.monotonic() - t0

        if audio_out is None:
            logger.warning("[%s] TTS retornou vazio.", session_id)
            return None, None

        # ── Relatório de latência ──────────────────────────────────────────
        t_total = time.monotonic() - t_start
        logger.info(
            "[%s] ✅ Pipeline | %.0fms total (STT:%.0f Trad:%.0f TTS:%.0f) | %d bytes",
            session_id, t_total * 1000,
            t_stt * 1000, t_translate * 1000, t_tts * 1000,
            len(audio_out),
        )

        if t_total > 3.0:
            logger.warning("[%s] ⚠️ LATÊNCIA EXCEDIDA: %.1fs", session_id, t_total)

        # Metadata enviada ao frontend para exibir na interface
        metadata = {
            "type": "transcript",
            "original": transcript,
            "translated": translated,
            "lang_from": lang_origem,
            "lang_to": lang_destino,
            "lang_pair": list(lang_tracker.lang_pair),
        }

        return audio_out, metadata

    except Exception as exc:
        logger.error("[%s] ❌ Erro no pipeline: %s", session_id, exc, exc_info=True)
        return None, None
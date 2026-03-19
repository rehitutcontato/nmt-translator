"""
config.py — Configurações centrais do servidor de tradução.

Centraliza todas as constantes e variáveis de ambiente para facilitar
ajustes sem tocar na lógica de negócio.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AudioConfig:
    """Parâmetros do stream de áudio PCM definidos pelo Gemini."""
    sample_rate: int = 16_000          # Hz — definido pelo Gemini
    channels: int = 1                  # Mono (ESP32 tem 1 microfone)
    sample_width_bytes: int = 2        # 16-bit PCM → 2 bytes por amostra
    # ⚠️ ALERTA PARA GEMINI: chunk_size_ms não foi especificado.
    # Usando 250ms como padrão. Valor impacta diretamente a latência da STT.
    chunk_duration_ms: int = int(os.getenv("CHUNK_DURATION_MS", "250"))

    @property
    def chunk_size_bytes(self) -> int:
        """Tamanho em bytes de cada chunk de áudio recebido."""
        return int(self.sample_rate * self.channels *
                   self.sample_width_bytes * self.chunk_duration_ms / 1000)


@dataclass(frozen=True)
class ServerConfig:
    """Parâmetros do servidor FastAPI/WebSocket."""
    host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    port: int = int(os.getenv("SERVER_PORT", "8000"))
    # Tempo máximo (s) aguardando chunk antes de considerar silêncio
    silence_timeout_s: float = float(os.getenv("SILENCE_TIMEOUT_S", "1.5"))
    # Latência total máxima aceitável (s) — requisito do projeto
    max_latency_s: float = 3.0
    # Máximo de conexões simultâneas (múltiplos dispositivos ESP32)
    max_connections: int = int(os.getenv("MAX_CONNECTIONS", "10"))


@dataclass(frozen=True)
class TranslationConfig:
    """Configuração de idiomas e APIs de tradução."""
    # ⚠️ ALERTA PARA CHATGPT: idiomas source/target devem ser
    # negociados via handshake no início da sessão WebSocket?
    # Por ora, usando variáveis de ambiente como fallback.
    source_lang: str = os.getenv("SOURCE_LANG", "pt")
    # target_lang_label: nome por extenso que o LLaMA-3 entende no prompt
    target_lang: str = os.getenv("TARGET_LANG", "en")
    target_lang_label: str = os.getenv("TARGET_LANG_LABEL", "Inglês")

    # Voz Edge TTS para o idioma de destino
    # Lista completa: https://speech.microsoft.com/portal/voicegallery
    tts_voice: str = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")

    # ── Chaves de API — NUNCA hardcoded, sempre via env vars ──────────────
    # Groq: STT (Whisper Large v3) + Tradução (LLaMA-3)
    # Obtenha gratuitamente em: https://console.groq.com
    groq_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GROQ_API_KEY")
    )

    # Legado — mantidos para compatibilidade com fases futuras
    gemini_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY")
    )
    google_translate_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GOOGLE_TRANSLATE_API_KEY")
    )
    google_tts_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GOOGLE_TTS_API_KEY")
    )


# Instâncias globais — importadas pelos demais módulos
AUDIO_CFG = AudioConfig()
SERVER_CFG = ServerConfig()
TRANSLATION_CFG = TranslationConfig()

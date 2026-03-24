"""
NMT — config.py atualizado para Fase 2
Lê todas as variáveis de ambiente e expõe como dataclasses tipadas.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────
# FASE 1 — mantidas sem alteração
# ──────────────────────────────────────────────

@dataclass
class AudioConfig:
    chunk_duration_ms: int = int(os.getenv("CHUNK_DURATION_MS", "250"))
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2  # 16-bit = 2 bytes
    silence_timeout_s: float = float(os.getenv("SILENCE_TIMEOUT_S", "1.5"))
    min_audio_ms: int = 300  # mínimo para chamar Whisper

    @property
    def chunk_size_bytes(self) -> int:
        return int(self.sample_rate * self.channels * self.sample_width * self.chunk_duration_ms / 1000)

    @property
    def min_audio_bytes(self) -> int:
        return int(self.sample_rate * self.channels * self.sample_width * self.min_audio_ms / 1000)


@dataclass
class ServerConfig:
    host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    max_connections: int = int(os.getenv("MAX_CONNECTIONS", "10"))
    environment: str = os.getenv("ENVIRONMENT", "production")

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@dataclass
class TranslationConfig:
    source_lang: str = os.getenv("SOURCE_LANG", "pt")
    target_lang: str = os.getenv("TARGET_LANG", "en")
    target_lang_label: str = os.getenv("TARGET_LANG_LABEL", "English")
    tts_voice: str = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")


# ──────────────────────────────────────────────
# FASE 2 — novas configurações
# ──────────────────────────────────────────────

@dataclass
class DatabaseConfig:
    url: str = field(default_factory=lambda: _normalize_db_url(os.getenv("DATABASE_URL", "")))

    @property
    def is_configured(self) -> bool:
        return bool(self.url)


def _normalize_db_url(url: str) -> str:
    """Railway injeta postgresql:// mas asyncpg precisa de postgresql+asyncpg://"""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@dataclass
class JWTConfig:
    secret_key: str = os.getenv("JWT_SECRET_KEY", "")
    algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

    @property
    def is_configured(self) -> bool:
        return len(self.secret_key) >= 32


@dataclass
class AbacatePayConfig:
    api_key: str = os.getenv("ABACATEPAY_API_KEY", "")
    webhook_secret: str = os.getenv("ABACATEPAY_WEBHOOK_SECRET", "")
    base_url: str = os.getenv("ABACATEPAY_BASE_URL", "https://api.abacatepay.com/v1")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class EmailConfig:
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    from_address: str = os.getenv("EMAIL_FROM", "nmt@nmt.ai")
    frontend_url: str = os.getenv("FRONTEND_URL", "https://nmt.up.railway.app")

    @property
    def is_configured(self) -> bool:
        return bool(self.resend_api_key)


# ──────────────────────────────────────────────
# INSTÂNCIAS GLOBAIS
# ──────────────────────────────────────────────

audio_config = AudioConfig()
server_config = ServerConfig()
translation_config = TranslationConfig()
db_config = DatabaseConfig()
jwt_config = JWTConfig()
abacate_config = AbacatePayConfig()
email_config = EmailConfig()

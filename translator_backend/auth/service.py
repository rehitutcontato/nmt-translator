"""
NMT — Fase 2
auth/service.py

Responsabilidades:
  - Hash e verificação de senhas (bcrypt)
  - Criação de access tokens e refresh tokens (JWT)
  - Verificação e decodificação de tokens
  - Tokens de verificação de email (UUID simples)

NUNCA importar modelos de banco aqui — sem acoplamento com SQLAlchemy.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from config import jwt_config

# ──────────────────────────────────────────────
# CONFIGURAÇÃO
# ──────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

SECRET_KEY = jwt_config.secret_key
ALGORITHM = jwt_config.algorithm
ACCESS_EXPIRE_MIN = jwt_config.access_token_expire_minutes
REFRESH_EXPIRE_DAYS = jwt_config.refresh_token_expire_days


# ──────────────────────────────────────────────
# SENHA
# ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Retorna hash bcrypt da senha. rounds=12 por configuração."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Compara senha em texto claro com hash bcrypt."""
    return pwd_context.verify(plain, hashed)


# ──────────────────────────────────────────────
# ACCESS TOKEN
# ──────────────────────────────────────────────

def create_access_token(user_id: str, plan_id: str) -> str:
    """
    Cria JWT de acesso com validade de ACCESS_EXPIRE_MIN minutos.

    Payload:
      sub   → user_id
      plan  → plan_id (evita round-trip ao banco no WebSocket)
      type  → 'access'
      exp   → timestamp de expiração
    """
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "plan": plan_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_EXPIRE_MIN),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_access_token(token: str) -> Optional[dict]:
    """
    Decodifica e valida token de acesso.
    Retorna payload dict ou None se inválido/expirado.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        if not payload.get("sub"):
            return None
        return payload
    except JWTError:
        return None


# ──────────────────────────────────────────────
# REFRESH TOKEN
# ──────────────────────────────────────────────

def create_refresh_token(user_id: str) -> str:
    """
    Cria JWT de refresh com validade de REFRESH_EXPIRE_DAYS dias.
    Payload mínimo — não carrega plan_id (pode ter mudado).
    """
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),  # JWT ID único — base para revogação futura
        "iat": now,
        "exp": now + timedelta(days=REFRESH_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_refresh_token(token: str) -> Optional[dict]:
    """
    Decodifica e valida refresh token.
    Retorna payload ou None se inválido/expirado.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        if not payload.get("sub"):
            return None
        return payload
    except JWTError:
        return None


# ──────────────────────────────────────────────
# EMAIL VERIFICATION TOKEN
# ──────────────────────────────────────────────

def create_email_verification_token(user_id: str) -> str:
    """
    Token simples para verificação de email.
    JWT com 24h de validade — enviado no link de confirmação.
    """
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "type": "email_verify",
        "exp": now + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_email_token(token: str) -> Optional[str]:
    """Retorna user_id se token válido, None caso contrário."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "email_verify":
            return None
        return payload.get("sub")
    except JWTError:
        return None


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def token_expires_in_seconds() -> int:
    """Retorna quantos segundos até o access token expirar."""
    return ACCESS_EXPIRE_MIN * 60

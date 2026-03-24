"""
NMT — Fase 2
Pydantic schemas para validação de request/response.

Separados dos modelos SQLAlchemy por design — nunca expõe o ORM diretamente.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


# ──────────────────────────────────────────────
# USER
# ──────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter pelo menos 8 caracteres")
        if not any(c.isdigit() for c in v):
            raise ValueError("Senha deve conter pelo menos 1 número")
        if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v):
            raise ValueError("Senha deve conter pelo menos 1 caractere especial")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    id: str
    email: str
    name: Optional[str]
    email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserWithPlan(UserPublic):
    """Resposta de /auth/me — inclui plano atual."""
    plan_id: str
    plan_name: str
    minutes_used_this_month: float
    minutes_limit: int  # -1 = ilimitado


# ──────────────────────────────────────────────
# AUTH TOKENS
# ──────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos


class RefreshRequest(BaseModel):
    refresh_token: str


# ──────────────────────────────────────────────
# PLAN
# ──────────────────────────────────────────────

class PlanPublic(BaseModel):
    id: str
    name: str
    price_brl: float
    minutes_month: int
    max_languages: int
    interpreter_mode: bool
    priority_queue: bool
    api_access: bool
    history_days: int

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────────
# SUBSCRIPTION
# ──────────────────────────────────────────────

class SubscriptionPublic(BaseModel):
    id: str
    plan_id: str
    status: str
    started_at: datetime
    expires_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────────
# BILLING / CHECKOUT
# ──────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str
    payment_method: str = "pix"  # pix | card


class CheckoutResponse(BaseModel):
    checkout_id: str
    pix_qr_code: Optional[str]
    pix_qr_code_image: Optional[str]  # base64
    expires_at: datetime
    amount_brl: float


class BillingStatusResponse(BaseModel):
    paid: bool
    plan_id: Optional[str]
    status: str  # pending | paid | expired | failed


# ──────────────────────────────────────────────
# USAGE
# ──────────────────────────────────────────────

class UsageResponse(BaseModel):
    plan_id: str
    minutes_limit: int  # -1 = ilimitado
    minutes_used: float
    minutes_remaining: float  # -1 = ilimitado
    reset_date: datetime  # primeiro dia do próximo mês


# ──────────────────────────────────────────────
# DASHBOARD
# ──────────────────────────────────────────────

class DashboardData(BaseModel):
    user: UserPublic
    subscription: SubscriptionPublic
    plan: PlanPublic
    usage: UsageResponse
    recent_sessions: list[dict]  # últimas 10 sessões com lang_from, lang_to, minutos

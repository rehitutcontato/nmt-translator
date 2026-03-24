"""
NMT — Fase 2
Modelos SQLAlchemy 2.0 — schema completo do banco de dados.

Tabelas:
  - users          → contas de usuário
  - plans          → definição de planos (free, starter, pro, business, enterprise)
  - subscriptions  → assinaturas ativas de cada usuário
  - usage_logs     → registro de uso (minutos por sessão)
  - payments       → histórico de pagamentos via AbacatePay
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ──────────────────────────────────────────────
# BASE
# ──────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# USERS
# ──────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    # UUID como PK — nunca integers sequenciais (expõe volume de usuários)
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relacionamentos
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    usage_logs: Mapped[list["UsageLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"


# ──────────────────────────────────────────────
# PLANS
# ──────────────────────────────────────────────

class Plan(Base):
    __tablename__ = "plans"

    # ID textual (free, starter, pro, business, enterprise) — mais legível que integer
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    price_brl: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # -1 = ilimitado
    minutes_month: Mapped[int] = mapped_column(Integer, nullable=False)
    max_languages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_simultaneous_users: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    interpreter_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    priority_queue: Mapped[bool] = mapped_column(Boolean, default=False)
    api_access: Mapped[bool] = mapped_column(Boolean, default=False)

    # Dias de histórico de sessões (-1 = ilimitado)
    history_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    # Relacionamentos
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="plan")

    def __repr__(self) -> str:
        return f"<Plan id={self.id!r} name={self.name!r} price={self.price_brl}>"


# ──────────────────────────────────────────────
# SUBSCRIPTIONS
# ──────────────────────────────────────────────

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plans.id"), nullable=False
    )

    # active | cancelled | expired | trialing
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # NULL = sem expiração definida (plano free ou vitalício)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ID de cobrança no AbacatePay (para reconciliação)
    abacate_billing_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relacionamentos
    user: Mapped["User"] = relationship(back_populates="subscriptions")
    plan: Mapped["Plan"] = relationship(back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription user={self.user_id!r} plan={self.plan_id!r} status={self.status!r}>"


# ──────────────────────────────────────────────
# USAGE LOGS
# ──────────────────────────────────────────────

class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Duração real da tradução em minutos (pode ser fracionário, ex: 0.35)
    minutes_used: Mapped[float] = mapped_column(Numeric(6, 3), default=0.0)

    lang_from: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    lang_to: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    # Relacionamentos
    user: Mapped["User"] = relationship(back_populates="usage_logs")

    def __repr__(self) -> str:
        return f"<UsageLog user={self.user_id!r} min={self.minutes_used} at={self.created_at}>"


# ──────────────────────────────────────────────
# PAYMENTS
# ──────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_brl: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # pix | card
    method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # pending | paid | failed | refunded
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    # ID da transação na AbacatePay
    abacate_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)

    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relacionamentos
    user: Mapped["User"] = relationship(back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment user={self.user_id!r} amount={self.amount_brl} status={self.status!r}>"

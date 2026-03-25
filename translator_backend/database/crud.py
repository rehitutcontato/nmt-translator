"""
NMT — Fase 2
Funções CRUD assíncronas.

REGRA: nunca usar f-strings em queries — sempre SQLAlchemy ORM ou parâmetros bindados.
REGRA: nunca retornar senha ou hash para fora deste módulo sem necessidade explícita.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import User, Plan, Subscription, UsageLog, Payment


# ══════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════

async def get_user_by_id(user_id: str, db: AsyncSession) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(email: str, db: AsyncSession) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.email == email.lower().strip())
    )
    return result.scalar_one_or_none()


async def create_user(email: str, password_hash: str, name: Optional[str], db: AsyncSession) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=email.lower().strip(),
        password_hash=password_hash,
        name=name,
    )
    db.add(user)
    await db.flush()  # gera o ID sem commit — o commit vem do get_db()

    # Criar assinatura free automaticamente
    await create_subscription(user.id, "free", db=db)

    return user


async def verify_user_email(user_id: str, db: AsyncSession) -> None:
    await db.execute(
        update(User).where(User.id == user_id).values(email_verified=True)
    )


async def deactivate_user(user_id: str, db: AsyncSession) -> None:
    """Soft delete — mantém os dados mas bloqueia acesso."""
    await db.execute(
        update(User).where(User.id == user_id).values(is_active=False)
    )


# ══════════════════════════════════════════════
# PLANS
# ══════════════════════════════════════════════

async def get_plan(plan_id: str, db: AsyncSession) -> Optional[Plan]:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()


async def get_all_plans(db: AsyncSession) -> list[Plan]:
    result = await db.execute(select(Plan).order_by(Plan.price_brl))
    return list(result.scalars().all())


# ══════════════════════════════════════════════
# SUBSCRIPTIONS
# ══════════════════════════════════════════════

async def get_active_subscription(user_id: str, db: AsyncSession) -> Optional[Subscription]:
    """Retorna a assinatura ativa mais recente do usuário."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.status == "active")
        .order_by(Subscription.started_at.desc())
        .limit(1)
    )
    sub = result.scalar_one_or_none()

    # Verificar se não expirou
    if sub and sub.expires_at and sub.expires_at < datetime.utcnow():
        await _expire_subscription(sub, db)
        # Retorna assinatura free após expirar
        return await get_or_create_free_subscription(user_id, db)

    return sub


async def get_or_create_free_subscription(user_id: str, db: AsyncSession) -> Subscription:
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.plan_id == "free")
        .where(Subscription.status == "active")
    )
    sub = result.scalar_one_or_none()
    if not sub:
        sub = await create_subscription(user_id, "free", db=db)
    return sub


async def create_subscription(
    user_id: str,
    plan_id: str,
    abacate_billing_id: Optional[str] = None,
    expires_days: int = 32,
    db: AsyncSession = None,
) -> Subscription:
    expires_at = None if plan_id == "free" else datetime.utcnow() + timedelta(days=expires_days)

    sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=plan_id,
        status="active",
        expires_at=expires_at,
        abacate_billing_id=abacate_billing_id,
    )
    db.add(sub)
    await db.flush()
    return sub


async def upsert_subscription(
    user_id: str,
    plan_id: str,
    abacate_billing_id: str,
    db: AsyncSession,
) -> Subscription:
    """
    Cria ou atualiza assinatura após confirmação de pagamento.
    Cancela assinaturas pagas anteriores antes de criar a nova.
    """
    # Cancelar assinatura paga ativa anterior (se houver)
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.status == "active")
        .where(Subscription.plan_id != "free")
    )
    old_subs = result.scalars().all()
    for old_sub in old_subs:
        old_sub.status = "cancelled"
        old_sub.cancelled_at = datetime.utcnow()

    # Criar nova assinatura
    return await create_subscription(
        user_id=user_id,
        plan_id=plan_id,
        abacate_billing_id=abacate_billing_id,
        expires_days=32,
        db=db,
    )




async def _expire_subscription(sub: Subscription, db: AsyncSession) -> None:
    sub.status = "expired"
    await db.flush()


async def cancel_subscription(user_id: str, db: AsyncSession) -> None:
    """Soft cancel — expira no fim do período atual."""
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.status == "active")
        .where(Subscription.plan_id != "free")
    )
    sub = result.scalar_one_or_none()
    if sub:
        sub.status = "cancelled"
        sub.cancelled_at = datetime.utcnow()
        await db.flush()

async def get_active_subscription(user_id: str, db) -> Subscription | None:
    from sqlalchemy import select
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == 'active')
    )
    return result.scalars().first()

async def get_usage_this_month(user_id: str, db) -> float:
    from sqlalchemy import select, func
    first_day = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.sum(UsageLog.minutes_used))
        .where(UsageLog.user_id == user_id)
        .where(UsageLog.created_at >= first_day)
    )
    return result.scalar() or 0.0

# ══════════════════════════════════════════════
# USAGE LOGS
# ══════════════════════════════════════════════

async def log_usage(
    user_id: str,
    session_id: str,
    minutes_used: float,
    lang_from: Optional[str],
    lang_to: Optional[str],
    db: AsyncSession,
) -> None:
    """Registra uso de tradução. Chamado em pipeline.py após cada tradução."""
    log = UsageLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        minutes_used=round(minutes_used, 3),
        lang_from=lang_from,
        lang_to=lang_to,
    )
    db.add(log)
    await db.flush()


async def get_usage_this_month(user_id: str, db: AsyncSession) -> float:
    """Total de minutos usados no mês corrente."""
    first_day = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(func.sum(UsageLog.minutes_used))
        .where(UsageLog.user_id == user_id)
        .where(UsageLog.created_at >= first_day)
    )
    return float(result.scalar() or 0.0)


async def get_recent_sessions(user_id: str, limit: int = 10, db: AsyncSession = None) -> list[dict]:
    """Últimas N sessões distintas com resumo de uso."""
    result = await db.execute(
        select(
            UsageLog.session_id,
            UsageLog.lang_from,
            UsageLog.lang_to,
            func.sum(UsageLog.minutes_used).label("total_minutes"),
            func.max(UsageLog.created_at).label("last_activity"),
        )
        .where(UsageLog.user_id == user_id)
        .group_by(UsageLog.session_id, UsageLog.lang_from, UsageLog.lang_to)
        .order_by(func.max(UsageLog.created_at).desc())
        .limit(limit)
    )
    rows = result.all()
    return [
        {
            "session_id": r.session_id,
            "lang_from": r.lang_from,
            "lang_to": r.lang_to,
            "total_minutes": round(float(r.total_minutes), 2),
            "last_activity": r.last_activity.isoformat() if r.last_activity else None,
        }
        for r in rows
    ]


# ══════════════════════════════════════════════
# PAYMENTS
# ══════════════════════════════════════════════

async def create_payment_record(
    user_id: str,
    plan_id: str,
    amount_brl: float,
    method: str,
    abacate_id: str,
    db: AsyncSession,
) -> Payment:
    payment = Payment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        plan_id=plan_id,
        amount_brl=amount_brl,
        method=method,
        status="pending",
        abacate_id=abacate_id,
    )
    db.add(payment)
    await db.flush()
    return payment


async def mark_payment_paid(abacate_id: str, db: AsyncSession) -> Optional[Payment]:
    result = await db.execute(
        select(Payment).where(Payment.abacate_id == abacate_id)
    )
    payment = result.scalar_one_or_none()
    if payment:
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()
        await db.flush()
    return payment


async def get_payment_by_abacate_id(abacate_id: str, db: AsyncSession) -> Optional[Payment]:
    result = await db.execute(
        select(Payment).where(Payment.abacate_id == abacate_id)
    )
    return result.scalar_one_or_none()

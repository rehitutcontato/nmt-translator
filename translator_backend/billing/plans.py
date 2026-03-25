"""
NMT — Fase 2
billing/router.py

Rotas de cobrança, pagamento e uso.

Endpoints:
  POST /billing/checkout          → cria cobrança Pix (requer auth)
  GET  /billing/status/{id}       → polling do status do pagamento (requer auth)
  GET  /billing/usage             → uso do mês atual do usuário (requer auth)
  POST /billing/webhook           → recebe eventos da AbacatePay (público, HMAC)
  POST /billing/cancel            → cancela assinatura (requer auth)
  GET  /billing/plans             → lista planos disponíveis (público)
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from database.connection import get_db
from database.models import Payment, Subscription, User

from .abacatepay import create_billing, get_billing_status, verify_webhook_signature
from .plans import PAID_PLANS, PLAN_UPGRADE_PATH, PLANS, get_plan_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ──────────────────────────────────────────────
# Schemas Pydantic
# ──────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str

    @field_validator("plan_id")
    @classmethod
    def must_be_paid_plan(cls, v: str) -> str:
        if v not in PAID_PLANS:
            raise ValueError(
                f"Plano inválido para cobrança: '{v}'. "
                f"Planos pagos: {sorted(PAID_PLANS)}"
            )
        return v


class CheckoutResponse(BaseModel):
    billing_id: str
    pix_qr_code: str
    pix_qr_code_image: str
    expires_at: str
    amount_brl: float
    plan_id: str
    plan_name: str


class UsageResponse(BaseModel):
    plan_id: str
    plan_name: str
    minutes_used: float
    minutes_limit: int        # -1 = ilimitado
    minutes_remaining: float  # -1 = ilimitado
    upgrade_plan: str | None


# ──────────────────────────────────────────────
# GET /billing/plans  — público, sem auth
# ──────────────────────────────────────────────

@router.get("/plans")
async def list_plans():
    """
    Retorna todos os planos com preços e features.
    Usado pela landing page e pelo dashboard para montar os cards de pricing.
    """
    return {
        "plans": [
            {
                "id": plan_id,
                **{k: v for k, v in plan.items()},
                "is_paid": plan["price_brl"] > 0,
                "upgrade_to": (
                    PLAN_UPGRADE_PATH[PLAN_UPGRADE_PATH.index(plan_id) + 1]
                    if plan_id != "enterprise"
                    else None
                ),
            }
            for plan_id, plan in PLANS.items()
        ]
    }


# ──────────────────────────────────────────────
# POST /billing/checkout
# ──────────────────────────────────────────────

@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cria uma cobrança Pix one-time e retorna os dados do QR Code.
    O frontend exibe o QR e inicia polling em /billing/status/{billing_id}.
    """
    plan = get_plan_info(body.plan_id)

    try:
        result = await create_billing(
            user_id=str(current_user.id),
            plan_id=body.plan_id,
            plan_name=plan["name"],
            amount_brl=plan["price_brl"],
            user_email=current_user.email,
            user_name=current_user.name or current_user.email,
        )
    except Exception as exc:
        logger.error("Erro no checkout AbacatePay: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Falha ao criar cobrança. Tente novamente em alguns instantes.",
        )

    # AbacatePay pode envolver a resposta em 'data'
    billing = result.get("data", result)

    billing_id = billing.get("id", "")
    if not billing_id:
        logger.error("AbacatePay não retornou billing id: %s", result)
        raise HTTPException(status_code=502, detail="Resposta inválida do gateway de pagamento.")

    return CheckoutResponse(
        billing_id=billing_id,
        pix_qr_code=billing.get("pixQrCode", ""),
        pix_qr_code_image=billing.get("pixQrCodeImage", ""),
        expires_at=billing.get("expiresAt", ""),
        amount_brl=plan["price_brl"],
        plan_id=body.plan_id,
        plan_name=plan["name"],
    )


# ──────────────────────────────────────────────
# GET /billing/status/{billing_id}
# Frontend faz polling aqui a cada ~3s após exibir o QR
# ──────────────────────────────────────────────

@router.get("/status/{billing_id}")
async def billing_status(
    billing_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Consulta o status de uma cobrança.
    Retorna: PENDING | PAID | EXPIRED | CANCELLED
    O frontend para o polling quando receber PAID ou EXPIRED.
    """
    try:
        result = await get_billing_status(billing_id)
    except Exception as exc:
        logger.error("Erro ao consultar status AbacatePay: %s", exc)
        raise HTTPException(status_code=502, detail="Não foi possível consultar o pagamento.")

    billing = result.get("data", result)
    status  = billing.get("status", "PENDING").upper()

    return {
        "billing_id": billing_id,
        "status": status,
        "paid": status == "PAID",
    }


# ──────────────────────────────────────────────
# GET /billing/usage
# ──────────────────────────────────────────────

@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retorna o uso do mês atual e os limites do plano ativo."""
    from database.crud import get_active_subscription, get_usage_this_month
    from .plans import _next_plan

    sub     = await get_active_subscription(str(current_user.id), db)
    plan_id = sub.plan_id if sub else "free"
    plan    = get_plan_info(plan_id)

    used  = await get_usage_this_month(str(current_user.id), db)
    limit = plan["minutes_month"]

    if limit == -1:
        remaining = -1.0
    else:
        remaining = round(max(0.0, limit - used), 2)

    return UsageResponse(
        plan_id=plan_id,
        plan_name=plan["name"],
        minutes_used=round(used, 2),
        minutes_limit=limit,
        minutes_remaining=remaining,
        upgrade_plan=_next_plan(plan_id),
    )


# ──────────────────────────────────────────────
# POST /billing/cancel
# ──────────────────────────────────────────────

@router.post("/cancel")
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-cancel: marca a assinatura como 'cancelled'.
    O acesso continua até expires_at (fim do período pago).
    """
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == str(current_user.id),
            Subscription.status == "active",
        )
    )
    sub = result.scalars().first()

    if not sub:
        raise HTTPException(status_code=404, detail="Nenhuma assinatura ativa encontrada.")

    sub.status       = "cancelled"
    sub.cancelled_at = datetime.utcnow()
    await db.commit()

    logger.info("Assinatura cancelada: user=%s expires_at=%s", current_user.id, sub.expires_at)

    return {
        "cancelled": True,
        "access_until": sub.expires_at.isoformat() if sub.expires_at else None,
        "message": (
            f"Assinatura cancelada. Seu acesso ao plano {sub.plan_id} "
            f"continua até {sub.expires_at.strftime('%d/%m/%Y') if sub.expires_at else 'fim do período'}."
        ),
    }


# ──────────────────────────────────────────────
# POST /billing/webhook
# CRÍTICO: validar assinatura HMAC antes de qualquer ação
# ──────────────────────────────────────────────

@router.post("/webhook")
async def abacatepay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Recebe eventos da AbacatePay e atualiza o banco de dados.

    Eventos tratados:
      BILLING_PAID      → ativa/renova assinatura + registra pagamento
      BILLING_EXPIRED   → expira assinatura
      BILLING_CANCELLED → expira assinatura

    SEMPRE retorna 200 para a AbacatePay (evita reenvios desnecessários).
    Erros internos são logados, não propagados.
    """
    body      = await request.body()
    signature = request.headers.get("X-Abacate-Signature", "")

    # ── 1. Validar assinatura ─────────────────────────────────────────
    if not await verify_webhook_signature(body, signature):
        logger.warning(
            "Webhook rejeitado: assinatura inválida. IP=%s",
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    data     = await request.json()
    event    = data.get("event", "").upper()
    metadata = data.get("metadata", {})

    logger.info("Webhook AbacatePay recebido: event=%s", event)

    # ── 2. BILLING_PAID ───────────────────────────────────────────────
    if event == "BILLING_PAID":
        user_id    = metadata.get("user_id")
        plan_id    = metadata.get("plan_id")
        billing    = data.get("billing", {})
        abacate_id = billing.get("id", "")
        amount_brl = billing.get("amount", 0) / 100  # centavos → reais

        if not user_id or not plan_id:
            logger.error("BILLING_PAID sem metadados: %s", metadata)
            return {"ok": False, "reason": "missing_metadata"}

        if plan_id not in PLANS:
            logger.error("BILLING_PAID com plano desconhecido: %s", plan_id)
            return {"ok": False, "reason": "unknown_plan"}

        try:
            await _upsert_subscription(user_id, plan_id, abacate_id, db)
            await _create_payment_record(user_id, plan_id, amount_brl, "pix", abacate_id, db)
        except Exception as exc:
            logger.error("Erro ao salvar pagamento no banco: %s", exc)
            # Retorna 200 mesmo assim — AbacatePay não deve reenviar para erros de DB
            return {"ok": False, "reason": "db_error"}

        # Email de confirmação em background (falha não bloqueia resposta)
        try:
            from auth.service import send_payment_confirmation_email
            await send_payment_confirmation_email(user_id, plan_id, db)
        except Exception as exc:
            logger.warning("Email de confirmação falhou (não crítico): %s", exc)

    # ── 3. BILLING_EXPIRED / BILLING_CANCELLED ────────────────────────
    elif event in ("BILLING_EXPIRED", "BILLING_CANCELLED"):
        user_id = metadata.get("user_id")
        if user_id:
            try:
                await _expire_subscription(user_id, db)
            except Exception as exc:
                logger.error("Erro ao expirar assinatura: %s", exc)
        else:
            logger.warning("Evento %s sem user_id nos metadados.", event)

    else:
        logger.info("Evento AbacatePay não tratado (ignorado): %s", event)

    return {"ok": True}


# ──────────────────────────────────────────────
# Helpers privados de banco
# ──────────────────────────────────────────────

async def _upsert_subscription(
    user_id: str,
    plan_id: str,
    abacate_id: str,
    db: AsyncSession,
) -> None:
    """Cria ou renova assinatura após pagamento confirmado."""
    expires_at = datetime.utcnow() + timedelta(days=32)  # 30 + 2 de margem

    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    sub = result.scalars().first()

    if sub:
        sub.plan_id            = plan_id
        sub.status             = "active"
        sub.expires_at         = expires_at
        sub.cancelled_at       = None
        sub.abacate_billing_id = abacate_id
    else:
        sub = Subscription(
            user_id=user_id,
            plan_id=plan_id,
            status="active",
            expires_at=expires_at,
            abacate_billing_id=abacate_id,
        )
        db.add(sub)

    await db.commit()
    logger.info(
        "Subscription upserted: user=%s plan=%s expires=%s",
        user_id, plan_id, expires_at.date(),
    )


async def _create_payment_record(
    user_id: str,
    plan_id: str,
    amount_brl: float,
    method: str,
    abacate_id: str,
    db: AsyncSession,
) -> None:
    payment = Payment(
        user_id=user_id,
        plan_id=plan_id,
        amount_brl=amount_brl,
        method=method,
        status="paid",
        abacate_id=abacate_id,
        paid_at=datetime.utcnow(),
    )
    db.add(payment)
    await db.commit()
    logger.info(
        "Pagamento registrado: user=%s plan=%s valor=R$%.2f",
        user_id, plan_id, amount_brl,
    )


async def _expire_subscription(user_id: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    )
    sub = result.scalars().first()

    if sub:
        sub.status       = "expired"
        sub.cancelled_at = datetime.utcnow()
        await db.commit()
        logger.info("Assinatura expirada via webhook: user=%s", user_id)
    else:
        logger.warning(
            "Tentativa de expirar assinatura inexistente/já inativa: user=%s", user_id
        )

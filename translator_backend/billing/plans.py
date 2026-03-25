"""
NMT — Fase 2
billing/plans.py

Definição dos planos e verificação de acesso.
Fonte da verdade para limites — alinhado com análise financeira v2.

Planos (preços em BRL):
  free       → R$0    | 30 min/mês  | 2 idiomas
  starter    → R$19   | 120 min/mês | 15 idiomas
  pro        → R$49   | 400 min/mês | 15 idiomas + fila prioritária
  business   → R$129  | 1500 min/mês| 3 usuários simultâneos
  enterprise → R$299  | ilimitado   | 10 usuários + API REST
"""

from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_usage_this_month

# ──────────────────────────────────────────────
# DEFINIÇÃO DOS PLANOS
# ──────────────────────────────────────────────

PLANS: dict[str, dict] = {
    "free": {
        "name": "Free",
        "price_brl": 0.00,
        "minutes_month": 30,
        "max_languages": 2,
        "max_simultaneous_users": 1,
        "interpreter_mode": False,
        "priority_queue": False,
        "api_access": False,
        "history_days": 7,
    },
    "starter": {
        "name": "Starter",
        "price_brl": 19.00,
        "minutes_month": 120,
        "max_languages": 15,
        "max_simultaneous_users": 1,
        "interpreter_mode": True,
        "priority_queue": False,
        "api_access": False,
        "history_days": 30,
    },
    "pro": {
        "name": "Pro",
        "price_brl": 49.00,
        "minutes_month": 400,
        "max_languages": 15,
        "max_simultaneous_users": 1,
        "interpreter_mode": True,
        "priority_queue": True,
        "api_access": False,
        "history_days": 90,
    },
    "business": {
        "name": "Business",
        "price_brl": 129.00,
        "minutes_month": 1500,
        "max_languages": 15,
        "max_simultaneous_users": 3,
        "interpreter_mode": True,
        "priority_queue": True,
        "api_access": False,
        "history_days": -1,  # ilimitado
    },
    "enterprise": {
        "name": "Enterprise",
        "price_brl": 299.00,
        "minutes_month": -1,  # ilimitado
        "max_languages": 15,
        "max_simultaneous_users": 10,
        "interpreter_mode": True,
        "priority_queue": True,
        "api_access": True,
        "history_days": 365,
    },
}

# Ordem de upgrade (para UI de "próximo plano")
PLAN_UPGRADE_PATH = ["free", "starter", "pro", "business", "enterprise"]

# Planos que exigem pagamento (não-free)
PAID_PLANS = {k for k, v in PLANS.items() if v["price_brl"] > 0}


# ──────────────────────────────────────────────
# VERIFICAÇÃO DE ACESSO
# ──────────────────────────────────────────────

async def check_access(
    user_id: str,
    plan_id: str,
    db: AsyncSession,
) -> dict:
    """
    Verifica se o usuário pode iniciar uma nova sessão de tradução.

    Returns dict:
      allowed          → bool
      reason           → str ('ok' | 'quota_exceeded' | 'unknown_plan')
      remaining        → float (minutos restantes, -1 = ilimitado)
      minutes_used     → float (uso no mês atual)
      minutes_limit    → int (-1 = ilimitado)
      upgrade_plan     → str | None (próximo plano disponível)
    """
    plan = PLANS.get(plan_id)

    # Plano desconhecido → força free
    if not plan:
        plan_id = "free"
        plan = PLANS["free"]

    # Plano ilimitado → acesso direto
    if plan["minutes_month"] == -1:
        return {
            "allowed": True,
            "reason": "unlimited",
            "remaining": -1,
            "minutes_used": 0.0,
            "minutes_limit": -1,
            "upgrade_plan": None,
        }

    used = await get_usage_this_month(user_id, db)
    remaining = plan["minutes_month"] - used

    if remaining <= 0:
        upgrade = _next_plan(plan_id)
        return {
            "allowed": False,
            "reason": "quota_exceeded",
            "remaining": 0.0,
            "minutes_used": round(used, 2),
            "minutes_limit": plan["minutes_month"],
            "upgrade_plan": upgrade,
        }

    return {
        "allowed": True,
        "reason": "ok",
        "remaining": round(remaining, 2),
        "minutes_used": round(used, 2),
        "minutes_limit": plan["minutes_month"],
        "upgrade_plan": _next_plan(plan_id),
    }


def _next_plan(current_plan_id: str) -> Optional[str]:
    """Retorna o próximo plano na escala de upgrade, ou None se já for enterprise."""
    try:
        idx = PLAN_UPGRADE_PATH.index(current_plan_id)
        if idx < len(PLAN_UPGRADE_PATH) - 1:
            return PLAN_UPGRADE_PATH[idx + 1]
    except ValueError:
        pass
    return None


def get_plan_info(plan_id: str) -> dict:
    """Retorna info do plano ou do free como fallback."""
    return PLANS.get(plan_id, PLANS["free"])


def plan_allows_interpreter_mode(plan_id: str) -> bool:
    return PLANS.get(plan_id, PLANS["free"]).get("interpreter_mode", False)


def plan_allows_api_access(plan_id: str) -> bool:
    return PLANS.get(plan_id, PLANS["free"]).get("api_access", False)

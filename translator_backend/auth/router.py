"""
NMT — Fase 2
auth/router.py

Endpoints de autenticação:
  POST /auth/register      → cria conta + envia email de boas-vindas
  POST /auth/login         → autentica + retorna tokens
  POST /auth/refresh       → renova access token via refresh token
  POST /auth/logout        → invalida refresh token (client-side por ora)
  GET  /auth/me            → dados do usuário + plano atual
  POST /auth/verify-email  → ativa email via token
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.email import send_welcome_email
from auth.service import (
    create_access_token,
    create_email_verification_token,
    create_refresh_token,
    hash_password,
    token_expires_in_seconds,
    verify_email_token,
    verify_password,
    verify_refresh_token,
)
from database.connection import get_db
from database.crud import (
    create_user,
    get_active_subscription,
    get_usage_this_month,
    get_user_by_email,
    get_user_by_id,
    verify_user_email,
)
from database.models import User
from database.schemas import (
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserWithPlan,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ──────────────────────────────────────────────
# REGISTER
# ──────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Cria nova conta de usuário.

    - Valida força da senha (via schema Pydantic)
    - Rejeita email já cadastrado
    - Cria assinatura Free automaticamente
    - Envia email de boas-vindas com link de verificação
    - Retorna access + refresh tokens imediatamente (sem esperar verificação)
    """
    # Verificar email duplicado
    existing = await get_user_by_email(body.email, db)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email já cadastrado",
        )

    # Criar usuário (cria assinatura free internamente)
    user = await create_user(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        db=db,
    )

    # Buscar plano da assinatura recém-criada
    sub = await get_active_subscription(user.id, db)
    plan_id = sub.plan_id if sub else "free"

    # Gerar tokens
    access_token = create_access_token(user.id, plan_id)
    refresh_token = create_refresh_token(user.id)

    # Enviar email de boas-vindas (não-bloqueante, erro não propaga)
    try:
        verify_token = create_email_verification_token(user.id)
        await send_welcome_email(user.email, user.name, verify_token)
    except Exception as e:
        logger.warning(f"[AUTH] Falha ao enviar email de boas-vindas para {user.email}: {e}")

    logger.info(f"[AUTH] Novo usuário registrado: {user.email}")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=token_expires_in_seconds(),
    )


# ──────────────────────────────────────────────
# LOGIN
# ──────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(
    body: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Autentica usuário e retorna tokens.

    Mensagem de erro genérica intencional — não revela se email existe.
    """
    user = await get_user_by_email(body.email, db)

    # Erro genérico — não revela se o email existe no sistema
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada. Entre em contato com o suporte.",
        )

    # Buscar plano atual
    sub = await get_active_subscription(user.id, db)
    plan_id = sub.plan_id if sub else "free"

    access_token = create_access_token(user.id, plan_id)
    refresh_token = create_refresh_token(user.id)

    logger.info(f"[AUTH] Login: {user.email} (plano: {plan_id})")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=token_expires_in_seconds(),
    )


# ──────────────────────────────────────────────
# REFRESH TOKEN
# ──────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Gera novo access token usando refresh token válido.
    O refresh token em si não muda (sliding window seria outro padrão).
    """
    payload = verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido ou expirado",
        )

    user_id = payload.get("sub")
    user = await get_user_by_id(user_id, db)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )

    # Buscar plano atual (pode ter mudado desde o último login)
    sub = await get_active_subscription(user.id, db)
    plan_id = sub.plan_id if sub else "free"

    new_access_token = create_access_token(user.id, plan_id)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=body.refresh_token,  # mesmo refresh token
        expires_in=token_expires_in_seconds(),
    )


# ──────────────────────────────────────────────
# ME (dados do usuário autenticado)
# ──────────────────────────────────────────────

@router.get("/me", response_model=UserWithPlan)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retorna dados do usuário logado + plano atual + uso do mês.
    Usado pelo frontend para renderizar o header e barra de progresso.
    """
    sub = await get_active_subscription(current_user.id, db)
    plan_id = sub.plan_id if sub else "free"

    minutes_used = await get_usage_this_month(current_user.id, db)

    # Buscar limite do plano
    from billing.plans import PLANS
    plan_info = PLANS.get(plan_id, PLANS["free"])
    minutes_limit = plan_info["minutes_month"]

    return UserWithPlan(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        email_verified=current_user.email_verified,
        created_at=current_user.created_at,
        plan_id=plan_id,
        plan_name=plan_info["name"],
        minutes_used_this_month=round(minutes_used, 2),
        minutes_limit=minutes_limit,
    )


# ──────────────────────────────────────────────
# LOGOUT (client-side — token é invalidado no frontend)
# ──────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout simples. O frontend deve descartar os tokens armazenados.

    Nota: JWT é stateless — revogação real requer blacklist em Redis.
    Para o MVP, client-side é suficiente. O access token expira em 1h no máximo.
    """
    logger.info(f"[AUTH] Logout: {current_user.email}")
    # Sem corpo na resposta (204)


# ──────────────────────────────────────────────
# VERIFY EMAIL
# ──────────────────────────────────────────────

@router.post("/verify-email")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Ativa o email do usuário via token enviado por email.
    Token tem 24h de validade.
    """
    user_id = verify_email_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido ou expirado",
        )

    await verify_user_email(user_id, db)
    logger.info(f"[AUTH] Email verificado para user_id={user_id}")

    return {"message": "Email verificado com sucesso"}

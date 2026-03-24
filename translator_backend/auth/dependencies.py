"""
NMT — Fase 2
auth/dependencies.py

Funções de dependência FastAPI para autenticação.

Uso em rotas protegidas:
    @router.get("/profile")
    async def profile(user: User = Depends(get_current_user)):
        ...

    @router.get("/admin")
    async def admin(user: User = Depends(get_verified_user)):
        ...
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from auth.service import verify_access_token
from database.connection import get_db
from database.crud import get_user_by_id, get_active_subscription
from database.models import User

# Bearer token extractor
bearer_scheme = HTTPBearer(auto_error=False)

# Erros padronizados — reutilizados em múltiplos lugares
CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Token inválido ou expirado",
    headers={"WWW-Authenticate": "Bearer"},
)

INACTIVE_EXCEPTION = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Conta desativada",
)

EMAIL_NOT_VERIFIED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Email não verificado. Verifique sua caixa de entrada.",
)


# ──────────────────────────────────────────────
# DEPENDÊNCIAS PRINCIPAIS
# ──────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extrai e valida o Bearer token.
    Retorna o objeto User do banco.
    Lança 401 se token inválido ou usuário não encontrado.
    """
    if not credentials:
        raise CREDENTIALS_EXCEPTION

    payload = verify_access_token(credentials.credentials)
    if not payload:
        raise CREDENTIALS_EXCEPTION

    user_id: str = payload.get("sub")
    user = await get_user_by_id(user_id, db)

    if not user:
        raise CREDENTIALS_EXCEPTION

    if not user.is_active:
        raise INACTIVE_EXCEPTION

    return user


async def get_verified_user(
    user: User = Depends(get_current_user),
) -> User:
    """
    Como get_current_user, mas exige email verificado.
    Usado em rotas que requerem conta confirmada.
    """
    if not user.email_verified:
        raise EMAIL_NOT_VERIFIED
    return user


async def get_current_user_with_plan(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna {"user": User, "plan_id": str} — evita segundo round-trip ao banco
    usando o plan_id embutido no token.

    Usado no WebSocket handler onde queremos latência mínima.
    """
    if not credentials:
        raise CREDENTIALS_EXCEPTION

    payload = verify_access_token(credentials.credentials)
    if not payload:
        raise CREDENTIALS_EXCEPTION

    user_id: str = payload.get("sub")
    plan_id: str = payload.get("plan", "free")

    user = await get_user_by_id(user_id, db)
    if not user or not user.is_active:
        raise CREDENTIALS_EXCEPTION

    return {"user": user, "plan_id": plan_id}


# ──────────────────────────────────────────────
# EXTRATOR DE TOKEN PARA WEBSOCKET
# WebSocket não suporta headers HTTP padrão —
# token vem como query param: ?token=eyJ...
# ──────────────────────────────────────────────

def extract_ws_token(token: str) -> tuple[str, str]:
    """
    Valida token JWT de query param do WebSocket.

    Returns:
        (user_id, plan_id)

    Raises:
        ValueError com mensagem de erro para fechar o WebSocket.
    """
    if not token:
        raise ValueError("Token obrigatório")

    payload = verify_access_token(token)
    if not payload:
        raise ValueError("Token inválido ou expirado")

    user_id = payload.get("sub")
    plan_id = payload.get("plan", "free")

    if not user_id:
        raise ValueError("Token malformado")

    return user_id, plan_id

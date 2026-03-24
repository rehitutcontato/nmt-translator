"""
NMT — Fase 2
Conexão assíncrona com PostgreSQL via SQLAlchemy 2.0
"""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from database.models import Base

# ──────────────────────────────────────────────
# ENGINE
# ──────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Railway injeta postgresql:// mas asyncpg precisa de postgresql+asyncpg://
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# NullPool evita problemas com fork em ambientes serverless / Railway
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("ENVIRONMENT", "production") == "development",
    poolclass=NullPool,
)

# Session factory assíncrona
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ──────────────────────────────────────────────
# DEPENDENCY INJECTION (FastAPI)
# ──────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency do FastAPI.
    Uso: db: AsyncSession = Depends(get_db)
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ──────────────────────────────────────────────
# HEALTH CHECK
# ──────────────────────────────────────────────

async def check_db_connection() -> bool:
    """Retorna True se o banco está acessível. Usado no /health."""
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# CRIAÇÃO DE TABELAS (usado no seed/dev, não em produção)
# Em produção, Alembic controla o schema.
# ──────────────────────────────────────────────

async def create_all_tables():
    """Cria todas as tabelas. Só para dev/teste — em produção use alembic upgrade head."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

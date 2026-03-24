"""
NMT — Fase 2
Script de seed: insere os planos no banco.

Rodar UMA VEZ após a primeira migration:
    python seed_plans.py

Idempotente — pode rodar múltiplas vezes sem duplicar.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

# Garante que o módulo database seja encontrado
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.dialects.postgresql import insert as pg_insert
from database.models import Plan, Base

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Definição dos planos — alinhado com análise financeira v2
PLANS_DATA = [
    {
        "id": "free",
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
    {
        "id": "starter",
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
    {
        "id": "pro",
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
    {
        "id": "business",
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
    {
        "id": "enterprise",
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
]


async def seed():
    engine = create_async_engine(DATABASE_URL, echo=True)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        # Garante que as tabelas existem (fallback caso alembic não tenha rodado)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        for plan_data in PLANS_DATA:
            # INSERT ... ON CONFLICT DO UPDATE — idempotente
            stmt = pg_insert(Plan).values(**plan_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={k: v for k, v in plan_data.items() if k != "id"},
            )
            await session.execute(stmt)

        await session.commit()

    await engine.dispose()
    print(f"✅ {len(PLANS_DATA)} planos inseridos/atualizados com sucesso.")


if __name__ == "__main__":
    if not DATABASE_URL:
        print("❌ DATABASE_URL não definida. Configure no .env e tente novamente.")
        sys.exit(1)
    asyncio.run(seed())

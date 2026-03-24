"""
alembic/env.py — NMT Fase 2
Configuração assíncrona do Alembic para PostgreSQL + asyncpg.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# 1. ADICIONE ESTAS DUAS LINHAS AQUI
from dotenv import load_dotenv
load_dotenv()

# Adiciona o diretório raiz ao path para importar database.models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Base

# ──────────────────────────────────────────────
# ALEMBIC CONFIG
# ──────────────────────────────────────────────

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Lê DATABASE_URL do ambiente — nunca do arquivo ini
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Metadados para autogenerate de migrations
target_metadata = Base.metadata


# ──────────────────────────────────────────────
# MIGRATIONS
# ──────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Modo offline — gera SQL sem conectar ao banco."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Modo online assíncrono com asyncpg."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

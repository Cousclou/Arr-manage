from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.services.runtime_config import seed_default_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


MIGRATIONS = [
    "ALTER TABLE task_logs ADD COLUMN IF NOT EXISTS service VARCHAR(16) DEFAULT 'system'",
    "ALTER TABLE task_logs ADD COLUMN IF NOT EXISTS stats_json TEXT",
    "ALTER TABLE task_logs ADD COLUMN IF NOT EXISTS details_count INTEGER DEFAULT 0",
    "ALTER TABLE task_logs ADD COLUMN IF NOT EXISTS details_truncated BOOLEAN DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS ix_task_logs_service ON task_logs (service)",
    "CREATE INDEX IF NOT EXISTS ix_task_logs_created_at ON task_logs (created_at)",
]


async def migrate_db() -> None:
    from app.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in MIGRATIONS:
            await conn.execute(text(stmt))


async def init_db() -> None:
    await migrate_db()
    async with async_session() as session:
        await seed_default_settings(session)

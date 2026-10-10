import logging
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


if not settings.DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing. Set it in Render Environment Variables."
    )


engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db():
    """
    د جدولونو جوړول، که موجود نه وي.
    موجود معلومات نه حذفوي.
    """
    from app import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    logger.info("Database initialized successfully.")


async def close_db():
    await engine.dispose()
    logger.info("Database connections closed.")

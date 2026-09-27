import os

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def database_url() -> str:
    """DATABASE_URL is libpq-style (shared with the loader); switch the driver to asyncpg."""
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def make_engine() -> AsyncEngine:
    return create_async_engine(database_url(), pool_size=5, pool_pre_ping=True)

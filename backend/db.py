from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
import os
from dotenv import load_dotenv

load_dotenv()

def _normalize_database_url(raw_url: str | None) -> str:
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Railway often provides postgres:// or postgresql:// URL.
    # SQLAlchemy async engine needs postgresql+asyncpg://
    if raw_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw_url[len("postgres://"):]
    if raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+asyncpg://"):
        return "postgresql+asyncpg://" + raw_url[len("postgresql://"):]
    return raw_url


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL"))

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

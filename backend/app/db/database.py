"""数据库连接与会话管理 - 使用 asyncpg + SQLAlchemy 2.0 async。"""

from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _build_engine():
    """根据数据库类型创建引擎，自动处理 SQLite 的特殊参数。"""
    url = settings.DATABASE_URL
    parsed = urlparse(url)
    extra_kwargs = {"echo": settings.DEBUG}

    if parsed.scheme.startswith("sqlite"):
        # SQLite 需要 check_same_thread=False 和较长的超时以避免并发写入锁
        extra_kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": 30,  # 等待锁的秒数，默认 5 秒
        }

    return create_async_engine(url, **extra_kwargs)


engine = _build_engine()
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

"""SQLAlchemy ORM 模型定义。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db.database import Base


# 根据数据库类型选择列类型
def get_json_column():
    """获取JSON列类型"""
    if "sqlite" in settings.DATABASE_URL.lower():
        # SQLite使用标准JSON类型
        return JSON()
    else:
        # PostgreSQL使用JSONB类型
        try:
            from sqlalchemy.dialects.postgresql import JSONB
            return JSONB()
        except ImportError:
            return JSON()


# 获取JSON列实例
JSONColumn = get_json_column()


class Document(Base):
    """上传文档元数据表。"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkJob(Base):
    """分块任务表 - 每次 execute 生成一个 job。"""

    __tablename__ = "chunk_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id"), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONColumn, default=dict)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="job")


class Chunk(Base):
    """分块结果表。"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("chunk_jobs.id"), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONColumn, default=dict)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped["ChunkJob"] = relationship("ChunkJob", back_populates="chunks")

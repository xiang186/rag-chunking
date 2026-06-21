"""FastAPI 应用入口。"""

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.documents import router as documents_router
from app.api.logs import router as logs_router
from app.api.retrieval import router as retrieval_router
from app.config import settings
from app.db.database import Base, engine


# ── 日志配置 ──
# 抑制 PDF 解析过程中第三方库的噪声日志
logging.getLogger("langdetect").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("unstructured").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时创建数据库表。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 配置 - 允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents_router)
app.include_router(logs_router)
app.include_router(retrieval_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME}

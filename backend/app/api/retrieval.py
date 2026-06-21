"""
检索相关 API 路由 - 支持元数据硬过滤、向量检索

提供：
1. POST /api/v1/retrieval/search - 向量检索 + 元数据过滤
2. POST /api/v1/retrieval/clean - 数据清洗管道
"""

import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.cleaning.pipeline import CleaningPipeline
from app.db.database import get_db
from app.db.models import Chunk
from app.models.schemas import (
    CleaningConfigRequest,
    CleaningConfigResponse,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalResultItem,
)
from app.services.embedding_service import EmbeddingService, QUERY_PREFIX
from app.services.log_service import emit_log, LogLevel

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


@router.post("/search", response_model=RetrievalResponse)
async def search_chunks(request: RetrievalRequest):
    """
    向量检索 + 元数据硬过滤。

    流程：
    1. 将用户查询文本向量化（自动添加 "query: " 前缀）
    2. 从数据库读取所有 chunks（有 metadata_filters 时先过滤）
    3. 计算向量余弦相似度
    4. 按相似度降序返回 top_k 结果

    Args:
        request: 检索请求，包含 query、top_k 和可选的 metadata_filters。

    Returns:
        RetrievalResponse: 检索结果列表。
    """
    t0 = time.perf_counter()

    await emit_log(
        f"检索请求: query={request.query[:50]}..., top_k={request.top_k}, filters={request.metadata_filters}",
        level=LogLevel.INFO,
        source="retrieval",
        query=request.query[:100],
        top_k=request.top_k,
        metadata_filters=request.metadata_filters,
    )

    try:
        # ── 1. 获取查询向量 ──
        embed_service = EmbeddingService()
        query_vector = await embed_service.get_query_embedding(request.query)

        # ── 2. 从数据库读取 chunks ──
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.db.database import async_session_factory

        async with async_session_factory() as session:
            # 构建查询
            stmt = select(Chunk)

            # 应用元数据硬过滤（metadata_filters）
            # metadata_filters 示例: {"source_doc": "档案A.pdf"}
            # 通过 SQLAlchemy JSON 字段路径匹配
            for key, value in request.metadata_filters.items():
                # 对 JSON 字段进行键值匹配
                stmt = stmt.where(
                    Chunk.chunk_metadata[key].as_string() == str(value)
                )

            result = await session.execute(stmt)
            db_chunks = result.scalars().all()

        if not db_chunks:
            elapsed = int((time.perf_counter() - t0) * 1000)
            await emit_log(
                f"检索完成 ({elapsed}ms): 无匹配结果",
                level=LogLevel.INFO,
                source="retrieval",
                duration_ms=elapsed,
                total_results=0,
            )
            return RetrievalResponse(
                query=request.query,
                total_results=0,
                results=[],
            )

        # ── 3. 批量计算所有 chunk 的向量（使用 passage 前缀） ──
        chunk_texts = [c.text for c in db_chunks]
        chunk_vectors = await embed_service.get_embeddings_batch(chunk_texts, prefix="passage: ")

        # ── 4. 计算余弦相似度 ──
        from langchain_community.utils.math import cosine_similarity

        scores = []
        for cv in chunk_vectors:
            sim = cosine_similarity([query_vector], [cv])[0][0]
            scores.append(float(sim))

        # ── 5. 按分数降序排序，取 top_k ──
        scored = list(zip(db_chunks, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        top_results = scored[: request.top_k]

        items: List[RetrievalResultItem] = []
        for chunk, score in top_results:
            items.append(
                RetrievalResultItem(
                    chunk_id=str(chunk.id),
                    doc_id=chunk.doc_id,
                    text=chunk.text[:500],  # 返回前 500 字符作为预览
                    metadata=chunk.chunk_metadata if chunk.chunk_metadata else {},
                    score=round(score, 4),
                )
            )

        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"检索完成 ({elapsed}ms): 共 {len(db_chunks)} 条，返回前 {len(items)} 条",
            level=LogLevel.SUCCESS,
            source="retrieval",
            duration_ms=elapsed,
            total_candidates=len(db_chunks),
            total_results=len(items),
        )

        return RetrievalResponse(
            query=request.query,
            total_results=len(items),
            results=items,
        )

    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"检索失败 ({elapsed}ms): {e}",
            level=LogLevel.ERROR,
            source="retrieval",
            duration_ms=elapsed,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=f"检索失败: {e}")


@router.post("/clean", response_model=CleaningConfigResponse)
async def clean_text(request: CleaningConfigRequest):
    """
    数据清洗管道 - 使用配置动态组装清洗链并执行。

    用法示例请求体：
    {
        "text": "待清洗的原始文本...",
        "enable_heuristic": true,
        "enable_pii": true,
        ...
    }
    """
    # 注意：text 字段通过 query 参数传入，避免与配置混合
    # 但原设计是让前端 POST 一个同时包含 text 和 config 的结构
    # 为保持 API 设计简洁，前端可以传 text 作为请求体的额外字段
    raise HTTPException(
        status_code=400,
        detail="请使用 POST /api/v1/retrieval/clean-text 接口（请求体中包含 text 字段）",
    )


class CleanTextRequest(CleaningConfigRequest):
    """带待清洗文本的清洗请求。"""
    text: str = ""


@router.post("/clean-text", response_model=CleaningConfigResponse)
async def clean_text_with_body(request: CleanTextRequest):
    """
    数据清洗管道 - 接收原文 + 清洗配置，返回清洗后文本。

    流程：
    1. 根据配置字典（enable_heuristic / enable_pii 等）组装清洗链
    2. 按注册顺序依次执行清洗器
    3. 返回清洗结果及变更记录

    Args:
        request: CleanTextRequest - 包含 text 和清洗配置。

    Returns:
        CleaningConfigResponse: 清洗后文本、变更记录和元数据。
    """
    t0 = time.perf_counter()

    if not request.text:
        raise HTTPException(status_code=400, detail="待清洗文本不能为空")

    await emit_log(
        f"开始清洗: text_length={len(request.text)}, config={request.model_dump(exclude={'text'})}",
        level=LogLevel.INFO,
        source="cleaning",
        text_length=len(request.text),
    )

    try:
        # 组装清洗配置
        config_dict = request.model_dump(exclude={"text"})

        # 执行清洗管道
        pipeline = CleaningPipeline(config=config_dict)
        result = pipeline.run(request.text)

        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"清洗完成 ({elapsed}ms): cleaned={result.cleaned}, changes={len(result.changes)}",
            level=LogLevel.SUCCESS,
            source="cleaning",
            duration_ms=elapsed,
            cleaned=result.cleaned,
            change_count=len(result.changes),
        )

        return CleaningConfigResponse(
            text=result.text,
            cleaned=result.cleaned,
            changes=result.changes,
            metadata=result.metadata,
        )

    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"清洗失败 ({elapsed}ms): {e}",
            level=LogLevel.ERROR,
            source="cleaning",
            duration_ms=elapsed,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=f"清洗失败: {e}")

"""分块服务 - 预览与执行分块任务。"""

import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Chunk, ChunkJob
from app.models.schemas import (
    ChunkExecuteResponse,
    ChunkPreviewItem,
    ChunkPreviewResponse,
    CleaningConfigRequest,
    StrategyName,
)
from app.services.log_service import emit_log, LogLevel
from app.strategies.base import ChunkResult
from app.strategies.factory import ChunkerFactory


def apply_cleaning(text: str, cleaning_config: CleaningConfigRequest | None) -> str:
    """如果提供了清洗配置，执行清洗管道并返回清洗后文本。"""
    if cleaning_config is None:
        return text

    from app.cleaning.pipeline import CleaningPipeline

    config_dict = cleaning_config.model_dump()
    pipeline = CleaningPipeline(config=config_dict)
    result = pipeline.run(text)
    return result.text


def run_chunking(
    text: str,
    strategy_name: str,
    params: Dict[str, Any],
    file_path: str | None = None,
) -> List[ChunkResult]:
    """调用工厂创建 Chunker 并执行分块。"""
    from app.services.log_service import emit_log_sync

    t0 = time.perf_counter()
    emit_log_sync(
        f"初始化分块策略: {strategy_name}",
        level=LogLevel.INFO,
        source="chunk_service",
        strategy=strategy_name,
        params=params,
    )

    chunker = ChunkerFactory.create(strategy_name, params)
    results = chunker.chunk(text, file_path=file_path)

    elapsed = int((time.perf_counter() - t0) * 1000)
    emit_log_sync(
        f"分块完成: {strategy_name} → {len(results)} 个块",
        level=LogLevel.SUCCESS,
        source="chunk_service",
        duration_ms=elapsed,
        strategy=strategy_name,
        total_chunks=len(results),
        text_length=len(text),
    )
    return results


async def preview_chunks(
    doc_id: str,
    text: str,
    strategy_name: StrategyName,
    params: Dict[str, Any],
    file_path: str | None = None,
    preview_limit: int = 5,
    cleaning_config: CleaningConfigRequest | None = None,
) -> ChunkPreviewResponse:
    """
    预览分块结果（不落库）。
    返回前 preview_limit 个分块及用于高亮的原文片段。
    """
    await emit_log(
        f"开始预览分块: doc_id={doc_id[:8]}..., 策略={strategy_name}",
        level=LogLevel.INFO,
        source="preview",
        strategy=str(strategy_name),
        text_length=len(text),
    )

    # 可选：分块前先清洗
    text = apply_cleaning(text, cleaning_config)

    all_chunks = run_chunking(text, strategy_name, params, file_path)
    total = len(all_chunks)

    # 根据文档大小动态计算默认预览块数
    # 小文档（≤10 块）：显示全部
    # 中等文档（11~200 块）：显示 50%
    # 大文档（>200 块）：显示 20%，最少 50 块
    if total <= 10:
        auto_limit = total
    elif total <= 200:
        auto_limit = max(10, int(total * 0.5))
    else:
        auto_limit = max(50, int(total * 0.2))

    # 动态计算的预览数不超过前端请求的上限
    limit = min(auto_limit, preview_limit)
    preview_chunks_data = all_chunks[:limit]

    # 构建原文预览片段（覆盖所有预览块的范围）
    if preview_chunks_data:
        preview_end = max(c.char_end for c in preview_chunks_data)
        source_preview = text[:min(preview_end + 200, len(text))]
    else:
        source_preview = text[:2000]

    preview_items = [
        ChunkPreviewItem(
            index=i,
            text=c.text,
            metadata=c.metadata,
            char_start=c.char_start,
            char_end=c.char_end,
        )
        for i, c in enumerate(preview_chunks_data)
    ]

    await emit_log(
        f"预览完成: 共 {len(all_chunks)} 块，展示前 {len(preview_items)} 块",
        level=LogLevel.SUCCESS,
        source="preview",
        total_chunks=len(all_chunks),
        preview_count=len(preview_items),
    )

    return ChunkPreviewResponse(
        doc_id=doc_id,
        strategy_name=strategy_name,
        total_chunks=len(all_chunks),
        preview_chunks=preview_items,
        source_text_preview=source_preview,
    )


async def execute_chunking(
    session: AsyncSession,
    doc_id: str,
    text: str,
    strategy_name: StrategyName,
    params: Dict[str, Any],
    file_path: str | None = None,
    cleaning_config: CleaningConfigRequest | None = None,
) -> ChunkExecuteResponse:
    """
    执行全量分块并写入 PostgreSQL。
    生成 job_id (UUID)，保存所有 chunks 及 metadata。
    """
    t0 = time.perf_counter()
    await emit_log(
        f"开始全量分块: doc_id={doc_id[:8]}..., 策略={strategy_name}",
        level=LogLevel.INFO,
        source="execute",
        strategy=str(strategy_name),
        text_length=len(text),
    )

    # 可选：分块前先清洗
    text = apply_cleaning(text, cleaning_config)

    all_chunks = run_chunking(text, strategy_name, params, file_path)
    job_id = str(uuid.uuid4())

    await emit_log(
        f"创建分块任务: job_id={job_id[:8]}...",
        level=LogLevel.INFO,
        source="execute",
        job_id=job_id,
        total_chunks=len(all_chunks),
    )

    try:
        job = ChunkJob(
            id=job_id,
            doc_id=doc_id,
            strategy_name=strategy_name,
            params=params,
            total_chunks=len(all_chunks),
            status="completed",
        )
        session.add(job)

        # Parent-Child 策略：先保存 parent 块，再关联 child 块
        parent_id_map: Dict[int, uuid.UUID] = {}

        for idx, chunk_result in enumerate(all_chunks):
            parent_id = None
            if chunk_result.parent_index is not None:
                parent_id = parent_id_map.get(chunk_result.parent_index)

            chunk_record = Chunk(
                job_id=job_id,
                doc_id=doc_id,
                chunk_index=idx,
                text=chunk_result.text,
                chunk_metadata=chunk_result.metadata,
                parent_id=parent_id,
                char_start=chunk_result.char_start,
                char_end=chunk_result.char_end,
            )
            session.add(chunk_record)
            await session.flush()

            # 每 50 个块输出一次进度
            if (idx + 1) % 50 == 0:
                await emit_log(
                    f"已写入 {idx + 1}/{len(all_chunks)} 个分块记录",
                    level=LogLevel.DEBUG,
                    source="execute",
                    progress=f"{idx + 1}/{len(all_chunks)}",
                )

            # 记录 parent 块 ID（granularity == parent 或作为 parent_index 的目标）
            if chunk_result.metadata.get("granularity") == "parent":
                parent_id_map[idx] = chunk_record.id

        await session.flush()

        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"全量分块完成: {len(all_chunks)} 个块已入库 (job_id={job_id[:8]}...)",
            level=LogLevel.SUCCESS,
            source="execute",
            duration_ms=elapsed,
            job_id=job_id,
            total_chunks=len(all_chunks),
        )

        return ChunkExecuteResponse(
            job_id=job_id,
            doc_id=doc_id,
            strategy_name=strategy_name,
            total_chunks=len(all_chunks),
            message=f"成功切分 {len(all_chunks)} 个块",
        )
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        await emit_log(
            f"分块入库失败 ({elapsed}ms): {type(e).__name__}: {e}",
            level=LogLevel.ERROR,
            source="execute",
            duration_ms=elapsed,
            error_type=type(e).__name__,
            error=str(e),
        )
        raise

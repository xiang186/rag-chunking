"""文档相关 API 路由。"""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.schemas import (
    ChunkExecuteRequest,
    ChunkExecuteResponse,
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    UploadResponse,
)
from app.services.chunk_service import execute_chunking, preview_chunks
from app.services.document_service import (
    create_document,
    extract_text_from_file,
    get_document,
    save_uploaded_file,
)
from app.services.log_service import emit_log, LogLevel
from app.strategies.factory import ChunkerFactory

router = APIRouter(prefix="/api/documents", tags=["documents"])


class TestEmbeddingRequest(BaseModel):
    """测试 Embedding API 连接请求。"""

    api_key: str
    base_url: str = ""
    model: str = "text-embedding-3-small"


@router.get("/strategies")
async def list_strategies():
    """获取所有可用分块策略及参数 schema。"""
    return ChunkerFactory.list_strategies()


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
):
    """
  上传文档文件，存储至本地，提取文本，返回 doc_id。
  支持 .txt, .md, .pdf 等格式。
  """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    doc_id, file_path, file_size = await save_uploaded_file(file)
    content_text = extract_text_from_file(file_path)

    await create_document(
        session,
        doc_id=doc_id,
        filename=file.filename,
        file_path=file_path,
        file_size=file_size,
        content_text=content_text,
    )

    return UploadResponse(
        doc_id=doc_id,
        filename=file.filename,
        file_size=file_size,
    )


@router.post("/{doc_id}/preview", response_model=ChunkPreviewResponse)
async def preview_document_chunks(
    doc_id: str,
    request: ChunkPreviewRequest,
    session: AsyncSession = Depends(get_db),
):
    """
  预览分块结果（不落库）。
  接收 strategy_name 和 params，返回前 5 个 chunks 用于前端高亮预览。
  """
    doc = await get_document(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")

    if not doc.content_text:
        raise HTTPException(status_code=400, detail="文档内容为空，无法分块")

    return await preview_chunks(
        doc_id=doc_id,
        text=doc.content_text,
        strategy_name=request.strategy_name,
        params=request.params,
        file_path=doc.file_path,
        preview_limit=request.preview_limit,
        cleaning_config=request.cleaning_config,
    )


@router.post("/{doc_id}/execute", response_model=ChunkExecuteResponse)
async def execute_document_chunking(
    doc_id: str,
    request: ChunkExecuteRequest,
    session: AsyncSession = Depends(get_db),
):
    """
  执行全量分块，生成 UUID job_id，将 chunks 和 metadata 存入 PostgreSQL。
  """
    doc = await get_document(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")

    if not doc.content_text:
        raise HTTPException(status_code=400, detail="文档内容为空，无法分块")

    return await execute_chunking(
        session=session,
        doc_id=doc_id,
        text=doc.content_text,
        strategy_name=request.strategy_name,
        params=request.params,
        file_path=doc.file_path,
        cleaning_config=request.cleaning_config,
    )


@router.post("/test-embedding")
async def test_embedding_connection(request: TestEmbeddingRequest):
    """
    测试 Embedding API 连通性。
    用指定的 API Key、Base URL、模型名尝试生成一个短文本的 embedding 向量。
    """
    t0 = time.perf_counter()
    await emit_log(
        "开始测试 Embedding API 连接...",
        level=LogLevel.INFO,
        source="test_embedding",
        model=request.model,
        base_url=request.base_url or "OpenAI 默认",
    )

    try:
        from openai import OpenAI

        client_kwargs = {"api_key": request.api_key}
        if request.base_url:
            client_kwargs["base_url"] = request.base_url

        client = OpenAI(**client_kwargs)

        # 使用 openai 原生客户端直接调用，兼容性更好
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.embeddings.create(
                model=request.model,
                input="Hello, this is a test message for embedding API.",
            ),
        )

        elapsed = int((time.perf_counter() - t0) * 1000)
        vector = response.data[0].embedding
        vector_dim = len(vector)
        preview = f"[{vector[0]:.4f}, {vector[1]:.4f}, ..., {vector[-1]:.4f}]"
        model_used = response.model

        await emit_log(
            f"Embedding API 连接成功! 耗时={elapsed}ms, 模型={model_used}, 向量维度={vector_dim}",
            level=LogLevel.SUCCESS,
            source="test_embedding",
            duration_ms=elapsed,
            model=model_used,
            vector_dim=vector_dim,
        )

        return {
            "success": True,
            "duration_ms": elapsed,
            "vector_dim": vector_dim,
            "vector_preview": preview,
            "model_used": model_used,
            "message": f"连接成功! 耗时 {elapsed}ms，模型 {model_used}，向量维度 {vector_dim}",
        }

    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        error_msg = str(e)

        await emit_log(
            f"Embedding API 连接失败 ({elapsed}ms): {error_msg[:200]}",
            level=LogLevel.ERROR,
            source="test_embedding",
            duration_ms=elapsed,
            error=error_msg[:500],
        )

        # 提取用户友好的错误信息
        if "401" in error_msg or "Unauthorized" in error_msg:
            friendly = "API Key 无效或未授权"
        elif "connection" in error_msg.lower() or "connect" in error_msg.lower():
            friendly = "无法连接到 API 地址，请检查 Base URL 和网络"
        elif "rate" in error_msg.lower() or "quota" in error_msg.lower():
            friendly = "API 调用频率超出限制或额度不足"
        elif "model" in error_msg.lower() and "not found" in error_msg.lower():
            friendly = "指定的模型名称不存在或不可用"
        elif "400" in error_msg and "input" in error_msg:
            friendly = "API 请求格式不兼容，请检查 Base URL 是否为 OpenAI 兼容接口"
        else:
            friendly = error_msg[:200]

        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "duration_ms": elapsed,
                "message": friendly,
                "error": error_msg[:500],
            },
        )

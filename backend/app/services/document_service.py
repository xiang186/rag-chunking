"""文档服务 - 处理文件上传与文本提取。"""

import os
import sys
import time
import uuid
from contextlib import redirect_stderr, nullcontext
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Document
from app.services.log_service import emit_log, emit_log_sync, LogLevel


async def save_uploaded_file(file: UploadFile) -> tuple[str, str, int]:
    """
    保存上传文件到本地存储目录。

    Returns:
        (doc_id, file_path, file_size)
    """
    t0 = time.perf_counter()
    doc_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "upload").name
    dest_path = settings.UPLOAD_DIR / f"{doc_id}_{safe_name}"

    await emit_log(
        f"开始接收文件: {safe_name}",
        level=LogLevel.INFO,
        source="upload",
        filename=safe_name,
    )

    content = await file.read()
    file_size = len(content)
    dest_path.write_bytes(content)

    elapsed = int((time.perf_counter() - t0) * 1000)
    await emit_log(
        f"文件保存完成: {safe_name} ({file_size} 字节)",
        level=LogLevel.SUCCESS,
        source="upload",
        duration_ms=elapsed,
        filename=safe_name,
        file_size=file_size,
    )

    return doc_id, str(dest_path), file_size


def extract_text_from_file(file_path: str) -> str:
    """
    从文件中提取纯文本内容。

    支持 .txt, .md, .pdf 格式，其他格式尝试按文本读取。
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    t0 = time.perf_counter()

    if suffix in (".txt", ".md", ".markdown"):
        emit_log_sync(
            f"提取纯文本: {path.name}",
            level=LogLevel.INFO,
            source="text_extract",
            file_type=suffix,
        )
        result = path.read_text(encoding="utf-8", errors="ignore")
        elapsed = int((time.perf_counter() - t0) * 1000)
        emit_log_sync(
            f"文本提取完成: {len(result)} 字符",
            level=LogLevel.SUCCESS,
            source="text_extract",
            duration_ms=elapsed,
            char_count=len(result),
        )
        return result

    if suffix == ".pdf":
        emit_log_sync(
            f"提取 PDF 文本: {path.name}（使用 unstructured 库）",
            level=LogLevel.INFO,
            source="text_extract",
            file_type="pdf",
        )
        try:
            from unstructured.partition.auto import partition
            # 传默认语言范围避免 langdetect 的 stderr 噪声
            # （langdetect 用 print() 而不是 logging，无法通过 setLevel 抑制）
            with open(os.devnull, "w", encoding="utf-8") as devnull:
                with redirect_stderr(devnull):
                    elements = partition(filename=str(path), languages=["zh", "en"])
            result = "\n\n".join(str(e) for e in elements)
            elapsed = int((time.perf_counter() - t0) * 1000)
            emit_log_sync(
                f"PDF 文本提取完成: {len(result)} 字符, {len(elements)} 个元素",
                level=LogLevel.SUCCESS,
                source="text_extract",
                duration_ms=elapsed,
                char_count=len(result),
                element_count=len(elements),
            )
            return result
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            emit_log_sync(
                f"unstructured 提取失败，回退到原始解码: {e}",
                level=LogLevel.WARN,
                source="text_extract",
                duration_ms=elapsed,
                error=str(e),
            )
            return path.read_bytes().decode("utf-8", errors="ignore")

    emit_log_sync(
        f"尝试按文本读取: {path.name}",
        level=LogLevel.INFO,
        source="text_extract",
        file_type=suffix,
    )
    result = path.read_text(encoding="utf-8", errors="ignore")
    elapsed = int((time.perf_counter() - t0) * 1000)
    emit_log_sync(
        f"文本读取完成: {len(result)} 字符",
        level=LogLevel.SUCCESS,
        source="text_extract",
        duration_ms=elapsed,
        char_count=len(result),
    )
    return result


async def create_document(
    session: AsyncSession,
    doc_id: str,
    filename: str,
    file_path: str,
    file_size: int,
    content_text: str,
) -> Document:
    """创建文档数据库记录。"""
    await emit_log(
        f"写入文档记录: {filename} (doc_id={doc_id[:8]}...)",
        level=LogLevel.DEBUG,
        source="document_db",
    )
    doc = Document(
        id=doc_id,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        content_text=content_text,
    )
    session.add(doc)
    await session.flush()
    await emit_log(
        f"文档记录已入库: doc_id={doc_id[:8]}...",
        level=LogLevel.SUCCESS,
        source="document_db",
    )
    return doc


async def get_document(session: AsyncSession, doc_id: str) -> Document | None:
    """根据 doc_id 查询文档。"""
    from sqlalchemy import select
    result = await session.execute(select(Document).where(Document.id == doc_id))
    return result.scalar_one_or_none()

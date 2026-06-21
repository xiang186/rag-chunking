"""
Markdown 结构分块策略 - MarkdownHeaderTextSplitter

底层原理：
MarkdownHeaderTextSplitter 基于 Markdown 标题层级进行结构化切分：
1. 扫描文本中的 Markdown 标题标记（#, ##, ### 等）
2. 按指定标题层级将文档切分为逻辑段落
3. 每个分块的 metadata 中自动注入标题层级信息，如：
   {"Header 1": "第一章", "Header 2": "1.1 概述"}
4. 可选 strip_headers 参数控制是否从内容中移除标题行

适用场景：Markdown 文档、技术 Wiki、结构化知识库文档。
优势：保留文档层级结构，便于检索时提供上下文。
"""

from typing import Any, List

from langchain_text_splitters import MarkdownHeaderTextSplitter

from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult


class MarkdownStructureChunker(BaseChunker):
    """基于 Markdown 标题层级的结构化分块器。"""

    # 默认跟踪的标题层级
    DEFAULT_HEADERS = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
        ("####", "Header 4"),
    ]

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        strip_headers = bool(self.params.get("strip_headers", False))
        headers_to_split_on = self.params.get("headers", self.DEFAULT_HEADERS)

        emit_log_sync(
            f"Markdown 结构分块: strip_headers={strip_headers}, 标题层级数={len(headers_to_split_on)}",
            level=LogLevel.INFO,
            source="markdown_structure",
            strip_headers=strip_headers,
            header_levels=len(headers_to_split_on),
            text_length=len(text),
        )

        # 创建 Markdown 标题分割器
        splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            strip_headers=strip_headers,
        )

        # split_text 返回 Document 列表，metadata 含标题信息
        documents = splitter.split_text(text)
        results: List[ChunkResult] = []
        search_pos = 0

        for doc in documents:
            chunk_text = doc.page_content
            start, end = self._find_char_positions(text, chunk_text, search_pos)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={
                        "strip_headers": strip_headers,
                        **doc.metadata,
                    },
                    char_start=start,
                    char_end=end,
                )
            )
            search_pos = end

        emit_log_sync(
            f"Markdown 结构分块完成: {len(results)} 个段落块",
            level=LogLevel.DEBUG,
            source="markdown_structure",
            total_chunks=len(results),
        )

        return results

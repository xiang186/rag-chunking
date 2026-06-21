"""
递归字符分块策略 - RecursiveCharacterTextSplitter

底层原理：
LangChain 的 RecursiveCharacterTextSplitter 采用"递归分隔符"策略：
1. 维护一个分隔符优先级列表，默认顺序为 ["\n\n", "\n", " ", ""]
2. 首先尝试用最高优先级分隔符（双换行，即段落边界）切分文本
3. 若切分后的块仍超过 chunk_size，则降级使用下一级分隔符递归切分
4. overlap 参数确保相邻块之间有字符重叠，避免语义在边界处断裂

这种策略适合通用文本，对段落结构有一定感知，但不理解语义。
"""

from typing import Any, Dict, List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult


class RecursiveCharacterChunker(BaseChunker):
    """基于 RecursiveCharacterTextSplitter 的递归字符分块器。"""

    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 50

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        chunk_size = int(self.params.get("chunk_size", self.DEFAULT_CHUNK_SIZE))
        chunk_overlap = int(self.params.get("chunk_overlap", self.DEFAULT_CHUNK_OVERLAP))

        emit_log_sync(
            f"递归字符分块: chunk_size={chunk_size}, overlap={chunk_overlap}, 文本长度={len(text)}",
            level=LogLevel.INFO,
            source="recursive_character",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            text_length=len(text),
        )

        # 创建 LangChain 递归字符分割器
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            # 分隔符优先级：段落 > 行 > 空格 > 字符
            separators=["\n\n", "\n", " ", ""],
        )

        chunks = splitter.split_text(text)
        results: List[ChunkResult] = []
        search_pos = 0

        for chunk_text in chunks:
            start, end = self._find_char_positions(text, chunk_text, search_pos)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
                    char_start=start,
                    char_end=end,
                )
            )
            # 考虑 overlap，搜索位置回退
            search_pos = max(0, end - chunk_overlap)

        emit_log_sync(
            f"递归字符分块完成: 生成 {len(results)} 个块",
            level=LogLevel.DEBUG,
            source="recursive_character",
            total_chunks=len(results),
        )

        return results

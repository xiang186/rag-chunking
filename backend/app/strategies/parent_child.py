"""
父子双粒度分块策略 - Parent-Child Chunking

底层原理：
Parent-Child 是一种双粒度索引策略，广泛应用于现代 RAG 系统（如 LlamaIndex）：
1. Parent Chunk（父块）：较大粒度（如 2000 字符），保留完整上下文
2. Child Chunk（子块）：较小粒度（如 400 字符），用于精确向量检索
3. 检索时先匹配 Child Chunk 的高相似度向量
4. 返回对应的 Parent Chunk 给 LLM，提供更完整的上下文

实现步骤：
1. 用较大 chunk_size 生成 Parent 块
2. 对每个 Parent 块内部用较小 chunk_size 生成 Child 块
3. Child 块的 metadata 中记录 parent_index，建立映射关系
4. 存储时同时保存 Parent 和 Child，检索链路：Child -> Parent -> LLM
"""

from typing import Any, List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult


class ParentChildChunker(BaseChunker):
    """双粒度父子分块器 - 生成 child chunks 并映射到 parent chunks。"""

    DEFAULT_PARENT_CHUNK_SIZE = 2000
    DEFAULT_CHILD_CHUNK_SIZE = 400
    DEFAULT_CHILD_CHUNK_OVERLAP = 50

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        parent_size = int(self.params.get("parent_chunk_size", self.DEFAULT_PARENT_CHUNK_SIZE))
        child_size = int(self.params.get("child_chunk_size", self.DEFAULT_CHILD_CHUNK_SIZE))
        child_overlap = int(self.params.get("child_chunk_overlap", self.DEFAULT_CHILD_CHUNK_OVERLAP))
        return_parents_only = bool(self.params.get("return_parents_only", False))

        emit_log_sync(
            f"父子双粒度分块: parent_size={parent_size}, child_size={child_size}, overlap={child_overlap}",
            level=LogLevel.INFO,
            source="parent_child",
            parent_size=parent_size,
            child_size=child_size,
            child_overlap=child_overlap,
            text_length=len(text),
        )

        # 第一步：生成 Parent 块（大粒度）
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=parent_size,
            chunk_overlap=0,  # Parent 块之间不重叠
        )
        parent_texts = parent_splitter.split_text(text)

        emit_log_sync(
            f"生成 {len(parent_texts)} 个父块",
            level=LogLevel.DEBUG,
            source="parent_child",
            parent_count=len(parent_texts),
        )

        # 第二步：对每个 Parent 生成 Child 块（小粒度）
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=child_size,
            chunk_overlap=child_overlap,
        )

        results: List[ChunkResult] = []
        global_search_pos = 0

        for parent_idx, parent_text in enumerate(parent_texts):
            parent_start, parent_end = self._find_char_positions(text, parent_text, global_search_pos)

            if return_parents_only:
                results.append(
                    ChunkResult(
                        text=parent_text,
                        metadata={
                            "granularity": "parent",
                            "parent_index": parent_idx,
                            "parent_chunk_size": parent_size,
                        },
                        char_start=parent_start,
                        char_end=parent_end,
                    )
                )
            else:
                # 生成 Child 块
                child_texts = child_splitter.split_text(parent_text)
                child_search_pos = 0

                for child_idx, child_text in enumerate(child_texts):
                    child_start_in_parent, child_end_in_parent = self._find_char_positions(
                        parent_text, child_text, child_search_pos
                    )
                    global_start = parent_start + child_start_in_parent
                    global_end = parent_start + child_end_in_parent

                    results.append(
                        ChunkResult(
                            text=child_text,
                            metadata={
                                "granularity": "child",
                                "parent_index": parent_idx,
                                "child_index": child_idx,
                                "parent_text_preview": parent_text[:100] + "...",
                                "child_chunk_size": child_size,
                            },
                            char_start=global_start,
                            char_end=global_end,
                            parent_index=parent_idx,
                        )
                    )
                    child_search_pos = max(0, child_end_in_parent - child_overlap)

            global_search_pos = parent_end

        emit_log_sync(
            f"父子双粒度分块完成: {len(parent_texts)} 父块 → {len(results)} 子块",
            level=LogLevel.DEBUG,
            source="parent_child",
            parent_count=len(parent_texts),
            total_chunks=len(results),
        )

        return results

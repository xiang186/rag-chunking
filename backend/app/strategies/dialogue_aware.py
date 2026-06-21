"""
对话体感知语义分块器 (DialogueAwareSemanticChunker)

专门针对对话体文档（如《被讨厌的勇气》中青年与哲人的辩论）优化。

核心逻辑——"三步走"策略：

步骤 1：角色预切分（Base Unit）
  使用正则表达式，以"青年："或"哲人："等发言标记为界，将文档切成单轮发言。
  每轮包含一个完整的发言（含发言标记和内容）。

步骤 2：构建"对话交换"（Exchange Unit）
  将连续的多轮发言绑定为一个不可分割的"对话交换"语义单元。
  例如，"青年的提问"与"哲人的回答"合为一个 exchange。
  默认每 2 轮组成一个 exchange，奇数轮末尾单独成组。

步骤 3：语义分块（Semantic Chunking）
  计算相邻 exchange 之间的 Embedding 向量余弦相似度。
  如果相似度低于阈值（说明话题转移），则在此处切断。
  相似度高于阈值的相邻 exchange 合并到同一个 chunk 中。

对比直接按单轮计算相似度的优势：
- 避免"青年描述场景（感性）"与"哲人理论拆解（理性）"之间被错误切断
- 以完整的问答对为基本语义单元，保持对话逻辑的完整性
"""

import asyncio
import concurrent.futures
import re
import time
from typing import Any, List, Tuple

import numpy as np
from langchain_community.utils.math import cosine_similarity

from app.services.embedding_service import EmbeddingService, PASSAGE_PREFIX
from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult


# 匹配常见中文对话发言者标记，如：
#   青年：...  哲人：...  用户：...  客服：...
#   张三: ...  assistant: ...  human: ...
SPEAKER_PATTERN = re.compile(
    r"^(?P<speaker>[\u4e00-\u9fff\w\s]{1,20})[：:]\s*",
    re.MULTILINE,
)


class DialogueAwareSemanticChunker(BaseChunker):
    """
    对话体感知语义分块器。

    三步走策略：
    1. 角色预切分 → 2. 构建问答交换对 → 3. 语义相似度合并

    以"对话交换"（如一问一答）为基本语义单元，
    基于 Embedding 相似度判断话题边界，避免在问与答之间切断。
    """

    # 默认相似度阈值（余弦相似度低于此值的 exchange 间视为话题切换）
    DEFAULT_SIMILARITY_THRESHOLD = 0.75
    # 默认每个 exchange 包含的连续轮次数（2 = 一问一答）
    DEFAULT_EXCHANGE_SIZE = 2

    @staticmethod
    def _run_async(coro):
        """
        从同步代码中安全执行异步协程。

        解决 asyncio.run() 在 FastAPI 运行事件循环中抛
        "asyncio.run() cannot be called from a running event loop" 的问题。
        """
        try:
            loop = asyncio.get_running_loop()
            # 已有运行中的事件循环 → 在独立线程的新循环中执行
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # 没有运行中的事件循环
            return asyncio.run(coro)

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        # ── 步骤 1：按发言者标签预切分为轮次 ──
        rounds = self._split_into_rounds(text)

        if len(rounds) <= 1:
            # 仅一轮或无匹配，回退到单块
            return [
                ChunkResult(
                    text=text,
                    metadata={"strategy": "dialogue_aware", "rounds": len(rounds)},
                    char_start=0,
                    char_end=len(text),
                )
            ]

        emit_log_sync(
            f"对话体分块: 检测到 {len(rounds)} 个发言轮次",
            level=LogLevel.INFO,
            source="dialogue_chunker",
            total_rounds=len(rounds),
        )

        # ── 步骤 2：获取参数 ──
        similarity_threshold = float(
            self.params.get("similarity_threshold", self.DEFAULT_SIMILARITY_THRESHOLD)
        )
        exchange_size = int(
            self.params.get("exchange_size", self.DEFAULT_EXCHANGE_SIZE)
        )

        # ── 步骤 2：构建"对话交换"（问答对） ──
        exchanges = self._group_into_exchanges(rounds, exchange_size)
        emit_log_sync(
            f"构建了 {len(exchanges)} 个对话交换（每 {exchange_size} 轮一组）",
            level=LogLevel.INFO,
            source="dialogue_chunker",
            total_exchanges=len(exchanges),
            exchange_size=exchange_size,
        )

        if len(exchanges) <= 1:
            # 仅一个 exchange，直接作为一个块
            return self._build_results_from_exchanges(text, exchanges, similarity_threshold, [])

        # ── 步骤 3：获取 Embedding 并计算 exchange 间相似度 ──
        try:
            api_key = self.params.get("openai_api_key") or None
            base_url = self.params.get("openai_base_url") or None
            model = self.params.get("embedding_model") or None

            embed_service = EmbeddingService(
                base_url=base_url,
                api_key=api_key,
                model=model,
            )

            # 对每个 exchange 进行向量化（每个 exchange 包含多轮完整的对话）
            exchange_texts = [e["full_text"] for e in exchanges]
            vectors = self._run_async(
                embed_service.get_embeddings_batch(exchange_texts, prefix=PASSAGE_PREFIX)
            )

        except Exception as e:
            # Embedding API 不可用时，退化到按 exchange 自然切分
            emit_log_sync(
                f"Embedding API 不可用，回退到按 {exchange_size}轮/组 切分: {e}",
                level=LogLevel.WARN,
                source="dialogue_chunker",
            )
            return self._build_results_from_exchanges(text, exchanges, similarity_threshold, [])

        # ── 步骤 3（续）：计算相邻 exchange 相似度，找到话题断点 ──
        similarities = []
        for i in range(len(vectors) - 1):
            sim = cosine_similarity([vectors[i]], [vectors[i + 1]])[0][0]
            similarities.append(float(sim))

        # 找出相似度低于阈值的断点位置
        break_indices = [
            i for i, sim in enumerate(similarities) if sim < similarity_threshold
        ]

        emit_log_sync(
            f"语义分块完成: {len(exchanges)} 个 exchange → {len(break_indices) + 1} 个语义块（断点: {len(break_indices)} 个）",
            level=LogLevel.DEBUG,
            source="dialogue_chunker",
            total_exchanges=len(exchanges),
            breakpoints=len(break_indices),
        )

        # ── 按断点合并 exchanges 为最终 chunks ──
        return self._build_results_from_exchanges(text, exchanges, similarity_threshold, break_indices)

    # ── 以下为内部辅助方法 ──

    def _group_into_exchanges(self, rounds: List[dict], exchange_size: int = 2) -> List[dict]:
        """
        将连续轮次按 exchange_size 分组为"对话交换"。

        默认每 2 轮为一组（如 青年提问 + 哲人回答 = 一问一答），
        保持问答对的完整性，避免 Embedding 在问与答之间切断。

        Args:
            rounds: _split_into_rounds 的输出。
            exchange_size: 每组包含的轮次数，默认 2。

        Returns:
            每个 exchange 包含：
            - rounds: 包含的轮次列表
            - full_text: 所有轮次的完整文本拼接（含发言标记）
            - speakers: 出现的发言者列表（去重有序）
            - start / end: 在原文中的起止位置
        """
        exchanges = []
        for i in range(0, len(rounds), exchange_size):
            group = rounds[i : i + exchange_size]
            speakers = list(dict.fromkeys(r["speaker"] for r in group if r["speaker"]))
            full_text = "\n\n".join(r["full_text"] for r in group)
            exchanges.append({
                "rounds": group,
                "full_text": full_text,
                "speakers": speakers,
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "round_count": len(group),
            })
        return exchanges

    def _build_results_from_exchanges(
        self,
        text: str,
        exchanges: List[dict],
        similarity_threshold: float,
        break_indices: List[int],
    ) -> List[ChunkResult]:
        """
        根据断点位置合并 exchanges，生成 ChunkResult 列表。

        在断点位置切分，非断点位置的 exchange 合并到同一个 chunk。
        """
        results: List[ChunkResult] = []
        search_pos = 0

        # 按断点分组
        start_idx = 0
        all_break = break_indices + [len(exchanges) - 1]  # 保证最后一段也被输出

        for bp in break_indices:
            group = exchanges[start_idx : bp + 1]
            chunk_text = self._merge_exchanges(group)
            start = text.find(chunk_text, search_pos)
            if start == -1:
                start = search_pos
            end = start + len(chunk_text)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={
                        "strategy": "dialogue_aware",
                        "exchange_count": len(group),
                        "round_count": sum(e["round_count"] for e in group),
                        "speakers": list(dict.fromkeys(
                            s for e in group for s in e["speakers"]
                        )),
                        "similarity_threshold": similarity_threshold,
                    },
                    char_start=start,
                    char_end=end,
                )
            )
            search_pos = end
            start_idx = bp + 1

        # 剩余 exchanges
        if start_idx < len(exchanges):
            group = exchanges[start_idx:]
            chunk_text = self._merge_exchanges(group)
            start = text.find(chunk_text, search_pos)
            if start == -1:
                start = search_pos
            end = start + len(chunk_text)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={
                        "strategy": "dialogue_aware",
                        "exchange_count": len(group),
                        "round_count": sum(e["round_count"] for e in group),
                        "speakers": list(dict.fromkeys(
                            s for e in group for s in e["speakers"]
                        )),
                        "similarity_threshold": similarity_threshold,
                    },
                    char_start=start,
                    char_end=end,
                )
            )

        return results

    @staticmethod
    def _merge_exchanges(group: List[dict]) -> str:
        """将一组 exchange 合并为一个文本块。"""
        return "\n\n".join(e["full_text"] for e in group)

    def _split_into_rounds(self, text: str) -> List[dict]:
        """
        按发言者标签将文本切分为发言轮次列表。

        每轮包含：
        - speaker: 发言者名称
        - text: 该轮发言文本（不含发言标记）
        - full_text: 原始文本片段（含发言标记）
        - start / end: 在原文中的起止位置
        """
        rounds = []
        # 找到所有发言标记的位置
        matches = list(SPEAKER_PATTERN.finditer(text))
        if not matches:
            # 没有匹配到发言者标记，整个文本作为一个轮次
            return [{"speaker": "", "text": text, "full_text": text, "start": 0, "end": len(text)}]

        # 从第一个发言标记开始划分
        for i, match in enumerate(matches):
            start = match.start()
            speaker = match.group("speaker").strip()

            # 该轮内容的结束位置：下一个发言标记开始，或文本末尾
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(text)

            full_text = text[start:end].strip()
            # 跳过发言标记本身，获取发言内容
            content_start = match.end()
            content = text[content_start:end].strip()

            rounds.append({
                "speaker": speaker,
                "text": content,
                "full_text": full_text,
                "start": start,
                "end": end,
            })

        return rounds

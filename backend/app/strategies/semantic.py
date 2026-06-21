"""
语义分块策略 - 基于 Embedding 向量相似度的语义切分

底层原理：
1. 将文本按句子边界（。！？\n 等）切分为句子
2. 对每个句子及其上下文窗口（buffer_size）计算 Embedding 向量
3. 计算相邻句子之间的余弦距离（1 - cosine_similarity）
4. 根据距离分布的统计特征计算动态阈值：
   - percentile: 取距离分布的 P 分位数（默认 95，即取最大的 5% 距离）
   - standard_deviation: 均值 + N * 标准差（默认 3，取极端异常值）
   - interquartile: 均值 + N * 四分位距（默认 1.5）
5. 距离超过阈值的位置视为语义断点

适用场景：长文档、技术文档，需要保持语义完整性的场景。
"""

import time
import re
from typing import Any, List

import numpy as np
from langchain_core.embeddings import Embeddings
from langchain_community.utils.math import cosine_similarity

from app.config import settings
from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult

# 支持中英文的句子分隔符正则
SENTENCE_SPLIT_RE = r"(?<=[。！？.!?\n])\s*"


class _OpenAICompatibleEmbeddings(Embeddings):
    """
    基于原生 openai 客户端的 Embeddings 包装器。

    替换 langchain_openai.OpenAIEmbeddings，解决其对非 OpenAI API
    请求格式兼容性差的问题。
    """

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def _embed(self, texts: List[str]) -> List[List[float]]:
        from openai import OpenAI

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = OpenAI(**client_kwargs)
        response = client.embeddings.create(model=self.model, input=texts)
        sorted_data = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in sorted_data]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text])[0]


def _split_sentences(text: str) -> list[dict]:
    """按中英文句子边界切分，返回句子及其在原文中的起止位置。"""
    parts = re.split(SENTENCE_SPLIT_RE, text)
    sentences = []
    cursor = 0
    for p in parts:
        p_stripped = p.strip()
        if not p_stripped:
            cursor += len(p)
            continue
        # 在原文中找到这个句子的精确范围
        start = text.find(p_stripped, cursor)
        if start == -1:
            start = cursor
        end = start + len(p_stripped)
        sentences.append({
            "text": p_stripped,
            "start": start,
            "end": end,
        })
        cursor = end
    return sentences


def _combine_sentences(
    sentences: List[dict], buffer_size: int = 1
) -> List[dict]:
    """对每个句子，合并前后 buffer_size 个句子作为上下文窗口。"""
    for i in range(len(sentences)):
        combined = ""
        for j in range(i - buffer_size, i + buffer_size + 1):
            if 0 <= j < len(sentences):
                if combined:
                    combined += " "
                combined += sentences[j]["text"]
        sentences[i]["combined_sentence"] = combined
    return sentences


def _compute_distances(
    sentences: List[dict], embeddings: Embeddings
) -> tuple[List[float], List[dict]]:
    """计算相邻句子的余弦距离，并注入到 sentences 列表中。"""
    texts = [s["combined_sentence"] for s in sentences]
    vectors = embeddings.embed_documents(texts)

    for i, (s, v) in enumerate(zip(sentences, vectors)):
        s["combined_sentence_embedding"] = v

    distances: List[float] = []
    for i in range(len(sentences) - 1):
        sim = cosine_similarity(
            [sentences[i]["combined_sentence_embedding"]],
            [sentences[i + 1]["combined_sentence_embedding"]],
        )[0][0]
        dist = 1.0 - float(sim)
        distances.append(dist)
        sentences[i]["distance_to_next"] = dist

    return distances, sentences


def _calc_threshold(
    distances: List[float],
    method: str,
    amount: float,
) -> float:
    """根据指定方法和参数计算断点阈值。"""
    if not distances:
        return 0.0

    arr = np.array(distances)

    if method == "percentile":
        # amount 已在 0-100 范围内（如 95 = 第95百分位）
        return float(np.percentile(arr, amount))
    elif method == "standard_deviation":
        return float(np.mean(arr) + amount * np.std(arr))
    elif method == "interquartile":
        q1, q3 = np.percentile(arr, [25, 75])
        iqr = q3 - q1
        return float(np.mean(arr) + amount * iqr)
    else:
        raise ValueError(f"不支持的断点类型: {method}")


def _semantic_chunk(
    text: str,
    embeddings: Embeddings,
    threshold: float = 95.0,
    method: str = "percentile",
    buffer_size: int = 1,
) -> List[str]:
    """完整语义切分流程。"""
    raw_sentences = _split_sentences(text)
    if not raw_sentences:
        return [text]

    if len(raw_sentences) <= 3:
        # 句子太少，无需语义切分
        return [text]

    # 转成带索引的 dict（已包含原文位置）
    sents = [{"text": s["text"], "start": s["start"], "end": s["end"], "index": i}
             for i, s in enumerate(raw_sentences)]

    # 合并上下文窗口
    sents = _combine_sentences(sents, buffer_size)

    # 计算 Embedding 和距离
    distances, sents = _compute_distances(sents, embeddings)

    # 计算断点阈值
    breakpoint = _calc_threshold(distances, method, threshold)

    # 找到所有断点位置（句子的 index）
    indices = [i for i, d in enumerate(distances) if d > breakpoint]

    # 按断点切分，直接使用原文片段，保证与原始文本精确一致
    chunks: List[str] = []
    seg_start = 0
    for idx in indices:
        seg_end = sents[idx]["end"]
        chunks.append(text[seg_start:seg_end])
        seg_start = seg_end
    if seg_start < len(text):
        chunks.append(text[seg_start:])

    return chunks


class SemanticChunker(BaseChunker):
    """基于 Embedding 相似度的语义分块器。"""

    # 注意：percentile 方法中 threshold 应在 0-100 范围内
    # 如 95 表示取第 95 百分位（最大的 5% 距离值）
    DEFAULT_THRESHOLD = 95.0
    DEFAULT_BREAKPOINT_TYPE = "percentile"
    DEFAULT_BUFFER_SIZE = 1

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        threshold = float(
            self.params.get("breakpoint_threshold", self.DEFAULT_THRESHOLD)
        )
        breakpoint_type = self.params.get(
            "breakpoint_type", self.DEFAULT_BREAKPOINT_TYPE
        )
        buffer_size = int(
            self.params.get("buffer_size", self.DEFAULT_BUFFER_SIZE)
        )

        # 从前端传入的自定义参数中获取 API 配置
        api_key = self.params.get("openai_api_key") or settings.OPENAI_API_KEY
        base_url = (
            self.params.get("openai_base_url")
            or settings.OPENAI_BASE_URL
            or None
        )
        model = (
            self.params.get("embedding_model") or settings.EMBEDDING_MODEL
        )

        emit_log_sync(
            f"语义分块: threshold={threshold}, type={breakpoint_type}, model={model}, 文本长度={len(text)}",
            level=LogLevel.INFO,
            source="semantic",
            threshold=threshold,
            breakpoint_type=breakpoint_type,
            model=model,
            text_length=len(text),
        )

        # 检查 API Key 是否已配置
        if not api_key:
            emit_log_sync(
                "未配置 API Key！语义分块需要调用 Embedding API。"
                "请在页面参数中填入或在 .env 文件中设置 OPENAI_API_KEY。",
                level=LogLevel.ERROR,
                source="semantic",
            )
            raise RuntimeError(
                "语义分块失败：未配置 API Key。"
                "请在页面的「参数配置」中填入 API Key，"
                "或在后端 .env 文件中设置 OPENAI_API_KEY。"
            )

        # 初始化 OpenAI Embedding 模型
        emit_log_sync(
            f"初始化 Embedding 模型: model={model}, base_url={base_url or 'OpenAI 默认'}",
            level=LogLevel.DEBUG,
            source="semantic",
            model=model,
            base_url=base_url,
        )

        embeddings = _OpenAICompatibleEmbeddings(
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

        emit_log_sync(
            f"正在计算 Embedding 向量并检测语义断点...（首次调用可能较慢）",
            level=LogLevel.INFO,
            source="semantic",
        )

        t0 = time.perf_counter()
        try:
            # 使用自定义语义切分实现，支持中文标点、正确处理百分位阈值
            chunk_texts = _semantic_chunk(
                text,
                embeddings=embeddings,
                threshold=threshold,
                method=breakpoint_type,
                buffer_size=buffer_size,
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            emit_log_sync(
                f"Embedding API 调用失败 ({elapsed}ms): {e}",
                level=LogLevel.ERROR,
                source="semantic",
                duration_ms=elapsed,
                error=str(e),
            )
            raise RuntimeError(
                f"语义分块失败：无法连接到 Embedding API。"
                f"\n请检查：1) API Key 和 Base URL 是否正确；"
                f"2) 网络是否可达。\n原始错误：{e}"
            ) from e

        elapsed = int((time.perf_counter() - t0) * 1000)
        emit_log_sync(
            f"Embedding 计算完成，耗时 {elapsed}ms，共 {len(chunk_texts)} 个语义段",
            level=LogLevel.INFO,
            source="semantic",
            duration_ms=elapsed,
            total_chunks=len(chunk_texts),
        )

        results: List[ChunkResult] = []
        search_pos = 0

        for ci, chunk_text in enumerate(chunk_texts):
            start, end = self._find_char_positions(text, chunk_text, search_pos)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={
                        "breakpoint_threshold": threshold,
                        "breakpoint_type": breakpoint_type,
                        "embedding_time_ms": elapsed,
                    },
                    char_start=start,
                    char_end=end,
                )
            )
            search_pos = end

        emit_log_sync(
            f"语义分块完成: 检测到 {len(results)} 个语义段",
            level=LogLevel.DEBUG,
            source="semantic",
            total_chunks=len(results),
        )

        return results

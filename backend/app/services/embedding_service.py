"""
Embedding 服务集成 - 针对本地 llama-server / gte-Qwen2 优化

封装本地 Embedding API 调用，提供：
1. 自动前缀：入库文本加 "passage: "，查询文本加 "query: "（gte-Qwen2 规范）
2. 批量处理：支持一次性传入文本列表，严禁 for 循环单条请求
3. 重试机制：指数退避重试，应对瞬时故障
4. 超时控制：可配置超时时间
"""

import asyncio
import time
from typing import List, Optional, Tuple

from openai import AsyncOpenAI

from app.config import settings

# gte-Qwen2 规范：检索任务需要为 passage 和 query 添加不同前缀
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "

# 默认重试配置
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 60  # 秒
DEFAULT_BATCH_SIZE = 32  # 单次 API 调用最大批处理数量


class EmbeddingService:
    """
    本地 Embedding 服务封装。

    用法:
        svc = EmbeddingService()
        # 批量入库
        vectors = await svc.get_embeddings_batch(["text1", "text2"])
        # 单条查询
        vector = await svc.get_query_embedding("查询文本")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = DEFAULT_TIMEOUT,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        self.base_url = base_url or settings.OPENAI_BASE_URL or "http://localhost:8080/v1"
        self.api_key = api_key or settings.OPENAI_API_KEY or "not-needed"
        self.model = model or settings.EMBEDDING_MODEL or "gte-Qwen2"
        self.max_retries = max_retries
        self.timeout = timeout
        self.batch_size = batch_size

        # 创建异步 OpenAI 客户端
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    async def get_embeddings_batch(
        self, texts: List[str], prefix: str = PASSAGE_PREFIX
    ) -> List[List[float]]:
        """
        批量获取文本的 Embedding 向量。

        Args:
            texts: 待向量化的文本列表。
            prefix: 文本前缀，默认为 PASSAGE_PREFIX ("passage: ")。

        Returns:
            List[List[float]]: embedding 向量列表，顺序与输入一致。

        Raises:
            RuntimeError: 多次重试后仍然失败时抛出。
        """
        if not texts:
            return []

        # 添加前缀
        prefixed = [f"{prefix}{t}" for t in texts]

        # 按 batch_size 分批处理
        all_vectors: List[List[float]] = []
        for i in range(0, len(prefixed), self.batch_size):
            batch = prefixed[i : i + self.batch_size]
            vectors = await self._call_with_retry(batch)
            all_vectors.extend(vectors)

        return all_vectors

    async def get_query_embedding(self, text: str) -> List[float]:
        """
        获取单条查询文本的 Embedding 向量。

        Args:
            text: 查询文本。

        Returns:
            List[float]: embedding 向量。
        """
        result = await self.get_embeddings_batch([text], prefix=QUERY_PREFIX)
        return result[0] if result else []

    async def _call_with_retry(
        self, texts: List[str], attempt: int = 1
    ) -> List[List[float]]:
        """
        带指数退避重试的 Embedding API 调用。

        Args:
            texts: 已加前缀的文本列表。
            attempt: 当前重试次数（内部递归用）。

        Returns:
            List[List[float]]: embedding 向量列表。
        """
        try:
            response = await self._client.embeddings.create(
                model=self.model,
                input=texts,
            )
            # 按 input 顺序排序并返回
            sorted_data = sorted(response.data, key=lambda d: d.index)
            return [d.embedding for d in sorted_data]

        except Exception as e:
            if attempt >= self.max_retries:
                raise RuntimeError(
                    f"Embedding API 调用失败（已重试 {self.max_retries} 次）: {e}"
                ) from e

            # 指数退避：1s → 2s → 4s → ...
            wait = 2 ** (attempt - 1)
            await asyncio.sleep(wait)
            return await self._call_with_retry(texts, attempt + 1)

    async def health_check(self) -> Tuple[bool, str]:
        """
        检查 Embedding 服务是否可用。

        Returns:
            (是否可用, 诊断消息)
        """
        try:
            t0 = time.perf_counter()
            await self._call_with_retry(["ping"], attempt=1)
            elapsed = int((time.perf_counter() - t0) * 1000)
            return True, f"Embedding 服务响应正常 ({elapsed}ms)"
        except Exception as e:
            return False, f"Embedding 服务不可用: {e}"

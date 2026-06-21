"""分块策略基类 - 定义所有 Chunker 的统一接口。"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChunkResult:
    """
    分块结果数据类。

    每个 ChunkResult 代表一个切分后的文本块，包含：
    - text: 分块文本内容
    - metadata: 附加元数据（如 Markdown 标题层级、父块 ID 等）
    - char_start/char_end: 在原文中的字符偏移，用于前端高亮预览
    - parent_index: ParentChild 策略中 child 块对应的 parent 索引
    """

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    char_start: int = 0
    char_end: int = 0
    parent_index: Optional[int] = None


class BaseChunker(ABC):
    """
    分块策略抽象基类（Strategy Pattern）。

    所有具体分块策略必须继承此类并实现 chunk() 方法。
    工厂类 ChunkerFactory 根据 strategy_name 实例化对应策略。
    """

    def __init__(self, params: Dict[str, Any] | None = None):
        """
        初始化分块器。

        Args:
            params: 策略参数字典，如 chunk_size、overlap、threshold 等。
        """
        self.params = params or {}

    @abstractmethod
    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        """
        对输入文本执行分块。

        Args:
            text: 待分块的原始文本。
            **kwargs: 额外参数（如文件路径，用于 PDF 版面分析）。

        Returns:
            分块结果列表。
        """
        ...

    @staticmethod
    def _find_char_positions(text: str, chunk_text: str, search_start: int = 0) -> tuple[int, int]:
        """
        在原文中定位分块文本的起止字符位置。

        LangChain Splitter 通常不返回偏移量，且常常对空白字符做归一化
        （如 MarkdownHeaderTextSplitter 将 \n\n 转换为  \n），此方法
        通过多级回退策略查找每个 chunk 在原文中的位置。

        匹配策略：
        1. 精确字符串匹配
        2. 基于单词的 \s+ 正则匹配（处理空白归一化）
        3. 基于 search_start 的长度近似
        """
        # 1. 精确匹配
        start = text.find(chunk_text, search_start)
        if start != -1:
            return start, start + len(chunk_text)

        # 2. 空白弹性匹配：用 \s+ 连接所有单词，处理各类空白归一化差异
        words = chunk_text.split()
        if words:
            pattern = r'\s+'.join(re.escape(w) for w in words)
            match = re.search(pattern, text[search_start:])
            if match:
                s = search_start + match.start()
                e = search_start + match.end()
                return s, e

        # 3. 最终回退
        return search_start, search_start + len(chunk_text)

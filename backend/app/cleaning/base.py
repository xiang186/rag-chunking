"""
数据清洗策略基类 - 采用策略模式 (Strategy Pattern)

每个清洗器独立实现一种清洗能力，通过 CleaningPipeline 管道模式动态组装。
所有清洗器继承自 BaseCleaningStrategy，通过 process() 方法对外暴露。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CleaningResult:
    """
    清洗结果数据类。

    Attributes:
        text: 清洗后的文本
        cleaned: 是否发生了实际修改
        changes: 变更记录列表（每项包含操作类型和位置/内容描述）
        metadata: 清洗过程产生的附加元数据（如脱敏计数）
    """
    text: str
    cleaned: bool = False
    changes: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseCleaningStrategy(ABC):
    """
    清洗策略抽象基类。

    所有具体清洗策略必须继承此类并实现 process() 方法。
    CleaningPipeline 会根据配置字典动态实例化并串联执行。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化清洗器。

        Args:
            config: 可选配置字典，如 {"presidio": false} 控制是否使用 presidio 库。
        """
        self.config = config or {}

    @abstractmethod
    def process(self, text: str) -> CleaningResult:
        """
        对输入文本执行清洗。

        Args:
            text: 待清洗的原始文本。

        Returns:
            CleaningResult: 清洗结果，包含清洗后文本和变更记录。
        """
        ...

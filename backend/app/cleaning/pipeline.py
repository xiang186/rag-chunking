"""
清洗管道 (CleaningPipeline) - 管道模式实现

根据前端传入的配置字典，动态组装并执行清洗链。
每个清洗器按注册顺序依次执行，上一个的输出作为下一个的输入。

支持的配置格式：
{
    "enable_heuristic": true,        # 启用启发式清洗 (ftfy + 空白标准化)
    "enable_layout": false,          # 启用版面感知清洗 (需 file_path)
    "enable_pii": true,              # 启用隐私脱敏
    "enable_semantic_filter": true,  # 启用语义过滤
    "layout_backend": "unstructured", # 版面分析后端
    "use_presidio": false,           # 使用 presidio NLP 引擎
    "custom_filter_rules": [...],    # 自定义过滤规则
    "file_path": "/path/to/file.pdf" # 版面分析所需文件路径
}
"""

from typing import Any, Dict, List, Optional

from app.cleaning.base import BaseCleaningStrategy, CleaningResult
from app.cleaning.heuristic import HeuristicCleaner
from app.cleaning.layout import LayoutAwareCleaner
from app.cleaning.pii import PIIRedactionCleaner
from app.cleaning.semantic_filter import SemanticFilterCleaner

# 清洗器注册表：配置键名 -> (清洗器类, 描述)
CLEANER_REGISTRY: Dict[str, tuple[type[BaseCleaningStrategy], str]] = {
    "enable_heuristic": (HeuristicCleaner, "启发式清洗（乱码修复 + 空白标准化）"),
    "enable_layout": (LayoutAwareCleaner, "版面感知清洗（PDF 多栏/页眉页脚）"),
    "enable_pii": (PIIRedactionCleaner, "PII 隐私脱敏（手机号/邮箱/身份证）"),
    "enable_semantic_filter": (SemanticFilterCleaner, "语义过滤（免责声明/页码/版权）"),
}


class CleaningPipeline:
    """
    清洗管道 - 管道模式 (Pipeline Pattern)

    根据配置字典动态组装并顺序执行清洗链。

    用法:
        pipeline = CleaningPipeline(config={
            "enable_heuristic": True,
            "enable_pii": True,
            "file_path": "/path/to/doc.pdf",
        })
        result = pipeline.run("原始文本...")
        # result.text 为清洗后的文本
        # result.changes 为所有变更记录
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化清洗管道。

        Args:
            config: 前端传入的配置字典。None 时所有清洗器默认关闭。
        """
        self.config = config or {}

    def run(self, text: str) -> CleaningResult:
        """
        执行整条清洗链。

        按 CLEANER_REGISTRY 中定义的顺序依次执行每个启用的清洗器。
        上一个清洗器的输出文本自动传给下一个清洗器。

        Args:
            text: 待清洗的原始文本。

        Returns:
            CleaningResult: 整条管道的清洗结果。changes 包含所有清洗器的变更记录。
        """
        if not text:
            return CleaningResult(text=text, cleaned=False, changes=[], metadata={})

        all_changes: List[Dict[str, Any]] = []
        all_metadata: Dict[str, Any] = {}
        current_text = text

        # 在注册表顺序中遍历，检查每个清洗器是否启用
        for config_key, (cleaner_cls, description) in CLEANER_REGISTRY.items():
            if not self.config.get(config_key, False):
                continue

            # 为每个清洗器准备配置（传入全局配置中的相关参数）
            cleaner_config = {}
            if config_key == "enable_layout":
                cleaner_config["layout_backend"] = self.config.get("layout_backend", "unstructured")
                cleaner_config["file_path"] = self.config.get("file_path")
            elif config_key == "enable_pii":
                cleaner_config["use_presidio"] = self.config.get("use_presidio", False)
            elif config_key == "enable_semantic_filter":
                cleaner_config["custom_rules"] = self.config.get("custom_filter_rules", [])

            # 实例化并执行
            cleaner = cleaner_cls(config=cleaner_config)
            step_result = cleaner.process(current_text)

            # 记录变更
            all_changes.extend(
                {"stage": description, **c} for c in step_result.changes
            )
            all_metadata[config_key] = step_result.metadata
            current_text = step_result.text

        return CleaningResult(
            text=current_text,
            cleaned=any(c.get("op") not in ("skip",) for c in all_changes),
            changes=all_changes,
            metadata={
                "pipeline_stages": list(
                    CLEANER_REGISTRY[k][1] for k in CLEANER_REGISTRY if self.config.get(k)
                ),
                **all_metadata,
            },
        )

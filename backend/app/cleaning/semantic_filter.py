"""
语义清洗器 (SemanticFilterCleaner) - 去除页眉页脚、免责声明等无意义文本

基于规则 + 可扩展关键词库，识别并移除文档中常见的非正文元素。
不同于 LayoutAwareCleaner（按版面结构区分），此清洗器纯粹基于
文本内容模式匹配，适用于已经提取为纯文本的文档。

适用场景：
- 页眉页脚文字（如 "第 1 页 / 共 20 页"）
- 法律免责声明模板
- 公司保密标记
- 重复出现的章节标题
"""

import re
from typing import Any, Dict, List, Tuple

from app.cleaning.base import BaseCleaningStrategy, CleaningResult

# ── 默认过滤规则：页眉页脚/免责声明等 ──
#
# 每条规则是一个 (pattern, description) 二元组。
# pattern 为正则模式，description 用于日志记录。
DEFAULT_FILTER_RULES: List[Tuple[str, str]] = [
    # 页码行
    (r"^[\s]*第\s*\d+\s*页[\s/]+共\s*\d+\s*页[\s]*$", "页眉页脚-页码"),
    (r"^[\s]*Page\s*\d+\s*of\s*\d+[\s]*$", "页眉页脚-页码英文"),
    (r"^[\s]*\d+\s*/\s*\d+[\s]*$", "页眉页脚-简写页码"),

    # 免责声明
    (r"^[\s]*免责[声明责].*?[:：].*$", "免责声明"),
    (r"^[\s]*[Dd]isclaimer[:：].*$", "免责声明英文"),
    (r"^[\s]*[Nn]otice[:：].*$", "通知声明"),

    # 保密标记
    (r"^[\s]*[机绝内秘].*?[文件资料].*$", "保密标记"),
    (r"^[\s]*[Cc]onfidential.*$", "保密标记英文"),

    # 版权声明
    (r"^[\s]*©.*$", "版权声明"),
    (r"^[\s]*[Cc]opyright.*$", "版权声明英文"),

    # 常见页脚：公司名称/网址
    (r"^[\s]*[wW][wW][wW]\..*\..*$", "网址页脚"),

    # 长分隔线
    (r"^[\s]*[-_＝=]{5,}[\s]*$", "装饰分隔线"),
]


class SemanticFilterCleaner(BaseCleaningStrategy):
    """
    语义清洗：去除页眉页脚、免责声明等无意义文本。

    支持通过 config["custom_rules"] 传入额外规则，
    每条规则为 (正则模式, 描述) 二元组。
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        # 合并默认规则和用户自定义规则
        raw_custom: list = self.config.get("custom_rules", [])
        custom_rules = []
        for rule in raw_custom:
            if isinstance(rule, (list, tuple)) and len(rule) == 2:
                custom_rules.append((rule[0], rule[1]))
            elif isinstance(rule, str):
                custom_rules.append((rule, f"自定义规则: {rule[:30]}"))
            else:
                custom_rules.append((str(rule), "自定义规则"))
        self.rules = DEFAULT_FILTER_RULES + custom_rules
        # 预编译所有正则
        self._compiled = [(re.compile(p, re.MULTILINE), desc) for p, desc in self.rules]

    def process(self, text: str) -> CleaningResult:
        changes: list[Dict[str, Any]] = []
        result = text

        for pattern, desc in self._compiled:
            matches = list(pattern.finditer(result))
            if not matches:
                continue

            # 倒序移除（保持行号不变）
            for m in reversed(matches):
                line = result[m.start():m.end()].strip()
                result = result[:m.start()] + result[m.end():]

            changes.append({
                "op": "semantic_filter",
                "rule": desc,
                "count": len(matches),
                "detail": f"匹配规则「{desc}」移除了 {len(matches)} 行: {line[:40]}...",
            })

        # 清理移除后产生的多余空行
        original_after = result
        result = re.sub(r"\n{3,}", "\n\n", result)
        if result != original_after:
            changes.append({
                "op": "cleanup_blank_lines",
                "detail": "清理了移除空白行后多余的空行",
            })

        cleaned = len(changes) > 0
        return CleaningResult(
            text=result,
            cleaned=cleaned,
            changes=changes,
            metadata={"rules_matched": len(changes), "rules": [c["rule"] for c in changes if "rule" in c]},
        )

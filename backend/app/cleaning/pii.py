"""
隐私脱敏清洗器 (PIIRedactionCleaner) - 掩码手机号、邮箱、身份证号

使用正则表达式匹配常见个人身份信息 (PII) 模式，
可选的 presidio 引擎提供 NLP 增强的实体识别能力。

当 presidio 可用时，优先使用 presidio 进行
基于命名实体识别 (NER) 的脱敏，提高准确率。

依赖: pip install presidio-analyzer presidio-anonymizer
"""

import re
from typing import Any, Dict, List

from app.cleaning.base import BaseCleaningStrategy, CleaningResult


# 正则模式定义：匹配中国大陆常见 PII
PII_PATTERNS: List[Dict[str, Any]] = [
    {
        "name": "chinese_phone",
        "label": "手机号",
        # 用 (?<!\d)/(?!\d) 替代 \b，避免中文语境下 \b 不生效（Python re 的 \w 含 CJK 字符）
        "pattern": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "mask": lambda m: m.group()[:3] + "****" + m.group()[-4:],
    },
    {
        "name": "chinese_landline",
        "label": "固定电话",
        "pattern": re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)"),
        "mask": lambda m: m.group()[:4] + "****" + m.group()[-4:],
    },
    {
        "name": "email",
        "label": "邮箱",
        "pattern": re.compile(r"\b[\w.-]+@[\w.-]+\.\w{2,4}\b"),
        "mask": lambda m: m.group()[0] + "***@" + m.group().split("@")[1],
    },
    {
        "name": "id_card",
        "label": "身份证号",
        "pattern": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "mask": lambda m: m.group()[:6] + "********" + m.group()[-4:],
    },
    {
        "name": "chinese_name",
        "label": "中文姓名",
        # 匹配常见中文人名模式（2-4字中文 + 空格或边界）
        "pattern": re.compile(r"(?<=[\s\n\t])[\u4e00-\u9fff]{2,4}(?=[\s\n\t])"),
        "mask": lambda m: m.group()[0] + "XX",
    },
]


class PIIRedactionCleaner(BaseCleaningStrategy):
    """
    隐私脱敏清洗：掩码手机号、邮箱、身份证等 PII。
    支持正则模式匹配和可选的 presidio NLP 引擎。
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.use_presidio = self.config.get("use_presidio", False)
        self._presidio_analyzer = None
        self._presidio_anonymizer = None

    def process(self, text: str) -> CleaningResult:
        changes: list[Dict[str, Any]] = []
        result = text

        # ── 优先使用 presidio NLP 引擎 ──
        if self.use_presidio:
            result, presidio_changes = self._redact_with_presidio(result)
            changes.extend(presidio_changes)

        # ── 正则模式脱敏（始终执行） ──
        for pii in PII_PATTERNS:
            matches = list(pii["pattern"].finditer(result))
            if matches:
                # 倒序替换以避免偏移问题
                for m in reversed(matches):
                    masked = pii["mask"](m)
                    result = result[:m.start()] + masked + result[m.end():]
                changes.append({
                    "op": "pii_mask",
                    "pii_type": pii["label"],
                    "count": len(matches),
                    "detail": f"掩码了 {len(matches)} 个{pii['label']}",
                })

        cleaned = len(changes) > 0
        return CleaningResult(
            text=result,
            cleaned=cleaned,
            changes=changes,
            metadata={
                "pii_types_masked": list(set(c["pii_type"] for c in changes if "pii_type" in c)),
                "total_masked": sum(c.get("count", 0) for c in changes if "count" in c),
            },
        )

    def _redact_with_presidio(self, text: str) -> tuple[str, list]:
        """使用 Microsoft Presidio 进行 NLP 增强的 PII 识别和脱敏。"""
        changes = []
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import SpacyNlpEngine
            from presidio_anonymizer import AnonymizerEngine
            from presidio_anonymizer.entities import OperatorConfig

            if self._presidio_analyzer is None:
                # 使用中文 spaCy 模型，识别中文人名/组织/地名等实体
                nlp_engine = SpacyNlpEngine(
                    models=[{"lang_code": "zh", "model_name": "zh_core_web_sm"}]
                )
                self._presidio_analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
                self._presidio_anonymizer = AnonymizerEngine()

            analyzer_results = self._presidio_analyzer.analyze(
                text=text,
                language="zh",
            )
            if analyzer_results:
                anonymized_result = self._presidio_anonymizer.anonymize(
                    text=text,
                    analyzer_results=analyzer_results,
                    operators={
                        "DEFAULT": OperatorConfig("replace", {"new_value": "<PII>"}),
                    },
                )
                changes.append({
                    "op": "presidio_redact",
                    "count": len(analyzer_results),
                    "detail": f"Presidio 识别并脱敏了 {len(analyzer_results)} 个实体",
                })
                return anonymized_result.text, changes
        except ImportError:
            changes.append({
                "op": "skip",
                "detail": "presidio 未安装，跳过 NLP 引擎。如需启用请: pip install presidio-analyzer presidio-anonymizer",
            })
        except Exception as e:
            changes.append({
                "op": "skip",
                "detail": f"Presidio 增强识别不可用: {str(e)[:80]}，回退到正则模式",
            })

        return text, changes

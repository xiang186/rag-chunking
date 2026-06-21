"""
启发式清洗器 (HeuristicCleaner) - 修复乱码、标准化空白符

使用 ftfy 库修复常见文本编码问题（如 mojibake 乱码），
同时用正则表达式标准化多余空白符、空行、制表符等。

依赖: pip install ftfy
"""

import re
from typing import Any, Dict

from app.cleaning.base import BaseCleaningStrategy, CleaningResult


class HeuristicCleaner(BaseCleaningStrategy):
    """启发式清洗：ftfy 乱码修复 + 正则空白标准化。"""

    def process(self, text: str) -> CleaningResult:
        changes: list[Dict[str, Any]] = []
        result = text

        # ── 阶段 1：ftfy 修复编码问题 ──
        try:
            import ftfy

            fixed = ftfy.fix_text(result)
            # 内容确实变化时才记录（避免 ftfy 返回同内容新对象导致的误报）
            if fixed != result:
                src_len = len(result)
                result = fixed
                dst_len = len(result)
                diff = src_len - dst_len
                if diff > 0:
                    changes.append({
                        "op": "ftfy_fix",
                        "detail": f"ftfy 修复了 {diff} 个编码问题字符，长度: {src_len} -> {dst_len}",
                    })
                elif diff < 0:
                    changes.append({
                        "op": "ftfy_fix",
                        "detail": f"ftfy 扩展了 {-diff} 个字符（如连字→单字），长度: {src_len} -> {dst_len}",
                    })
                else:
                    changes.append({
                        "op": "ftfy_fix",
                        "detail": "ftfy 执行了字符规范化（如引号/连字标准化），文本长度未变",
                    })
        except ImportError:
            # ftfy 未安装时静默跳过，不影响管道执行
            changes.append({
                "op": "skip",
                "detail": "ftfy 未安装，跳过编码修复。如需启用请: pip install ftfy",
            })

        # ── 阶段 1b：解码字面 Unicode 转义（如 "\u3000" → 真正的全角空格） ──
        # 用户可能从某些来源粘贴了字面转义序列而非实际字符
        literal_escapes_before = result
        escape_count = len(re.findall(r'\\u[0-9a-fA-F]{4}', result))
        if escape_count:
            result = re.sub(
                r'\\u([0-9a-fA-F]{4})',
                lambda m: chr(int(m.group(1), 16)),
                result,
            )
            if result != literal_escapes_before:
                changes.append({
                    "op": "unicode_escape_decode",
                    "detail": f"解码了 {escape_count} 个字面 Unicode 转义（如 \\u3000 → 实际字符）",
                })

        # ── 阶段 2：标准化空白符 ──
        original = result

        # 2a: 将各种换行符统一为 \n
        result = result.replace("\r\n", "\n").replace("\r", "\n")

        # 2b: 去除行首空白（所有行，含第一行）
        result = re.sub(r"\n +", "\n", result)
        result = re.sub(r"^ +", "", result)

        # 2c: 去除行尾多余空白（含全角空格 \u3000、不换行空格 \u00a0）
        result = re.sub(r"[ \t\u3000\u00a0]+\n", "\n", result)

        # 2d: 将多个连续空行缩减为最多一个空行（在行尾清理之后执行，避免空格干扰）
        result = re.sub(r"\n{3,}", "\n\n", result)

        # 2e: Unicode 空白符 → 普通空格（全角空格 · 不换行空格 · 其他 Unicode 空白）
        result = re.sub(
            r"[\u3000\u00a0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u202f\u205f]",
            " ", result,
        )

        # 2f: 将连续多个空格缩减为单个空格
        result = re.sub(r" {2,}", " ", result)

        # 2g: 去除 \t 制表符
        result = result.replace("\t", " ")

        # 2h: 首尾 trim
        result = result.strip()

        if result != original:
            changes.append({
                "op": "whitespace_normalize",
                "detail": "标准化了空白符（含全角空格·不换行空格·换行/制表符）",
            })

        cleaned = len(changes) > 0 and changes[0].get("op") != "skip"
        return CleaningResult(
            text=result,
            cleaned=cleaned,
            changes=changes,
            metadata={"heuristic_strategies": ["ftfy_fix", "whitespace_normalize"]},
        )

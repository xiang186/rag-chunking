"""
表格结构分析与列映射配置生成。

解析文档中的 HTML 表格，输出：
- 每张表的行列数
- 每列前 N 行的示例数据
- 自动识别的列角色建议（员工姓名/评分/评分理由）
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
import pandas as pd


@dataclass
class ColumnInfo:
    col_index: int
    sample_values: List[str]
    suggested_role: str  # "employee" | "score" | "reason" | "dimension" | "unknown"


@dataclass
class TableAnalysis:
    table_index: int
    rows: int
    cols: int
    columns: List[ColumnInfo]
    group_name: str = ""


@dataclass
class TableAnalysisResult:
    tables: List[TableAnalysis]
    total_tables: int


def analyze_tables(text: str) -> TableAnalysisResult:
    """分析文档中的所有 HTML 表格结构。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(text, "html.parser")
    html_tables = soup.find_all("table")

    result_tables: List[TableAnalysis] = []

    # 提取组名（# 标题）
    current_group = ""
    for elem in soup.children:
        if hasattr(elem, "get_text"):
            t = elem.get_text(strip=True)
            if t.startswith("# "):
                current_group = t[2:].strip()

    for idx, table in enumerate(html_tables):
        analysis = _analyze_single_table(str(table), idx, current_group)
        result_tables.append(analysis)

    return TableAnalysisResult(tables=result_tables, total_tables=len(result_tables))


def _analyze_single_table(table_html: str, table_index: int, group_name: str) -> TableAnalysis:
    """分析单个表格的结构。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(table_html, "html.parser")
    all_trs = soup.find_all("tr")
    if not all_trs:
        return TableAnalysis(table_index=table_index, rows=0, cols=0, columns=[], group_name=group_name)

    # 收集所有行每列的文本
    raw_rows: List[List[str]] = []
    max_cols = 0
    for tr in all_trs:
        cells = tr.find_all(["td", "th"])
        row = []
        for cell in cells:
            text = _clean_simple(cell)
            colspan = int(cell.get("colspan", 1))
            for _ in range(colspan):
                row.append(text)
        if row:
            raw_rows.append(row)
            max_cols = max(max_cols, len(row))

    # 补齐列数
    for row in raw_rows:
        while len(row) < max_cols:
            row.append("")

    if not raw_rows:
        return TableAnalysis(table_index=table_index, rows=0, cols=0, columns=[], group_name=group_name)

    # 构建每列的示例数据（前 3 行）
    columns: List[ColumnInfo] = []
    for c in range(max_cols):
        samples = []
        for r in range(min(3, len(raw_rows))):
            val = raw_rows[r][c].strip() if c < len(raw_rows[r]) else ""
            samples.append(val)
        role = _suggest_column_role(samples, c, raw_rows)
        columns.append(ColumnInfo(
            col_index=c,
            sample_values=samples,
            suggested_role=role,
        ))

    return TableAnalysis(
        table_index=table_index,
        rows=len(raw_rows),
        cols=max_cols,
        columns=columns,
        group_name=group_name,
    )


def _clean_simple(cell) -> str:
    """快速提取单元格文本。"""
    for br in cell.find_all("br"):
        br.replace_with("，")
    for p in cell.find_all("p"):
        p.append(" ")
    text = cell.get_text(separator="", strip=False).strip()
    return re.sub(r"\s+", " ", text)


def _suggest_column_role(samples: List[str], col_idx: int, all_rows: List[List[str]]) -> str:
    """根据样本数据猜测列的角色。"""
    import re

    # 检查样本中是否包含明确的角色标识
    sample_text = " ".join(samples).lower()

    # 包含"评分""得分""分数"等 → score
    if re.search(r"^[评得分][分]*$|^[0-9.]+$|^[0-9.]+分$", sample_text.replace("，", " "), re.UNICODE):
        pass  # 往下继续判断

    # 检查列名/第一行
    first = samples[0] if samples else ""

    # 表头标签匹配
    if first in ("评分", "得分", "分数", "Score", "score"):
        # 检查同一行的前列是否是"姓名"类型 → 可能是员工名
        prev_col = ""
        if col_idx > 0 and all_rows:
            for r in range(min(1, len(all_rows))):
                prev_col = all_rows[r][col_idx - 1] if col_idx - 1 < len(all_rows[r]) else ""
                if prev_col in ("评分理由", "评分原因", "理由", "说明", "备注"):
                    return "reason"
        # 检查后一列是否是"理由"
        next_col = ""
        if all_rows:
            for r in range(min(1, len(all_rows))):
                next_col = all_rows[r][col_idx + 1] if col_idx + 1 < len(all_rows[r]) else ""
                if next_col in ("评分理由", "评分原因", "理由", "说明", "备注", "reason"):
                    return "score"
        return "score"

    if first in ("评分理由", "评分原因", "理由", "说明", "备注", "Reason", "reason"):
        # 前列是评分
        return "reason"

    if "姓名" in first or "name" in first.lower():
        return "employee"

    # 数值检测：该列大部分是数字 → score
    numeric_count = 0
    for s in samples[1:]:  # 从第2行开始检查
        s = s.strip()
        if re.match(r"^[0-9]+(\.[0-9]+)?$", s) or re.match(r"^[0-9]+(\.[0-9]+)?分$", s):
            numeric_count += 1

    if numeric_count >= 2:
        return "score"

    # 中文姓名检测：2-3个中文字符
    name_count = 0
    for s in samples:
        s = s.strip()
        if re.match(r"^[\u4e00-\u9fff]{2,3}$", s):
            name_count += 1
    if name_count >= 2 and col_idx >= 4:  # 靠后的列更可能是员工名
        return "employee"

    # 维度信息：靠左的短文本列
    if col_idx <= 3:
        return "dimension"

    return "unknown"

"""
table_chunker.py — 智能表格解析与分块引擎

适用场景：
  处理由 Excel/CSV 转换而来的 Markdown 文档，其中包含大量 HTML <table> 标签。
  传统的字符/语义分块会破坏表格的行列对应关系，本模块通过：
    1. HTML 表格解析与清洗（BeautifulSoup）
    2. 动态行级模板化自然语言转换
    3. 跨行引用解析
    4. 结构化 Metadata 构建
  将每一行表格数据转换为语义完整的独立 Chunk，供下游 RAG 系统使用。

核心模块：
  - TableChunkingConfig : Pydantic 配置类
  - TableParser          : HTML 表格解析、清洗、表头提取
  - ReferenceResolver    : 跨行/跨列引用解析（同上、同X行）
  - RowTemplateEngine    : 动态模板化生成 Chunk 文本
  - TableChunker         : 主类，串联上述模块

依赖：
  pip install beautifulsoup4 pandas pydantic
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.strategies.base import BaseChunker, ChunkResult

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# 1. TableChunkingConfig — Pydantic 配置类
# ────────────────────────────────────────────────────────────


class TableChunkingConfig(BaseModel):
    """
    TableChunker 的配置模型。

    Attributes:
        enable_reference_resolution:
            是否开启跨行引用解析（同上、同X行等），默认 True。
        template_style:
            模板风格。
            - "default"           : 通用模板「列名为值1，列名为值2...」
            - "primary_key_based" : 主键模板「关于【ID】的记录显示：列2为值2...」
        skip_empty_rows:
            是否自动跳过全为空的行，默认 True。
    """

    enable_reference_resolution: bool = Field(
        default=True,
        description="是否启用跨行引用解析（同上、特征同X行等）",
    )
    template_style: Literal["default", "primary_key_based"] = Field(
        default="default",
        description="模板风格：default（通用）| primary_key_based（主键优先）",
    )
    skip_empty_rows: bool = Field(
        default=True,
        description="是否跳过全为空的行",
    )


# ────────────────────────────────────────────────────────────
# 2. TableParser — HTML 表格解析器
# ────────────────────────────────────────────────────────────

# 主键列关键词：如果表格第一列的 <th> 文本包含以下任一词，则自动启用主键模板
_PRIMARY_KEY_KEYWORDS: Tuple[str, ...] = (
    "id", "编号", "序号", "名称", "姓名", "项目", "指标", "层位",
    "深度", "地层", "样品", "点位", "编号/id", "id/编号",
)

# 数值模式：用于 metadata 中的 numeric_fields 自动识别
_NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


class ParsedTable:
    """
    解析后的表格数据结构。

    Attributes:
        table_index:   当前文档中第几个表格（0-based）。
        global_context: 跨列表头合并而成的表格大标题/全局上下文描述。
        headers:       列名列表，按出现顺序排列（不含 colspan 合并的大标题）。
        rows:          数据行列表，每行为 dict {列名: 清洗后文本}。
        raw_rows:      原始二维列表（清洗后，行填充对齐），供引用解析使用。
    """

    __slots__ = ("table_index", "global_context", "headers", "rows", "raw_rows")

    def __init__(
        self,
        table_index: int,
        global_context: str,
        headers: List[str],
        rows: List[Dict[str, str]],
        raw_rows: List[List[str]],
    ) -> None:
        self.table_index = table_index
        self.global_context = global_context
        self.headers = headers
        self.rows = rows
        self.raw_rows = raw_rows


def _clean_cell_text(cell_html: str) -> str:
    """
    清洗单个 HTML 单元格文本：

    - 提取 <img> 的 src 并保留为 [图片: URL]。
    - 将 <br> 替换为逗号 + 空格。
    - 去除 <p>、<span> 等标签，仅保留纯文本。
    - 去除首尾空白。
    """
    if not cell_html:
        return ""

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(cell_html, "html.parser")

    # 提取图片 URL
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if src:
            replacement = f"[图片: {src}]" if not alt else f"[图片: {alt}({src})]"
            img.replace_with(replacement)

    # 将 <br> 替换为逗号
    for br in soup.find_all("br"):
        br.replace_with("，")

    # 处理 <p> 标签：在段落末尾追加空格，避免多个段落被无间隔拼接
    for p in soup.find_all("p"):
        p.append(" ")

    # 获取纯文本，自动拼接（不使用 strip=True，保留 <p> 追加的空格）
    text = soup.get_text(separator="", strip=False)
    # 去除首尾空白并压缩多余空白（含上面 <p> 追加的空格以及原文中的换行/缩进）
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_headers_and_context(thead: Any, table_index: int) -> Tuple[str, List[str]]:
    """
    从 <thead> 中提取表头信息和全局上下文。

    处理策略（支持多行 thead）：
    - 遍历 thead 中的所有 <tr>。
    - 如果某行的全部 <th> 都有 colspan>1（即跨列标题行），视为「全局上下文」行。
    - 最后一行无跨列的 <th> 视为「列名行」。
    - 特殊：如果所有行都是上下文行，则仍将最后一行同时作为列名行。

    Examples:
        <thead>
            <tr><th colspan="3">XX矿区钻孔地层特征</th></tr>  ← 全局上下文
            <tr><th>层位</th><th>深度(m)</th><th>岩性描述</th></tr>  ← 列名
        </thead>
        → global_context="XX矿区钻孔地层特征", headers=["层位", "深度(m)", "岩性描述"]

    Returns:
        (global_context, headers)
    """
    from bs4 import BeautifulSoup

    if thead is None:
        return "", []

    all_trs = thead.find_all("tr")
    if not all_trs:
        return "", []

    context_parts: List[str] = []
    headers: List[str] = []

    for tr in all_trs:
        ths = tr.find_all(["th", "td"])
        if not ths:
            continue

        # 判断是否所有 th 都有 colspan>1（跨列标题行）
        all_colspan = all(int(th.get("colspan", 1)) > 1 for th in ths)

        if all_colspan:
            # 上下文行：收集文本，用 / 拼接
            row_texts = []
            for th in ths:
                text = th.get_text(" ", strip=True)
                row_texts.append(text)
            context_parts.extend(row_texts)
        else:
            # 列名行：提取列名，处理 colspan
            headers.clear()  # 只保留最后一个列名行
            for th in ths:
                text = th.get_text(" ", strip=True)
                colspan = int(th.get("colspan", 1))
                if colspan > 1:
                    # 列名行中也有跨列：按位置展开
                    for i in range(colspan):
                        headers.append(f"{text}[{i+1}]")
                else:
                    headers.append(text)

    # 如果没有任何列名行，将最后一行的上下文 th 同时作为列名
    if not headers:
        last_tr = all_trs[-1]
        ths = last_tr.find_all(["th", "td"])
        for th in ths:
            text = th.get_text(" ", strip=True)
            colspan = int(th.get("colspan", 1))
            for i in range(colspan):
                headers.append(f"{text}-{i+1}" if colspan > 1 else text)

    global_context = "；".join(context_parts) if context_parts else ""
    return global_context, headers


def _determine_column_count(first_row_tds: List[Any]) -> int:
    """根据第一行数据单元格的 colspan 确定表格总列数。"""
    col_count = 0
    for td in first_row_tds:
        colspan = int(td.get("colspan", 1))
        col_count += colspan
    return col_count


def _parse_rows(
    table_tag: Any, headers: List[str], global_context: str, skip_empty: bool
) -> Tuple[List[Dict[str, str]], List[List[str]], str]:
    from bs4 import BeautifulSoup
    rows_dict: List[Dict[str, str]] = []
    rows_raw: List[List[str]] = []
    headers_from_colspan_th = False
    inner_context = ""
    tbody = table_tag.find("tbody") if table_tag else None
    thead = table_tag.find("thead") if table_tag else None
    if tbody is not None:
        all_trs = tbody.find_all("tr")
    else:
        all_trs = table_tag.find_all("tr") if table_tag else []
        if thead is not None:
            thead_trs = set(thead.find_all("tr"))
            all_trs = [tr for tr in all_trs if tr not in thead_trs]
    col_count = len(headers) if headers else 0
    for tr in all_trs:
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue
        if all(cell.name == "th" for cell in tds):
            if not headers:
                headers.clear()
                for cell in tds:
                    text = cell.get_text(" ", strip=True)
                    colspan = int(cell.get("colspan", 1))
                    if colspan > 1:
                        for i in range(colspan):
                            headers.append(f"{text}[{i+1}]")
                        headers_from_colspan_th = True
                        if not global_context and not inner_context:
                            inner_context = text
                    else:
                        headers.append(text)
                col_count = len(headers)
            continue
        if not headers:
            col_count = _determine_column_count(tds)
            headers = [f"列{i+1}" for i in range(col_count)]
        raw_cells: List[str] = []
        col_idx = 0
        for td in tds:
            cell_text = _clean_cell_text(str(td))
            colspan = int(td.get("colspan", 1))
            for _ in range(colspan):
                raw_cells.append(cell_text)
                col_idx += 1
        while len(raw_cells) < col_count:
            raw_cells.append("")
        raw_cells = raw_cells[:col_count]
        row_dict: Dict[str, str] = {}
        for i, h in enumerate(headers):
            val = raw_cells[i] if i < len(raw_cells) else ""
            row_dict[h] = val
        if skip_empty and all(not v.strip() for v in row_dict.values()):
            continue
        rows_dict.append(row_dict)
        rows_raw.append(raw_cells)
    if headers_from_colspan_th and rows_dict and rows_raw:
        first_values = list(rows_dict[0].values())
        if _looks_like_header_row(first_values):
            new_headers = first_values
            headers.clear()
            headers.extend(new_headers)
            for i in range(len(rows_dict)):
                old_vals = list(rows_dict[i].values())
                new_dict = {}
                for j, h in enumerate(headers):
                    new_dict[h] = old_vals[j] if j < len(old_vals) else ""
                rows_dict[i] = new_dict
            rows_dict.pop(0)
            rows_raw.pop(0)
    effective_context = global_context or inner_context
    return rows_dict, rows_raw, effective_context

def _looks_like_header_row(cells: List[str]) -> bool:
    """
    判断一行数据是否像「隐式表头行」。

    判断条件（全部满足）：
    1. 至少 2 个单元格。
    2. 每个单元格文本长度在 2-15 字之间。
    3. 每个单元格不包含数字。
    4. 所有单元格都不以数字开头。
    """
    if not cells or len(cells) < 2:
        return False
    for c in cells:
        stripped = c.strip()
        if not stripped:
            return False
        if not (2 <= len(stripped) <= 15):
            return False
        if re.search(r"\d", stripped):
            return False
    return True


class TableParser:
    """
    HTML 表格解析器。

    功能：
    - 使用 BeautifulSoup 解析 Markdown 文本中的 <table> 标签。
    - 提取表头（含 colspan 合并处理）、全局上下文。
    - 单元格清洗（去标签、提取图片 URL、br→逗号）。
    - 处理 rowspan（向下填充）、colspan（横向展开）。
    - 自动跳过全空行。
    """

    def __init__(self, config: TableChunkingConfig) -> None:
        self.config = config

    def parse(self, text: str) -> List[ParsedTable]:
        """
        从文本中解析所有 HTML 表格。

        Args:
            text: 包含 HTML <table> 标签的 Markdown 文本。

        Returns:
            ParsedTable 对象列表。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(text, "html.parser")
        tables = soup.find_all("table")
        results: List[ParsedTable] = []

        for idx, table in enumerate(tables):
            try:
                parsed = self._parse_single_table(table, idx)
                if parsed is not None:
                    results.append(parsed)
            except Exception as e:
                logger.warning("解析第 %d 个表格时出错: %s", idx, e)
                continue

        return results

    def _parse_single_table(self, table_tag: Any, index: int) -> Optional[ParsedTable]:
        """解析单个 <table> 标签。"""
        from bs4 import BeautifulSoup

        thead = table_tag.find("thead")
        tbody = table_tag.find("tbody")

        # 提取表头和全局上下文
        global_context, headers = _extract_headers_and_context(thead, index)

        # 解析数据行（直接传入 table_tag，内部处理有/无 tbody 两种结构）
        rows_dict, rows_raw, parsed_context = _parse_rows(
            table_tag, headers, global_context, self.config.skip_empty_rows
        )
        # 如果 _parse_rows 从内部补充了全局上下文，同步更新
        if not global_context and parsed_context:
            global_context = parsed_context

        # 如果表头仍为空，用列数补上
        if not headers and rows_raw:
            col_count = len(rows_raw[0]) if rows_raw else 0
            headers = [f"列{i+1}" for i in range(col_count)]
            # 重新构建 rows_dict
            rows_dict = []
            for raw in rows_raw:
                rd: Dict[str, str] = {}
                for i, h in enumerate(headers):
                    rd[h] = raw[i] if i < len(raw) else ""
                rows_dict.append(rd)

        # 处理 rowspan 向下填充
        self._fill_rowspan(headers, rows_dict, rows_raw)

        if not rows_dict:
            return None

        return ParsedTable(
            table_index=index,
            global_context=global_context,
            headers=headers,
            rows=rows_dict,
            raw_rows=rows_raw,
        )

    @staticmethod
    def _fill_rowspan(
        headers: List[str],
        rows_dict: List[Dict[str, str]],
        rows_raw: List[List[str]],
    ) -> None:
        """
        简单的 rowspan 填充策略：
        如果某行某列为空且上一行同名列有值，则将上一行的值向下填充。
        这是对原始 rowspan 合并的近似处理。
        """
        if not headers or not rows_dict:
            return

        for col_idx, header in enumerate(headers):
            last_value = ""
            for row_idx in range(len(rows_dict)):
                current = rows_dict[row_idx].get(header, "")
                if not current and last_value:
                    rows_dict[row_idx][header] = last_value
                    if col_idx < len(rows_raw[row_idx]):
                        rows_raw[row_idx][col_idx] = last_value
                else:
                    last_value = current


# ────────────────────────────────────────────────────────────
# 3. ReferenceResolver — 跨行引用解析
# ────────────────────────────────────────────────────────────

# 引用模式匹配：支持中文简写引用
# - "同上" → 引用上一行同列的值
# - "同3层"、"同3行" → 引用第3行的同列值（1-based）
# - "特征同3"、"特征同3层" → 引用第3行对应列的值
_REF_SAME_AS_ABOVE = re.compile(r"^(同[上]|同上|同前|如上)$")
_REF_SAME_AS_ROW = re.compile(r"^(?:特征)?同\s*(\d+)\s*(?:层|行|排)?$")
# "见/参见/参考 第X行/层" 要求必须出现「见」或「参」前缀，避免误匹配数值单元格
_REF_SEE_ROW = re.compile(r"^[见参][见考]?(?:第)?\s*(\d+)\s*(?:层|行|排)?.*$")


class ReferenceResolver:
    """
    跨行/跨列引用解析器。

    处理表格中常见的简写引用，如：
    - "同上" → 用上一行同列值替换
    - "特征同3层" → 用第3行同列值替换（1-based 行号）
    - 将解析结果拼接到当前单元格末尾，保留原始标记。

    注意：行号使用 1-based（第1行 = 索引0），与用户视觉一致。
    """

    def __init__(self, config: TableChunkingConfig) -> None:
        self.config = config

    def resolve(
        self, tables: List[ParsedTable]
    ) -> List[ParsedTable]:
        """
        对解析后的所有表格执行引用解析（原地修改 rows / raw_rows）。

        Args:
            tables: 已解析的表格列表。

        Returns:
            引用已解析的表格列表（与输入为同一对象）。
        """
        if not self.config.enable_reference_resolution:
            return tables

        for table in tables:
            self._resolve_table(table)

        return tables

    def _resolve_table(self, table: ParsedTable) -> None:
        """解析单个表格内的所有引用。"""
        if not table.raw_rows or len(table.raw_rows) < 2:
            return  # 少于2行无引用意义

        for row_idx, row in enumerate(table.raw_rows):
            for col_idx, cell_text in enumerate(row):
                resolved = self._resolve_cell(cell_text, row_idx, col_idx, table)
                if resolved != cell_text:
                    table.raw_rows[row_idx][col_idx] = resolved
                    # 同步更新 rows_dict
                    if col_idx < len(table.headers):
                        header = table.headers[col_idx]
                        table.rows[row_idx][header] = resolved

    def _resolve_cell(
        self, cell_text: str, row_idx: int, col_idx: int, table: ParsedTable
    ) -> str:
        """解析单个单元格的引用。

        检查顺序（优先级高→低）：
        1. "同上" — 引用上一行同列
        2. "特征同X层" — 引用指定行同列
        3. "见/参见第X行" — 引用指定行同列
        """
        stripped = cell_text.strip()
        if not stripped:
            return cell_text

        # Case 1: "同上" — 引用上一行同列
        if _REF_SAME_AS_ABOVE.match(stripped):
            if row_idx > 0 and col_idx < len(table.raw_rows[row_idx - 1]):
                ref_val = table.raw_rows[row_idx - 1][col_idx].strip()
                if ref_val:
                    return f"{cell_text}（即：{ref_val}）"
            return cell_text

        # Case 2: "特征同3层"、"同3行" — 引用指定行同列
        m = _REF_SAME_AS_ROW.match(stripped)
        if m:
            target_row = int(m.group(1)) - 1  # 转为 0-based
            if 0 <= target_row < len(table.raw_rows) and col_idx < len(table.raw_rows[target_row]):
                ref_val = table.raw_rows[target_row][col_idx].strip()
                if ref_val:
                    first_col = table.raw_rows[target_row][0].strip() if table.raw_rows[target_row] else ""
                    extra = f"（{first_col}行）" if first_col else ""
                    return f"{cell_text}（即{extra}：{ref_val}）"
            return cell_text

        # Case 3: "见第3行"、"参见2层" — 明确包含 见/参 前缀才匹配
        m2 = _REF_SEE_ROW.match(stripped)
        if m2:
            target_row = int(m2.group(1)) - 1
            if 0 <= target_row < len(table.raw_rows) and col_idx < len(table.raw_rows[target_row]):
                ref_val = table.raw_rows[target_row][col_idx].strip()
                if ref_val:
                    first_col = table.raw_rows[target_row][0].strip() if table.raw_rows[target_row] else ""
                    extra = f"{first_col}行" if first_col else ""
                    return f"{cell_text}（参考{extra}：{ref_val}）"

        return cell_text


# ────────────────────────────────────────────────────────────
# 4. RowTemplateEngine — 动态模板化 Chunk 生成引擎
# ────────────────────────────────────────────────────────────


def _is_primary_key_column(header: str, headers: List[str]) -> bool:
    """
    判断第一列或列名是否为主键列。

    判断规则：
    1. 该列是 headers 的第一个元素。
    2. 列名包含 _PRIMARY_KEY_KEYWORDS 中的关键词。
    """
    if header != headers[0]:
        return False
    header_lower = header.lower().replace(" ", "").replace("/", "")
    return any(kw.lower() in header_lower for kw in _PRIMARY_KEY_KEYWORDS)


def _is_numeric(value: str) -> bool:
    """判断字符串是否为数值类型。"""
    return bool(_NUMERIC_PATTERN.match(value.strip()))


class RowTemplateEngine:
    """
    动态行级模板化 Chunk 生成引擎。

    核心逻辑：
    - 严禁硬编码列名！根据解析到的 <th> 动态生成自然语言模板。
    - 支持两种模板风格，由 TableChunkingConfig.template_style 控制。

    模板说明：
    default 模式（通用）：
        "{Global_Context}中，列名1为值1，列名2为值2，列名3为值3。"

    primary_key_based 模式（主键优先）：
        "在{Global_Context}中，关于【第一列的值】的记录显示：其列名2为值2，列名3为值3。"

    自动降级：
    - 如果开启了 primary_key_based 模式但未检测到主键列，则自动降级为 default 模式。
    """

    def __init__(self, config: TableChunkingConfig) -> None:
        self.config = config

    def generate_chunks(
        self, tables: List[ParsedTable]
    ) -> List[Dict[str, Any]]:
        """
        为解析后的所有表格生成 Chunk。

        Returns:
            [{"content": str, "metadata": dict}, ...]
        """
        chunks: List[Dict[str, Any]] = []
        for table in tables:
            chunks.extend(self._generate_for_table(table))
        return chunks

    def _generate_for_table(
        self, table: ParsedTable
    ) -> List[Dict[str, Any]]:
        """为一个表格生成所有行 Chunk。"""
        if not table.headers or not table.rows:
            return []

        use_primary_key = self._should_use_primary_key(table)
        chunks: List[Dict[str, Any]] = []

        for row_idx, row_data in enumerate(table.rows):
            content = self._build_row_text(
                row_data, table, row_idx, use_primary_key
            )
            metadata = self._build_metadata(row_data, table, row_idx)
            chunks.append({"content": content, "metadata": metadata})

        return chunks

    def _should_use_primary_key(self, table: ParsedTable) -> bool:
        """判断是否应使用主键模板。"""
        if self.config.template_style != "primary_key_based":
            return False
        if not table.headers:
            return False
        return _is_primary_key_column(table.headers[0], table.headers)

    def _build_row_text(
        self,
        row_data: Dict[str, str],
        table: ParsedTable,
        row_idx: int,
        use_primary_key: bool,
    ) -> str:
        """
        为单行构建 Chunk 文本。

        实现细节：
        1. 遍历该行的每一个（列名, 值）对。
        2. 跳过空值列（避免生成 "某某为" 这种无意义的片段）。
        3. 根据模板风格组装自然语言。
        """
        # 收集非空的列描述片段
        parts: List[str] = []
        pk_value = ""

        for col_idx, header in enumerate(table.headers):
            value = row_data.get(header, "").strip()
            if not value:
                continue

            if use_primary_key and col_idx == 0:
                # 主键列值单独提取，不加入 parts
                pk_value = value
                continue

            # 去除单元格自带的句号，避免模板结尾出现双标点
            if value.endswith("。"):
                value = value[:-1]
            parts.append(f"{header}为{value}")

        # 拼接描述列表
        description = "，".join(parts)

        # 确定全局上下文前缀
        ctx_prefix = f"{table.global_context}中" if table.global_context else ""

        if use_primary_key and pk_value:
            # 主键模板: "在{上下文}中，关于【{主键值}】的记录显示：{列2为值2，列3为值3}"
            if description:
                if ctx_prefix:
                    return f"在{ctx_prefix}，关于【{pk_value}】的记录显示：{description}。"
                else:
                    return f"关于【{pk_value}】的记录显示：{description}。"
            else:
                # 只有主键值，没有其他列数据
                if ctx_prefix:
                    return f"在{ctx_prefix}中，存在记录【{pk_value}】。"
                else:
                    return f"存在记录【{pk_value}】。"
        else:
            # 通用模板: "{上下文}中，列1为值1，列2为值2，列3为值3。"
            if description:
                if ctx_prefix:
                    return f"{ctx_prefix}，{description}。"
                else:
                    return f"{description}。"
            else:
                return ""

    def _build_metadata(
        self,
        row_data: Dict[str, str],
        table: ParsedTable,
        row_idx: int,
    ) -> Dict[str, Any]:
        """
        为单行构建结构化 Metadata。

        Metadata 包含：
        - table_title  : 表格大标题（全局上下文）。
        - table_index  : 当前文档中第几个表格。
        - row_index    : 行号（0-based）。
        - raw_data     : 该行原始的 {列名: 值} 字典。
        - numeric_fields: 自动识别的数值型字段 {列名: float/int}。
        """
        # 数值字段自动识别
        numeric_fields: Dict[str, float] = {}
        for header, value in row_data.items():
            v = value.strip()
            if _is_numeric(v):
                try:
                    if "." in v:
                        numeric_fields[header] = float(v)
                    else:
                        numeric_fields[header] = int(v)
                except (ValueError, TypeError):
                    pass
            # 也尝试识别带单位的数值，如 "12.5m"、"30层"
            else:
                unit_match = re.match(r"^(-?\d+(?:\.\d+)?)\s*[a-zA-Z\u4e00-\u9fff]+$", v)
                if unit_match:
                    try:
                        numeric_fields[header] = float(unit_match.group(1))
                    except ValueError:
                        pass

        return {
            "table_title": table.global_context,
            "table_index": table.table_index,
            "row_index": row_idx,
            "raw_data": dict(row_data),
            "numeric_fields": numeric_fields,
        }


# ────────────────────────────────────────────────────────────
# 5. TableChunker — 主类
# ────────────────────────────────────────────────────────────

# Chunk 输出类型
TableChunk = Dict[str, Any]


class TableChunker:
    """
    智能表格解析与分块主类。

    串联 TableParser → ReferenceResolver → RowTemplateEngine 三大模块，
    输出可直接用于 RAG 的 Chunk 列表。

    使用示例：
        >>> chunker = TableChunker()
        >>> chunks = chunker.chunk_text(markdown_text)
        >>> for c in chunks:
        ...     print(c["content"])
        ...     print(c["metadata"])
    """

    def __init__(self, config: Optional[TableChunkingConfig] = None) -> None:
        self.config = config or TableChunkingConfig()
        self.parser = TableParser(self.config)
        self.resolver = ReferenceResolver(self.config)
        self.engine = RowTemplateEngine(self.config)

    def chunk_text(self, text: str) -> List[TableChunk]:
        """
        对包含 HTML 表格的文本执行表格感知分块。

        处理流程：
        1. TableParser.parse()  → 解析所有 <table>，提取结构化数据。
        2. ReferenceResolver.resolve() → 解析跨行引用。
        3. RowTemplateEngine.generate_chunks() → 动态生成 Chunk 文本和 Metadata。

        Args:
            text: 包含 HTML <table> 标签的 Markdown/HTML 文本。

        Returns:
            [{"content": "自然语言文本", "metadata": {...}}, ...]
        """
        # Step 1: 解析
        tables = self.parser.parse(text)
        if not tables:
            logger.info("未在文本中找到有效的 HTML 表格，返回空列表。")
            return []

        logger.info("解析到 %d 个表格", len(tables))

        # Step 2: 引用解析
        self.resolver.resolve(tables)

        # Step 3: 模板化生成
        chunks = self.engine.generate_chunks(tables)

        logger.info(
            "表格分块完成: %d 个 Chunk（来自 %d 个表格）",
            len(chunks),
            len(tables),
        )
        return chunks

    def chunk_text_with_raw(self, text: str) -> Dict[str, Any]:
        """
        与 chunk_text() 相同，但额外返回中间解析结果，便于调试和前端展示。

        Returns:
            {
                "chunks": [...],
                "tables": [
                    {
                        "table_index": int,
                        "global_context": str,
                        "headers": [str],
                        "row_count": int,
                    }, ...
                ]
            }
        """
        tables = self.parser.parse(text)
        if not tables:
            return {"chunks": [], "tables": []}

        self.resolver.resolve(tables)
        chunks = self.engine.generate_chunks(tables)

        table_overviews = [
            {
                "table_index": t.table_index,
                "global_context": t.global_context,
                "headers": t.headers,
                "row_count": len(t.rows),
            }
            for t in tables
        ]

        return {"chunks": chunks, "tables": table_overviews}


# ────────────────────────────────────────────────────────────
# 6. 工厂函数
# ────────────────────────────────────────────────────────────


def create_table_chunker(
    config: Optional[TableChunkingConfig] = None,
) -> TableChunker:
    """
    工厂函数：创建 TableChunker 实例。

    使用示例：
        >>> chunker = create_table_chunker(
        ...     TableChunkingConfig(template_style="primary_key_based")
        ... )
        >>> chunks = chunker.chunk_text(html_text)

    Args:
        config: 可选配置，不传则使用默认配置。

    Returns:
        TableChunker 实例。
    """
    return TableChunker(config=config)


# ────────────────────────────────────────────────────────────
# 7. HTMLTableChunker — 与现有 BaseChunker 兼容的包装类
# ────────────────────────────────────────────────────────────


class HTMLTableChunker(BaseChunker):
    """
    BaseChunker 包装类，将 TableChunker 集成到现有分块策略流水线中。

    该策略专用于处理包含 HTML <table> 标签的 Markdown/HTML 文本。
    内部委托给 TableChunker 执行实际解析和 Chunk 生成。

    参数（通过 self.params 配置）：
        - enable_reference_resolution (bool): 是否开启引用解析，默认 True。
        - template_style (str): "default" 或 "primary_key_based"。
        - skip_empty_rows (bool): 是否跳过空行，默认 True。
    """

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        """对文本执行 HTML 表格感知分块。"""
        config = TableChunkingConfig(
            enable_reference_resolution=bool(
                self.params.get("enable_reference_resolution", True)
            ),
            template_style=self.params.get(
                "template_style", "default"
            ),
            skip_empty_rows=bool(
                self.params.get("skip_empty_rows", True)
            ),
        )
        chunker = create_table_chunker(config)
        raw_chunks = chunker.chunk_text(text)

        results: List[ChunkResult] = []
        for raw in raw_chunks:
            results.append(
                ChunkResult(
                    text=raw["content"],
                    metadata=raw["metadata"],
                    char_start=0,
                    char_end=0,
                )
            )
        return results


# ────────────────────────────────────────────────────────────
# 9. ComplexTableChunker — 复杂多维表格解析引擎
# ────────────────────────────────────────────────────────────
# 适用场景：企业绩效考核表、财务报表等具有以下特征的"地狱级"表格：
#   - 多层嵌套合并单元格（rowspan / colspan）
#   - 多维表头：行维度（考核项/指标说明） + 列维度（动态扩展的员工列）
#   - 数据高度重复：左侧指标对所有人员一致，右侧评分为每个人独立
#
# 核心思路：矩阵还原 → 维度解析 → 长表化 → 关系模板生成
# ────────────────────────────────────────────────────────────


import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class ComplexTableChunk:
    """复杂表格的 Chunk 输出结构。"""
    content: str
    metadata: Dict[str, Any]


@dataclass
class ComplexTableChunkingConfig:
    """
    ComplexTableChunker 的配置模型。

    Attributes:
        template_style:
            - "indicator_split": 默认。每个（员工 × 指标）生成一个独立 Chunk。
            - "weight_split":    按权重分组。同一权重下所有指标合并到一个 Chunk。
        max_field_length:
            指标（indicator/dim_2）和考核标准（description/dim_4）字段的
            最大字符数，超出部分用「…」截断。0 或负数表示不截断。
    """
    template_style: str = "indicator_split"
    max_field_length: int = 150


# ════════════════════════════════════════════════════════════
# 9.1  HTMLMatrixRestorer — HTML 矩阵还原器
# ════════════════════════════════════════════════════════════

class HTMLMatrixRestorer:
    """
    将带有 rowspan/colspan 的复杂 HTML 表格还原为完整的二维矩阵（DataFrame）。

    核心策略：
    1. 解析 <table> 中的所有 <tr>，逐行扫描 <td>/<th>。
    2. 维护一个"行指针"矩阵，遇到 rowspan/colspan 时：
       - colspan > 1：横向展开占位单元格。
       - rowspan > 1：记录待填充区域，后续行补充。
    3. 对填充后的矩阵应用 pandas.DataFrame 包装，
       并使用 forward fill 确保合并单元格的标签向下传递。
    """

    def restore(self, table_html: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        将 HTML 表格还原为完整填充的 DataFrame，同时分离表头和数据。

        Args:
            table_html: 完整的 <table>...</table> HTML 字符串。

        Returns:
            (header_df, data_df):
            - header_df: 表头行构成的 DataFrame（全 <th> 行）。
            - data_df: 数据行构成的 DataFrame（含 <td> 的行），合并单元格已展开/填充。
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(table_html, "html.parser")
        table = soup.find("table")
        if table is None:
            return pd.DataFrame(), pd.DataFrame()

        all_trs = table.find_all("tr")
        if not all_trs:
            return pd.DataFrame(), pd.DataFrame()

        # ── Step 1: 将行分为「表头行」(all-th) 和「数据行」(含 td) ──
        # 表头行需要跟踪 rowspan 以正确对齐列
        header_raw: List[List[str]] = []
        data_raw: List[List[str]] = []
        # 表头行的 rowspan 跟踪: (row, col) -> remaining
        hspan_map: Dict[Tuple[int, int], int] = {}

        for tr in all_trs:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            is_header = all(cell.name == "th" for cell in cells)

            if is_header:
                row_idx = len(header_raw)
                current_row: List[str] = []
                col_idx = 0

                # 填充 rowspan 遗留（保持空字符串，不填充 rowspan 值）
                while (row_idx, col_idx) in hspan_map:
                    remain = hspan_map.pop((row_idx, col_idx))
                    if remain > 1:
                        hspan_map[(row_idx + 1, col_idx)] = remain - 1
                    current_row.append("")
                    col_idx += 1

                for cell in cells:
                    while (row_idx, col_idx) in hspan_map:
                        remain = hspan_map.pop((row_idx, col_idx))
                        if remain > 1:
                            hspan_map[(row_idx + 1, col_idx)] = remain - 1
                        current_row.append("")
                        col_idx += 1

                    text = _clean_cell_text(str(cell))
                    colspan = int(cell.get("colspan", 1))
                    rowspan = int(cell.get("rowspan", 1))

                    for _ in range(colspan):
                        current_row.append(text)
                        col_idx += 1

                    if rowspan > 1:
                        for r in range(1, rowspan):
                            for c in range(colspan):
                                hspan_map[(row_idx + r, col_idx - colspan + c)] = rowspan - r

                header_raw.append(current_row)
            else:
                # 数据行：直接收集
                current_row: List[str] = []
                for cell in cells:
                    if cell.name == "th":
                        continue
                    text = _clean_cell_text(str(cell))
                    colspan = int(cell.get("colspan", 1))
                    for _ in range(colspan):
                        current_row.append(text)
                data_raw.append(current_row)

        # ── Step 2: 对齐表头行列数 ──
        if header_raw:
            max_hc = max(len(r) for r in header_raw)
            for r in header_raw:
                while len(r) < max_hc:
                    r.append("")

        # ── Step 3: 用 rowspan/colspan 展开填充数据行
        # 手动处理数据行的合并单元格
        # 重新解析数据行，记录 rowspan
        data_raw2: List[List[Optional[str]]] = []
        data_span_map: Dict[Tuple[int, int], Tuple[str, int]] = {}

        for tr in all_trs:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            # 只处理含 td 的行
            if all(c.name == "th" for c in cells):
                continue

            row_idx = len(data_raw2)
            current: List[Optional[str]] = []
            col_idx = 0
            max_cols = max_hc if header_raw else 10

            # 处理 rowspan 遗留
            while (row_idx, col_idx) in data_span_map:
                val, remain = data_span_map[(row_idx, col_idx)]
                if remain > 1:
                    data_span_map[(row_idx + 1, col_idx)] = (val, remain - 1)
                current.append(val)
                col_idx += 1

            for cell in cells:
                while (row_idx, col_idx) in data_span_map:
                    val, remain = data_span_map[(row_idx, col_idx)]
                    if remain > 1:
                        data_span_map[(row_idx + 1, col_idx)] = (val, remain - 1)
                    current.append(val)
                    col_idx += 1

                if cell.name == "th":
                    continue  # th 在数据行中跳过

                text = _clean_cell_text(str(cell))
                colspan = int(cell.get("colspan", 1))
                rowspan = int(cell.get("rowspan", 1))

                for _ in range(colspan):
                    current.append(text)
                    col_idx += 1
                if rowspan > 1:
                    for r in range(1, rowspan):
                        for c in range(colspan):
                            data_span_map[(row_idx + r, col_idx - colspan + c)] = (text, rowspan - r)

            # 补齐到 max_cols
            while len(current) < max_cols:
                current.append("")
            data_raw2.append(current)

        # ── Step 4: 构建 DataFrame ──
        hdf = pd.DataFrame(header_raw) if header_raw else pd.DataFrame()
        ddf = pd.DataFrame(data_raw2) if data_raw2 else pd.DataFrame()

        # 对齐列数
        if not ddf.empty and not hdf.empty:
            maxc = max(hdf.shape[1], ddf.shape[1])
            for _ in range(hdf.shape[1], maxc):
                hdf[maxc - 1] = ""
            for _ in range(ddf.shape[1], maxc):
                ddf[maxc - 1] = ""

        # 注意：不再对数据行执行 ffill(axis=0)。
        # 行合并(rowspan)已在 data_span_map 中正确展开（见 Step 3），
        # 额外的 forward fill 会把空白单元格错误地传播为上一行的值，
        # 从而将前一行员工的分数「传染」给同一员工的其他空白考核项。
        return hdf, ddf


# ════════════════════════════════════════════════════════════
# 9.2  DimensionParser — 多维表头解析器
# ════════════════════════════════════════════════════════════

# 行维度关键词
_ROW_DIM_KEYWORDS = [
    "组别", "组名", "部门", "考核项", "考核指标", "指标说明",
    "指标名称", "权重", "基线", "目标值", "考核标准", "评分标准",
]

# 列维度（员工）模式
_COL_DIM_PATTERNS = [
    r"[\u4e00-\u9fa5]{2,4}[-\u2014]?(?:评价|评分|得分|分数|理由|自评|他评|上级评)",
    r"[\u4e00-\u9fa5]{2,4}\s*(?:评分|得分|分数)",
]


@dataclass
class DimensionInfo:
    """维度解析结果。"""
    row_dim_cols: List[int] = None
    col_dim_start: int = -1
    employee_pairs: List[Tuple[str, int, int]] = None
    column_names: List[str] = None
    headers: List[str] = None

    def __post_init__(self):
        if self.row_dim_cols is None:
            self.row_dim_cols = []
        if self.employee_pairs is None:
            self.employee_pairs = []
        if self.column_names is None:
            self.column_names = []
        if self.headers is None:
            self.headers = []


class DimensionParser:
    """
    多维表头解析器。

    自动识别行维度列（固定列）和列维度列（动态员工列），
    支持扁平模式和多层表头模式。
    """

    def parse(self, hdf: pd.DataFrame, ddf: pd.DataFrame, text: str = "") -> DimensionInfo:
        """
        解析维度结构。

        Args:
            hdf: 表头 DataFrame（全 <th> 行）。
            ddf: 数据 DataFrame（含 <td> 的行）。
            text: 原始 HTML（可选）。

        Returns:
            DimensionInfo。
        """
        info = DimensionInfo()
        ncols = ddf.shape[1] if not ddf.empty else (hdf.shape[1] if not hdf.empty else 0)
        if ncols == 0:
            return info

        info.column_names = [str(i) for i in range(ncols)]

        # 从表头行获取列名提示
        if not hdf.empty:
            info.headers = [str(hdf.iloc[r, c]) for r in range(min(2, len(hdf))) for c in range(ncols)]
        else:
            info.headers = [str(i) for i in range(ncols)]

        # 检测员工列（使用表头行）
        employee_pairs = self._detect_employee_columns(hdf, text)
        info.employee_pairs = employee_pairs

        if employee_pairs:
            first_emp_col = min(p[1] for p in employee_pairs)
            info.col_dim_start = first_emp_col
            info.row_dim_cols = list(range(first_emp_col))
        else:
            info.row_dim_cols = list(range(min(4, ncols)))
            info.col_dim_start = len(info.row_dim_cols)

        return info

    def _detect_employee_columns(
        self, hdf: pd.DataFrame, text: str
    ) -> List[Tuple[str, int, int]]:
        """检测员工及评分/理由列。

        检测策略（按优先级）：
        A. 多层表头：row0 含姓名（colspan=2），row1 为"评分""评分理由"
        B. 姓名模式：检测「姓名：XXX」的 colspan th 及其后的 评分/理由 列
        C. 扁平模式：列名匹配 "姓名-评分" 模式
        """
        import re
        results: List[Tuple[str, int, int]] = []

        if hdf.empty:
            return results

        ncols = hdf.shape[1]

        # ── 策略A: 多层表头（row0=姓名, row1=评分/评分理由） ──
        if len(hdf) >= 2:
            row0 = [str(hdf.iloc[0, c]).strip() for c in range(ncols)]
            row1 = [str(hdf.iloc[1, c]).strip() for c in range(ncols)]

            i = 0
            while i < ncols:
                name = row0[i]
                if name and name != "nan":
                    sub1 = row1[i] if i < len(row1) else ""
                    sub2 = row1[i + 1] if i + 1 < len(row1) else ""
                    if "评分" in sub1 or "得分" in sub1 or "分数" in sub1:
                        # 清理姓名前缀
                        clean_name = re.sub(r"^姓名[：:]?\s*", "", name).strip()
                        score_col = i
                        reason_col = i + 1 if i + 1 < ncols and ("理由" in sub2 or "说明" in sub2) else -1
                        results.append((clean_name, score_col, reason_col))
                        i += 2 if reason_col > i else 1
                        continue
                i += 1

        if results:
            return results

        # ── 策略B: 姓名模式 — 检测「姓名：XXX」模式 ──
        if len(hdf) >= 1:
            row0 = [str(hdf.iloc[0, c]).strip() for c in range(ncols)]
            i = 0
            while i < ncols:
                cell = row0[i]
                if cell and "姓名" in cell and cell != "nan":
                    # 提取姓名（去掉"姓名："、"姓名："等前缀）
                    name = re.sub(r"^姓名[：:]?\s*", "", cell).strip()
                    if name:
                        score_col = i
                        # 尝试找后面的评分理由列（同行的下一个单元格或下方行）
                        reason_col = -1
                        if i + 1 < ncols:
                            # 下一列可能包含"评分理由"或直接是理由
                            nxt = row0[i + 1] if i + 1 < len(row0) else ""
                            if nxt and "理由" in nxt:
                                reason_col = i + 1
                            elif len(hdf) >= 2:
                                below = str(hdf.iloc[1, i + 1]).strip() if i + 1 < ncols else ""
                                if "理由" in below:
                                    reason_col = i + 1
                        results.append((name, score_col, reason_col))
                        # 姓名列通常 colspan=2（评分+理由），跳过2列
                        i += 2
                        continue
                i += 1

        if results:
            return results

        # ── 策略C: 扁平模式 ──
        if len(hdf) >= 1:
            row = [str(hdf.iloc[0, c]).strip() for c in range(ncols)]
            for i, cell in enumerate(row):
                for pat in _COL_DIM_PATTERNS:
                    if re.match(pat, cell):
                        results.append((cell, i, -1))

        return results


# ════════════════════════════════════════════════════════════
# 9.3  LongTableTransformer — 长表转换器
# ════════════════════════════════════════════════════════════

class LongTableTransformer:
    """宽表 → 长表转换器。"""

    def transform(self, df: pd.DataFrame, dim_info: DimensionInfo) -> pd.DataFrame:
        if dim_info.employee_pairs:
            return self._transform_by_pairs(df, dim_info)
        return self._transform_by_melt(df, dim_info)

    def _transform_by_pairs(self, df: pd.DataFrame, dim_info: DimensionInfo) -> pd.DataFrame:
        records = []
        rows = dim_info.row_dim_cols

        for _, row_data in df.iterrows():
            for emp_name, score_col, reason_col in dim_info.employee_pairs:
                score_val = str(row_data.iloc[score_col]) if score_col < len(row_data) else ""
                reason_val = str(row_data.iloc[reason_col]) if reason_col >= 0 and reason_col < len(row_data) else ""

                # 跳过空值以及常见的「无数据」标记
                score_clean = score_val.strip()
                if (
                    not score_clean
                    or score_clean in ("nan", "", "/", "-", "—", "#N/A", "#REF!", "N/A", "无", "空")
                ):
                    continue

                rec = {}
                for r_idx, r_col in enumerate(rows):
                    if r_col < len(row_data):
                        val = str(row_data.iloc[r_col])
                        rec[f"dim_{r_idx}"] = val if val != "nan" else ""
                rec["employee"] = emp_name
                rec["score"] = score_val
                # reason 也跳过空标记
                reason_clean = reason_val.strip()
                if reason_clean in ("nan", "", "/", "-", "—", "#N/A"):
                    reason_val = ""
                rec["reason"] = reason_val
                records.append(rec)

        return pd.DataFrame(records)

    def _transform_by_melt(self, df: pd.DataFrame, dim_info: DimensionInfo) -> pd.DataFrame:
        ncols = df.shape[1]
        if ncols == 0:
            return pd.DataFrame()
        id_cols = dim_info.row_dim_cols if dim_info.row_dim_cols else [0]
        value_cols = [c for c in range(ncols) if c not in id_cols]
        if not value_cols:
            return pd.DataFrame()
        # 使用整数列名（DataFrame 列是 int 索引），不转为 str
        melted = pd.melt(
            df,
            id_vars=list(id_cols),
            value_vars=list(value_cols),
            var_name="column",
            value_name="value",
        )
        return melted


# ════════════════════════════════════════════════════════════
# 9.4  ComplexTableChunker — 主类
# ════════════════════════════════════════════════════════════

class ComplexTableChunker:
    """
    复杂多维表格分块主类。
    串联 HTMLMatrixRestorer → DimensionParser → LongTableTransformer → 模板生成。
    """

    def __init__(self, config: Optional[ComplexTableChunkingConfig] = None) -> None:
        self.config = config or ComplexTableChunkingConfig()
        self.restorer = HTMLMatrixRestorer()
        self.parser = DimensionParser()
        self.transformer = LongTableTransformer()

    def chunk_text(self, text: str) -> List[Dict[str, Any]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        tables = soup.find_all("table")
        all_chunks: List[Dict[str, Any]] = []
        for table in tables:
            all_chunks.extend(self._process_table(str(table)))
        return all_chunks

    def _process_table(self, table_html: str) -> List[Dict[str, Any]]:
        hdf, ddf = self.restorer.restore(table_html)
        if ddf.empty or hdf.empty or ddf.shape[0] < 1:
            return []

        # 检测并移除数据中的"隐式列名行"（<td> 包装的列标签行）
        # 这类行的前 3 列通常包含"考核项""考核指标""指标说明"等短文本标签
        if ddf.shape[0] > 0:
            first_vals = [str(ddf.iloc[0, c]).strip() for c in range(min(3, ddf.shape[1]))]
            if _looks_like_header_row(first_vals):
                ddf = ddf.iloc[1:].reset_index(drop=True)

        dim_info = self.parser.parse(hdf, ddf, table_html)
        long_df = self.transformer.transform(ddf, dim_info)
        if long_df.empty:
            return []
        return self._generate_chunks(long_df, dim_info)

    def _generate_chunks(self, long_df: pd.DataFrame, dim_info: DimensionInfo) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []

        if self.config.template_style == "weight_split" and "employee" in long_df.columns:
            return self._generate_chunks_weight_split(long_df)

        # ── indicator_split 模式（默认） ──
        for _, row in long_df.iterrows():
            content = self._build_content(row)
            if not content:
                continue
            metadata = self._build_metadata(row)
            chunks.append({"content": content, "metadata": metadata})
        return chunks

    def _generate_chunks_weight_split(self, long_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        按（员工, 权重）分组生成 Chunk。

        同一权重下所有指标合并到一条 Chunk 中，符合「评分以权重为分割点」的实际场景。
        """
        chunks: List[Dict[str, Any]] = []

        # 确定权重列名（优先 dim_3，否则回退到 dim_2 或空字符串标记）
        weight_col = "dim_3" if "dim_3" in long_df.columns and long_df["dim_3"].notna().any() else None

        # 按（员工, 权重）分组
        group_keys = ["employee"]
        if weight_col:
            group_keys.append(weight_col)

        grouped = long_df.groupby(group_keys, sort=False)

        for group_key, group_df in grouped:
            if weight_col:
                emp_name, weight_val = group_key
                weight_str = str(weight_val) if pd.notna(weight_val) and str(weight_val).strip() not in ("", "nan") else ""
            else:
                emp_name = group_key[0]
                weight_str = ""

            content = self._build_content_weight_split(group_df, emp_name, weight_str)
            if not content:
                continue

            # 构建 metadata
            metadata = {
                "employee_name": str(emp_name),
                "weight": weight_str,
                "indicator_count": len(group_df),
                "raw_rows": [
                    {str(k): str(v) for k, v in row.items()}
                    for _, row in group_df.iterrows()
                ],
            }
            chunks.append({"content": content, "metadata": metadata})

        return chunks

    def _build_content_weight_split(
        self, group_df: pd.DataFrame, emp_name: str, weight_str: str
    ) -> str:
        """
        weight_split 模式的 Chunk 模板。

        结构：
        在【组名】的绩效考核中，员工【姓名】在权重【X%】的考核指标上表现如下：
        - 考核项-考核指标：得分为【X】分（评分理由：...）
        - 考核项-考核指标：得分为【X】分
        （背景信息：考核标准：...）
        """
        group = ""
        parts_lines: List[str] = []

        for _, row in group_df.iterrows():
            g = str(row.get("dim_0", ""))
            if g and g != "nan" and not group:
                group = g

            item = str(row.get("dim_1", ""))
            indicator = str(row.get("dim_2", ""))
            description = str(row.get("dim_4", ""))
            score = str(row.get("score", ""))
            reason = str(row.get("reason", ""))

            if score in ("nan", ""):
                continue

            # 构建指标描述
            item_desc = ""
            if item and item != "nan":
                item_desc = item
            if indicator and indicator != "nan":
                indicator_t = self._truncate_field(indicator)
                item_desc = f"{item_desc}-{indicator_t}" if item_desc else indicator_t

            # 得分行
            score_part = f"得分为【{score}】分"
            if reason and reason != "nan":
                score_part += f"（评分理由：{reason}）"

            if item_desc:
                parts_lines.append(f"- {item_desc}：{score_part}。")
            else:
                parts_lines.append(f"- {score_part}。")

        if not parts_lines:
            return ""

        # 开头
        header_parts = []
        if group:
            header_parts.append(f"在【{group}】的绩效考核中")
        header_parts.append(f"员工【{emp_name}】")
        if weight_str:
            header_parts.append(f"在权重【{weight_str}】的考核指标上表现如下")
        else:
            header_parts.append(f"的考核指标上表现如下")

        header = "，".join(header_parts) + "："

        # 背景信息（取第一条非空考核标准）
        bg_items: List[str] = []
        if weight_str:
            bg_items.append(f"指标权重：{weight_str}")
        seen_desc: set = set()
        for _, row in group_df.iterrows():
            desc = str(row.get("dim_4", ""))
            if desc and desc != "nan":
                desc_t = self._truncate_field(desc)
                if desc_t not in seen_desc:
                    bg_items.append(f"考核标准：{desc_t}")
                    seen_desc.add(desc_t)

        body = "\n".join(parts_lines)
        content = f"{header}\n{body}"
        if bg_items:
            content += f"\n（背景信息：{'；'.join(bg_items)}）"

        return content

    def _build_content(self, row: pd.Series) -> str:
        group = str(row.get("dim_0", ""))
        item = str(row.get("dim_1", ""))
        indicator = str(row.get("dim_2", ""))
        weight = str(row.get("dim_3", ""))
        description = str(row.get("dim_4", ""))
        employee = str(row.get("employee", ""))
        score = str(row.get("score", ""))
        reason = str(row.get("reason", ""))

        if not employee.strip() or score in ("nan", ""):
            return ""

        # 字段截断
        indicator = self._truncate_field(indicator)
        description = self._truncate_field(description)

        parts = []
        if group and group != "nan":
            parts.append(f"在【{group}】的绩效考核中")

        if employee:
            parts.append(f"员工【{employee}】")

        item_desc = ""
        if item and item != "nan":
            item_desc = item
        if indicator and indicator != "nan":
            item_desc = f"{item_desc}-{indicator}" if item_desc else indicator
        if item_desc:
            parts.append(f"在【{item_desc}】上")

        parts.append(f"得分为【{score}】分")

        if reason and reason != "nan":
            parts.append(f"评分理由：【{reason}】")

        content = "，".join(parts) + "。"

        bg_parts = []
        if weight and weight != "nan":
            bg_parts.append(f"指标权重：{weight}")
        if description and description != "nan":
            bg_parts.append(f"考核标准：{description}")
        if bg_parts:
            content += f"\n（背景信息：{'；'.join(bg_parts)}）"

        return content

    def _truncate_field(self, value: str) -> str:
        """如果配置了最大长度且字段超出，则截断。"""
        if not value or value == "nan":
            return value
        max_len = self.config.max_field_length
        if max_len <= 0:
            return value
        if len(value) > max_len:
            return value[:max_len] + "…"
        return value

    def _build_metadata(self, row: pd.Series) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        for key in ["dim_0", "dim_1", "dim_2", "dim_3", "dim_4"]:
            if key in row.index:
                val = str(row[key])
                if val != "nan":
                    meta[f"dim_{key.split('_')[1]}"] = val

        if "employee" in row.index:
            meta["employee_name"] = str(row["employee"])
        if "score" in row.index:
            score_str = str(row["score"]).replace("分", "").strip()
            try:
                meta["score"] = float(score_str)
            except (ValueError, TypeError):
                meta["score"] = score_str

        meta["raw_row"] = {str(k): str(v) for k, v in row.items()}
        return meta


# ════════════════════════════════════════════════════════════
# 9.5  ComplexTableChunker 工厂函数
# ════════════════════════════════════════════════════════════


def create_complex_table_chunker(
    config: Optional[ComplexTableChunkingConfig] = None,
) -> ComplexTableChunker:
    return ComplexTableChunker(config=config)


# ════════════════════════════════════════════════════════════
# 9.6  ComplexHTMLTableChunker — BaseChunker 包装类
# ════════════════════════════════════════════════════════════


class ComplexHTMLTableChunker(BaseChunker):
    """BaseChunker 包装类，用于集成到现有分块流水线。

    支持的参数（通过 params 配置）：
        - template_style (str):
            "indicator_split" (默认) — 每个（员工 × 指标）生成一个独立 Chunk。
            "weight_split" — 按权重分组，同一权重下所有指标合并到一条 Chunk。
        - max_field_length (int):
            指标和考核标准字段的最大字符数，超出截断。默认 150，≤0 表示不截断。
    """

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        config = ComplexTableChunkingConfig(
            template_style=self.params.get("template_style", "indicator_split"),
            max_field_length=int(self.params.get("max_field_length", 150)),
        )
        chunker = create_complex_table_chunker(config)
        raw_chunks = chunker.chunk_text(text)
        results: List[ChunkResult] = []
        for raw in raw_chunks:
            results.append(
                ChunkResult(
                    text=raw["content"],
                    metadata=raw["metadata"],
                    char_start=0,
                    char_end=0,
                )
            )
        return results


# ────────────────────────────────────────────────────────────
# 10. 命令行快速测试（if __name__ == "__main__"）
# ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 演示用的示例 HTML 表格文本
    SAMPLE_TEXT = """
<h1>地层信息表</h1>
<table>
    <thead>
        <tr>
            <th colspan="3">XX矿区ZK01钻孔地层特征</th>
        </tr>
        <tr>
            <th>层位</th>
            <th>深度(m)</th>
            <th>岩性描述</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>1</td>
            <td>0-5.2</td>
            <td>褐黄色含砾粉质粘土，可塑，含少量植物根系。</td>
        </tr>
        <tr>
            <td>2</td>
            <td>5.2-12.8</td>
            <td>灰黄色粉砂，饱和，中密，主要成分为石英。</td>
        </tr>
        <tr>
            <td>3</td>
            <td>12.8-25.0</td>
            <td>特征同2层</td>
        </tr>
        <tr>
            <td>4</td>
            <td>25.0-38.5</td>
            <td>青灰色全风化花岗岩，岩芯呈砂状，手捏易碎。</td>
        </tr>
        <tr>
            <td>5</td>
            <td>38.5-50.0</td>
            <td>同上</td>
        </tr>
    </tbody>
</table>
"""

    # 初始化
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    chunker = create_table_chunker(
        TableChunkingConfig(
            enable_reference_resolution=True,
            template_style="primary_key_based",
        )
    )

    # 分块
    result = chunker.chunk_text_with_raw(SAMPLE_TEXT)

    print(f"解析到 {len(result['tables'])} 个表格")
    print(f"生成 {len(result['chunks'])} 个 Chunk\n")

    for chunk in result["chunks"]:
        print("─" * 60)
        print("【Content】")
        print(chunk["content"])
        print()
        print("【Metadata】")
        import json
        print(json.dumps(chunk["metadata"], ensure_ascii=False, indent=2))
        print()

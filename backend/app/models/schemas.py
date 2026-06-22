"""Pydantic V2 请求/响应模型定义。"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class StrategyName(str, Enum):
    """分块策略枚举 - 与工厂类注册名保持一致。"""

    RECURSIVE_CHARACTER = "recursive_character"
    SEMANTIC = "semantic"
    MARKDOWN_STRUCTURE = "markdown_structure"
    PDF_TABLE_LAYOUT = "pdf_table_layout"
    PARENT_CHILD = "parent_child"
    DIALOGUE_AWARE = "dialogue_aware"
    HTML_TABLE = "html_table"
    COMPLEX_TABLE = "complex_table"


# ── 检索相关模型 ──


class RetrievalRequest(BaseModel):
    """检索请求。"""

    query: str = Field(..., description="用户查询文本")
    top_k: int = Field(default=10, ge=1, le=100, description="返回 top-K 结果")
    metadata_filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="元数据硬过滤条件，如 {\"source_doc\": \"档案A.pdf\"}",
    )


class RetrievalResultItem(BaseModel):
    """检索结果单项。"""

    chunk_id: str = Field(..., description="分块 ID")
    doc_id: str = Field(..., description="所属文档 ID")
    text: str = Field(..., description="分块文本")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="分块元数据")
    score: float = Field(..., description="相关性分数（向量余弦相似度）")


class RetrievalResponse(BaseModel):
    """检索响应。"""

    query: str = Field(..., description="原始查询")
    total_results: int = Field(..., description="匹配结果总数")
    results: List[RetrievalResultItem] = Field(..., description="检索结果列表")


# ── 清洗相关模型 ──


class CleaningConfigRequest(BaseModel):
    """清洗配置请求。"""

    enable_heuristic: bool = Field(default=True, description="启用启发式清洗（乱码修复 + 空白标准化）")
    enable_layout: bool = Field(default=False, description="启用版面感知清洗（PDF 多栏/页眉页脚）")
    enable_pii: bool = Field(default=False, description="启用 PII 隐私脱敏（手机号/邮箱/身份证）")
    enable_semantic_filter: bool = Field(default=False, description="启用语义过滤（免责声明/页码）")
    layout_backend: str = Field(default="unstructured", description="版面分析后端")
    use_presidio: bool = Field(default=False, description="使用 presidio NLP 引擎增强 PII 识别")
    custom_filter_rules: List[str] = Field(
        default_factory=list,
        description="自定义语义过滤规则，每项为正则模式字符串",
    )


class CleaningConfigResponse(BaseModel):
    """清洗执行结果响应。"""

    text: str = Field(..., description="清洗后的文本")
    cleaned: bool = Field(..., description="是否发生了实际修改")
    changes: List[Dict[str, Any]] = Field(default_factory=list, description="所有变更记录")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="清洗过程元数据")


class UploadResponse(BaseModel):
    """文件上传响应。"""

    doc_id: str = Field(..., description="文档唯一标识")
    filename: str = Field(..., description="原始文件名")
    file_size: int = Field(..., description="文件大小（字节）")
    message: str = "上传成功"


class ChunkPreviewItem(BaseModel):
    """单个预览分块。"""

    index: int = Field(..., description="分块序号（从 0 开始）")
    text: str = Field(..., description="分块文本内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="分块元数据")
    char_start: int = Field(..., description="在原文中的起始字符位置")
    char_end: int = Field(..., description="在原文中的结束字符位置")


class ChunkPreviewRequest(BaseModel):
    """预览分块请求。"""

    strategy_name: StrategyName = Field(..., description="分块策略名称")
    params: Dict[str, Any] = Field(default_factory=dict, description="策略参数")
    preview_limit: int = Field(default=10, ge=1, le=99999, description="预览块数上限")
    cleaning_config: "CleaningConfigRequest | None" = Field(default=None, description="可选：分块前先执行数据清洗")


class ChunkPreviewResponse(BaseModel):
    """预览分块响应。"""

    doc_id: str
    strategy_name: StrategyName
    total_chunks: int = Field(..., description="全量切分后的总块数")
    preview_chunks: List[ChunkPreviewItem] = Field(..., description="前 N 个预览块")
    source_text_preview: str = Field(..., description="用于预览的原文片段")


class ChunkExecuteRequest(BaseModel):
    """执行全量分块请求。"""

    strategy_name: StrategyName
    params: Dict[str, Any] = Field(default_factory=dict)
    cleaning_config: "CleaningConfigRequest | None" = Field(default=None, description="可选：分块前先执行数据清洗")


class ChunkExecuteResponse(BaseModel):
    """执行全量分块响应。"""

    job_id: UUID = Field(..., description="分块任务 UUID")
    doc_id: str
    strategy_name: StrategyName
    total_chunks: int
    message: str = "分块任务已完成"


class ChunkRecord(BaseModel):
    """数据库中的分块记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    doc_id: str
    chunk_index: int
    text: str
    metadata: Dict[str, Any]
    parent_id: Optional[UUID] = None
    created_at: datetime

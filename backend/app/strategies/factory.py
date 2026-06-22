"""
分块策略工厂 - Factory Pattern

根据 strategy_name 字符串实例化对应的 Chunker 策略类。
新增策略时只需：
1. 实现 BaseChunker 子类
2. 在 STRATEGY_REGISTRY 中注册
"""

from typing import Any, Dict, Type

from app.models.schemas import StrategyName
from app.strategies.base import BaseChunker
from app.strategies.dialogue_aware import DialogueAwareSemanticChunker
from app.strategies.markdown_structure import MarkdownStructureChunker
from app.strategies.parent_child import ParentChildChunker
from app.strategies.pdf_table_layout import PDFTableLayoutChunker
from app.strategies.recursive_character import RecursiveCharacterChunker
from app.strategies.semantic import SemanticChunker
from app.strategies.table_chunker import HTMLTableChunker, ComplexHTMLTableChunker

# 策略注册表：strategy_name -> Chunker 类
STRATEGY_REGISTRY: Dict[str, Type[BaseChunker]] = {
    StrategyName.RECURSIVE_CHARACTER: RecursiveCharacterChunker,
    StrategyName.SEMANTIC: SemanticChunker,
    StrategyName.MARKDOWN_STRUCTURE: MarkdownStructureChunker,
    StrategyName.PDF_TABLE_LAYOUT: PDFTableLayoutChunker,
    StrategyName.PARENT_CHILD: ParentChildChunker,
    StrategyName.DIALOGUE_AWARE: DialogueAwareSemanticChunker,
    StrategyName.HTML_TABLE: HTMLTableChunker,
    StrategyName.COMPLEX_TABLE: ComplexHTMLTableChunker,
}

# 每种策略的默认参数和适用场景说明（供前端展示）
STRATEGY_META: Dict[str, Dict[str, Any]] = {
    StrategyName.RECURSIVE_CHARACTER: {
        "label": "递归字符分块",
        "description": "按段落/行/空格递归切分，适合通用文本",
        "default_params": {"chunk_size": 500, "chunk_overlap": 50},
        "param_schema": [
            {
                "key": "chunk_size",
                "label": "块大小",
                "type": "slider",
                "min": 100, "max": 2000, "step": 50,
                "description": "每个分块的目标字符数。数值越大，每个块包含的文本越多，块数量越少。较大的块（如 1000+）保留更多上下文，但检索粒度较粗；较小的块（如 200-500）检索更精准，但可能丢失上下文。",
            },
            {
                "key": "chunk_overlap",
                "label": "重叠大小",
                "type": "slider",
                "min": 0, "max": 500, "step": 10,
                "description": "相邻分块之间重叠的字符数。重叠可以避免文本在边界处被截断导致语义断裂。适用于对上下文连贯性要求较高的场景，通常设为 chunk_size 的 10% ~ 20%。",
            },
        ],
    },
    StrategyName.SEMANTIC: {
        "label": "语义分块",
        "description": "基于 Embedding 相似度在语义断点切分，适合长文档",
        "default_params": {
            "breakpoint_threshold": 80.0,
            "breakpoint_type": "percentile",
            "buffer_size": 1,
            "openai_api_key": "",
            "openai_base_url": "",
            "embedding_model": "",
        },
        "param_schema": [
            {
                "key": "breakpoint_threshold",
                "label": "断点阈值",
                "type": "slider",
                "min": 10, "max": 99, "step": 5,
                "description": "语义断点的敏感度百分位（仅 percentile 方法有效）。值越大（如 95），只有最剧烈的语义变化才被切分，块数量少、内容多；值越小（如 50），更细微的语义变化也会触发切分，块数量多、内容少。建议从 80 开始调节。standard_deviation 和 interquartile 方法不依赖此值。",
            },
            {
                "key": "breakpoint_type",
                "label": "断点类型",
                "type": "select",
                "options": ["percentile", "standard_deviation", "interquartile"],
                "description": "计算语义断点的统计方法。percentile（百分位法，默认）：基于相似度距离的百分位阈值，值越大断点越少，通用性强。standard_deviation（标准差法）：基于距离均值偏离程度，适合分布均匀的文本。interquartile（四分位法）：基于四分位距，对异常值不敏感。使用非 percentile 方法时，上方的「断点阈值」会被忽略，改用各方法的默认值。",
            },
            {
                "key": "buffer_size",
                "label": "上下文窗口",
                "type": "slider",
                "min": 0, "max": 3, "step": 1,
                "description": "计算 Embedding 时每个句子前后合并的句子数。值为 1 时每个句子合并前后各 1 句（共 3 句）计算向量，可平滑短句噪声。值为 0 时仅用单句计算，对语义变化更敏感。建议保持默认值 1。",
            },
            {
                "key": "openai_api_key",
                "label": "API Key",
                "type": "text",
                "placeholder": "留空则使用 .env 配置",
                "description": "用于调用 Embedding API 的密钥。可填写 OpenAI、DeepSeek 或其他兼容 API 的 Key。留空时使用后端 .env 文件中的 OPENAI_API_KEY 配置。",
            },
            {
                "key": "openai_base_url",
                "label": "自定义 Base URL",
                "type": "text",
                "placeholder": "例如 https://api.openai.com/v1",
                "description": "Embedding API 的访问地址。使用 OpenAI 官方 API 时留空即可；使用本地 LM Studio 填写 http://192.168.x.x:1234/v1；使用其他代理服务则填写对应的 API 地址。",
            },
            {
                "key": "embedding_model",
                "label": "Embedding 模型",
                "type": "text",
                "placeholder": "例如 text-embedding-3-small",
                "description": "用于生成文本向量的 Embedding 模型名称。OpenAI 推荐 text-embedding-3-small（速度快）或 text-embedding-3-large（精度高）；本地模型请填写 LM Studio 中加载的模型名称。需确保所选服务商支持该模型。",
            },
        ],
    },
    StrategyName.MARKDOWN_STRUCTURE: {
        "label": "Markdown 结构分块",
        "description": "按 Markdown 标题层级切分，保留文档结构",
        "default_params": {"strip_headers": False},
        "param_schema": [
            {
                "key": "strip_headers",
                "label": "移除标题行",
                "type": "switch",
                "description": "是否从分块内容中移除 Markdown 标题行（如 # 标题）。开启后，分块结果仅保留正文内容，标题信息仍存在于元数据中。适合标题本身不携带关键信息的场景；关闭则保留标题作为分块内容的一部分。",
            },
        ],
    },
    StrategyName.PDF_TABLE_LAYOUT: {
        "label": "PDF 表格版面分块",
        "description": "版面分析避免表格被切断，适合含表格的 PDF",
        "default_params": {"chunk_size": 800, "chunk_overlap": 50, "use_docling": False, "languages": "zh,en", "page_batch_size": 5},
        "param_schema": [
            {
                "key": "chunk_size",
                "label": "块大小",
                "type": "slider",
                "min": 200, "max": 2000, "step": 50,
                "description": "每个分块的目标字符数。与递归分块不同，此处的块大小是版面分析后的文本回退切分参数。建议设为 600-1000，以匹配 PDF 页面的平均信息量。",
            },
            {
                "key": "chunk_overlap",
                "label": "重叠大小",
                "type": "slider",
                "min": 0, "max": 500, "step": 10,
                "description": "相邻分块之间的重叠字符数。PDF 表格可能在边界处被分割，适当重叠（50-100）可避免表格关键数据在切分处丢失。",
            },
            {
                "key": "languages",
                "label": "文档语言",
                "type": "select",
                "options": ["zh,en", "zh", "en", "ja,en", "ko,en", "de,en", "fr,en"],
                "description": "PDF 文档的主要语言，用于优化解析策略。中文文档建议选择「zh,en」。使用 ISO 639-1 语言代码（如 zh=en=英文, ja=日文）。",
            },
            {
                "key": "use_docling",
                "label": "使用 Docling 解析",
                "type": "switch",
                "description": "是否启用 Docling 引擎进行 PDF 版面分析。开启后能更准确地识别表格、页眉页脚、多栏布局等复杂结构，避免表格被错误切断。需要额外安装 docling 依赖包。",
            },
            {
                "key": "page_batch_size",
                "label": "每批页数",
                "type": "slider",
                "min": 1, "max": 50, "step": 1,
                "description": "Docling 分批处理时的每批页数。PDF 页数超过 50 页时建议设为 1-5 页/批，可避免「输入长度超限」错误；简单 PDF 可设为 10-20 页/批提高效率。该参数仅在启用 Docling 时生效。",
            },
        ],
    },
    StrategyName.PARENT_CHILD: {
        "label": "父子双粒度分块",
        "description": "大粒度父块 + 小粒度子块，适合高精度检索场景",
        "default_params": {
            "parent_chunk_size": 2000,
            "child_chunk_size": 400,
            "child_chunk_overlap": 50,
        },
        "param_schema": [
            {
                "key": "parent_chunk_size",
                "label": "父块大小",
                "type": "slider",
                "min": 500, "max": 4000, "step": 100,
                "description": "父块的目标字符数。父块是较粗粒度的分块单元，用于提供上下文。较大的父块（2000-4000）可覆盖整个段落或小节，适合需要全局语境理解的场景。",
            },
            {
                "key": "child_chunk_size",
                "label": "子块大小",
                "type": "slider",
                "min": 100, "max": 1000, "step": 50,
                "description": "子块的目标字符数。子块是较细粒度的分块单元，用于精准检索匹配。较小的子块（200-400）可精确定位到句子级别，提高检索精度。",
            },
            {
                "key": "child_chunk_overlap",
                "label": "子块重叠",
                "type": "slider",
                "min": 0, "max": 200, "step": 10,
                "description": "相邻子块之间的重叠字符数。由于子块粒度较细，适当重叠（20-50）可避免句子被从中截断。注意：子块重叠仅影响子块之间，不影响父块。",
            },
        ],
    },
    StrategyName.DIALOGUE_AWARE: {
        "label": "对话体语义分块",
        "description": "按发言人轮次预切分 + Embedding 合并，适合对话录",
        "default_params": {
            "similarity_threshold": 0.75,
            "exchange_size": 2,
            "openai_api_key": "",
            "openai_base_url": "",
            "embedding_model": "",
        },
        "param_schema": [
            {
                "key": "similarity_threshold",
                "label": "话题相似度阈值",
                "type": "slider",
                "min": 0.3, "max": 0.95, "step": 0.05,
                "description": "相邻「对话交换」之间的余弦相似度阈值。值越高（如 0.90），对话题切换越敏感，块越多、越细；值越低（如 0.60），越倾向于合并多组问答为一个块。建议从 0.75 开始调节。",
            },
            {
                "key": "exchange_size",
                "label": "每组合并轮次数",
                "type": "slider",
                "min": 1, "max": 6, "step": 1,
                "description": "将连续几轮发言绑定为一个「对话交换」语义单元。默认 2（一问一答），适合辩论体；如果单人发言较长可设为 1；多人群聊可设为 3-4。值越大，单次 Embedding 计算包含的信息越多，合并倾向越强。",
            },
            {
                "key": "openai_api_key",
                "label": "API Key",
                "type": "text",
                "placeholder": "留空则使用 .env 配置",
                "description": "用于调用 Embedding API 的密钥，与语义分块共用同一配置。",
            },
            {
                "key": "openai_base_url",
                "label": "自定义 Base URL",
                "type": "text",
                "placeholder": "例如 http://localhost:8080/v1",
                "description": "Embedding API 的访问地址。默认使用 .env 中的配置。",
            },
            {
                "key": "embedding_model",
                "label": "Embedding 模型",
                "type": "text",
                "placeholder": "例如 gte-Qwen2",
                "description": "用于生成文本向量的模型名称。对话体分块依赖 Embedding 判断语义连贯性。",
            },
        ],
    },
    StrategyName.HTML_TABLE: {
        "label": "HTML 表格分块",
        "description": "解析 HTML <table> 标签，按行转为自然语言描述，适合含表格的 Markdown/HTML",
        "default_params": {
            "enable_reference_resolution": True,
            "template_style": "default",
            "skip_empty_rows": True,
        },
        "param_schema": [
            {
                "key": "enable_reference_resolution",
                "label": "跨行引用解析",
                "type": "switch",
                "description": "是否开启「同上」「特征同X行」等简写的自动引用解析。开启后，「特征同2层」会被替换为第2行的具体描述，使语义更完整。",
            },
            {
                "key": "template_style",
                "label": "模板风格",
                "type": "select",
                "options": ["default", "primary_key_based"],
                "description": "生成自然语言的模板风格。「default」：通用模板，每行包含所有列描述；「primary_key_based」：主键模板，将第一列作为条目标识（如 ID/名称/层位），生成「关于【XX】的记录显示：列2为值2...」。",
            },
            {
                "key": "skip_empty_rows",
                "label": "跳过空行",
                "type": "switch",
                "description": "是否自动跳过全为空的无效行，减少无意义的 Chunk。",
            },
        ],
    },
    StrategyName.COMPLEX_TABLE: {
        "label": "复杂多维表格分块",
        "description": "解析多层合并单元格(rowspan/colspan)的复杂HTML表格，自动识别员工维度，适合企业绩效考核表/财务报表",
        "default_params": {
            "template_style": "indicator_split",
            "max_field_length": 150,
        },
        "param_schema": [
            {
                "key": "template_style",
                "label": "分块模式",
                "type": "select",
                "options": ["indicator_split", "weight_split"],
                "description": "indicator_split：每个（员工×指标）生成独立Chunk；weight_split：按权重分组，同一权重下所有指标合并为一条Chunk（更符合评分场景）。",
            },
            {
                "key": "max_field_length",
                "label": "字段最大字符数",
                "type": "slider",
                "min": 0,
                "max": 500,
                "step": 10,
                "description": "指标和考核标准字段的最大字符数，超出部分以「…」截断。设为 0 表示不截断。",
            },
        ],
    },
}


class ChunkerFactory:
    """分块策略工厂类 - 根据名称创建对应 Chunker 实例。"""

    @staticmethod
    def create(strategy_name: str, params: Dict[str, Any] | None = None) -> BaseChunker:
        """
        工厂方法：根据策略名称实例化 Chunker。

        Args:
            strategy_name: 策略名称，对应 StrategyName 枚举值。
            params: 策略参数字典。

        Raises:
            ValueError: 策略名称未注册时抛出。
        """
        if strategy_name not in STRATEGY_REGISTRY:
            raise ValueError(
                f"未知分块策略: {strategy_name}。"
                f"可用策略: {list(STRATEGY_REGISTRY.keys())}"
            )
        chunker_class = STRATEGY_REGISTRY[strategy_name]
        return chunker_class(params=params)

    @staticmethod
    def list_strategies() -> list[Dict[str, Any]]:
        """返回所有可用策略的元信息，供前端策略选择器使用。"""
        return [
            {"name": name, **meta}
            for name, meta in STRATEGY_META.items()
        ]

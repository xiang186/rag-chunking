# RAG 文档分块（Chunking）系统

前后端分离的企业级 RAG 文档分块系统，支持 **8 种分块策略**、**5 步数据清洗管道**、**向量检索**与实时可视化预览。

## 项目目录结构

```
RAG知识库搭建/
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── uploads/                          # 上传文件存储目录
│   └── app/
│       ├── main.py                       # FastAPI 应用入口
│       ├── config.py                     # 配置管理（pydantic-settings）
│       ├── api/
│       │   ├── documents.py              # 文档分块 API 路由
│       │   ├── retrieval.py              # 检索 & 清洗 API 路由
│       │   └── logs.py                   # WebSocket 日志路由
│       ├── models/
│       │   └── schemas.py                # Pydantic V2 请求/响应模型
│       ├── db/
│       │   ├── database.py               # asyncpg 数据库连接
│       │   └── models.py                 # SQLAlchemy ORM 模型
│       ├── services/
│       │   ├── document_service.py       # 文件上传与文本提取
│       │   ├── chunk_service.py          # 分块预览与执行
│       │   ├── embedding_service.py      # Embedding 服务（AsyncOpenAI）
│       │   └── log_service.py            # WebSocket 日志发布服务
│       ├── cleaning/                     # 数据清洗管道
│       │   ├── __init__.py
│       │   ├── base.py                   # BaseCleaningStrategy 抽象接口
│       │   ├── pipeline.py               # CleaningPipeline 管道模式
│       │   ├── heuristic.py              # 启发式清洗（ftfy + 空白标准化）
│       │   ├── layout.py                 # 版面感知清洗（PDF 多栏/页眉页脚）
│       │   ├── pii.py                    # PII 隐私脱敏（正则 + 可选 presidio）
│       │   └── semantic_filter.py        # 语义过滤（免责声明/页码/版权）
│       └── strategies/                   # 策略模式分块策略
│           ├── base.py                   # BaseChunker 抽象接口
│           ├── factory.py                # ChunkerFactory 工厂类
│           ├── recursive_character.py    # 递归字符分块
│           ├── semantic.py               # 语义分块
│           ├── markdown_structure.py     # Markdown 结构分块
│           ├── pdf_table_layout.py       # PDF 表格版面分块
│           ├── parent_child.py           # 父子双粒度分块
│           ├── dialogue_aware.py         # 对话体感知语义分块
│           └── table_chunker.py          # HTML 表格 + 复杂多维表格分块引擎
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── main.ts
        ├── App.vue
        ├── api/
        │   └── documents.ts              # Axios API 封装（含清洗/检索 API）
        ├── composables/
        │   ├── useChunkPreview.ts        # 防抖预览 Hook
        │   └── useLogStream.ts           # WebSocket 日志流 Hook
        ├── components/
        │   ├── ChunkingConfig.vue        # 分块配置与预览主组件
        │   ├── CleaningDrawer.vue        # 数据清洗管道配置面板
        │   ├── RetrievalPanel.vue        # 高级语义检索面板
        │   └── LogPanel.vue              # 实时日志面板
        └── types/
            └── chunking.ts               # TypeScript 类型定义
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11+, FastAPI, LangChain v1.2+, Pydantic V2, SQLAlchemy 2.0 |
| 前端 | Vue 3 (Composition API + script setup), TypeScript, Element Plus, Axios |
| 数据库 | PostgreSQL (asyncpg async driver) |
| 缓存/队列 | Redis |

## 八种分块策略

| 策略 | 类名 | 适用场景 |
|------|------|----------|
| 递归字符分块 | `RecursiveCharacterChunker` | 通用文本，按段落/行/空格递归切分 |
| 语义分块 | `SemanticChunker` | 长文档，基于 Embedding 相似度找语义断点 |
| Markdown 结构分块 | `MarkdownStructureChunker` | Markdown/Wiki，按标题层级保留文档结构 |
| PDF 表格版面分块 | `PDFTableLayoutChunker` | 含表格的 PDF，基于版面分析避免表格被切断；可选 Docling 引擎获得更精准的表格还原 |
| 父子双粒度分块 | `ParentChildChunker` | 高精度检索，大粒度父块 + 小粒度子块 |
| 对话体感知语义分块 | `DialogueAwareSemanticChunker` | 对话/会议记录，按发言人标签 + 话题边界切分 |
| HTML 表格分块 | `HTMLTableChunker` | 通用 HTML `<table>` 表格，支持 rowspan/colspan、跨行引用（同上/参见）解析、主键模式聚合 |
| 复杂多维表格分块 | `ComplexHTMLTableChunker` | 多层合并单元格的企业考核表/财务报表，自动识别员工维度与评分列，支持 indicator_split / weight_split 两种模板模式 |

### PDF 表格版面分块 — Docling 引擎

PDF 表格版面分块支持两种解析后端，通过 **「使用 Docling 解析」** 开关切换：

| 后端 | 说明 | 优缺点 |
|------|------|--------|
| `unstructured`（默认） | 版面元素检测（标题/段落/表格/图片） | 依赖轻，但表格还原精度一般 |
| `docling`（高精度） | 高精度 PDF 文档解析，表格还原为 Markdown | 还原精准度高，但首次使用需下载 HuggingFace 模型 |

**启用 Docling 后注意事项：**

1. **HuggingFace 模型自动下载** — 首次使用会自动下载 `docling-layout-heron` 模型（约数百 MB），日志中会出现 symlinks 缓存警告（`UserWarning: huggingface_hub cache-system uses symlinks...`）。**这是正常行为**，模型下载完成即可正常使用，后续不再出现该警告。如需消除警告，可在 Windows 中开启「开发者模式」。

2. **后端配置** — 自动使用 `pypdfium2` 后端解析 PDF（避免 `docling_parse` 的 glyph 资源路径 bug）。禁用 OCR（`do_ocr=False`），仅保留版面布局和表格结构识别以提高处理速度。

3. **页面分批处理** — 策略参数新增 **「每批页数」**（`page_batch_size`，默认 5），将 PDF 按页分批处理，避免大 PDF 触发 Docling 模型的输入长度限制（`400 input length too long` 错误）。PDF 超过 50 页时建议设为 1-5 页/批；简单小 PDF 可设为 10-20 页/批提高效率。

4. **回退机制** — 任意一批页面解析失败不影响其他批次；所有批次均失败则自动回退到 `pdfplumber` 提取纯文本。

## 数据清洗管道

4 种清洗策略，按注册顺序级联执行：

| 策略 | 配置键 | 说明 | 依赖 |
|------|--------|------|------|
| 启发式清洗 | `enable_heuristic` | ftfy 乱码修复 + Unicode 空白标准化（全角空格→半角、行首尾修剪） | `ftfy>=6.3.1` |
| 版面感知清洗 | `enable_layout` | PDF 多栏布局合并、页眉页脚/页码检测移除 | `unstructured` 或 `docling` |
| PII 隐私脱敏 | `enable_pii` | 手机号/邮箱/身份证/姓名掩码（正则 + 可选 Presidio NLP 增强） | 正则零依赖；增强需 `presidio-analyzer` + `zh_core_web_sm` |
| 语义过滤 | `enable_semantic_filter` | 去除免责声明、页码标记、版权信息、自定义正则规则 | 零依赖 |

PII 掩码效果：

| 类型 | 原始 | 脱敏后 |
|------|------|--------|
| 手机号 | `13800138000` | `138****8000` |
| 邮箱 | `zhangsan@example.com` | `z***@example.com` |
| 身份证 | `110101199001011234` | `110101********1234` |
| 中文姓名 | `张三` | `张XX` |

开启"增强模式"（`use_presidio: true`）后，集成 Presidio + spaCy 中文模型，额外支持基于 NER 的人名/组织/地址识别。

### 清洗与分块联动

清洗配置不仅可以在 Drawer 中独立测试，还可以**应用到实际的分块流程**：

```
清洗 Drawer ──→ 点击「应用配置到分块流程」──→ 主界面显示「清洗已启用」标签
                                                    ↓
                   预览 / 执行分块 ──→ 先走清洗管道 ──→ 再走分块策略
```

操作方式：在清洗面板配置好策略后，点击底部 **「应用配置到分块流程」** 按钮，清洗配置即同步到分块流程。关闭清洗点击标签上的 × 即可恢复原始文本分块。

预览请求示例（携带清洗配置）：

```json
{
  "strategy_name": "recursive_character",
  "params": { "chunk_size": 500, "chunk_overlap": 50 },
  "cleaning_config": {
    "enable_heuristic": true,
    "enable_pii": true,
    "enable_semantic_filter": false
  }
}
```

## 清洗策略测试

直接在「数据清洗管道」面板输入下列测试文本，开启对应策略后点击"执行清洗测试"即可验证。

### 启发式清洗测试

开启：**启发式清洗**（默认已开启）

```text
com\u3000\u3000\u3000多余空格  和  â€"â€"â€"â€" 乱码     

  
   行首有多余空格的行  
```

预期效果：全角空格 → 普通空格 → 合并；乱码字符修复为 em dash（—）；行首行尾空白清除；多空行压缩。

### PII 隐私脱敏测试

开启：**PII 隐私脱敏**

```text
我叫张三，电话是13800138000，邮箱zhangsan@test.com
备用手机：13912345678，身份证号：110101199001011234
```

预期效果：`13800138000` → `138****8000`，`zhangsan@test.com` → `z***@test.com`，`110101199001011234` → `110101********1234`
勾选"增强模式"后：`张三` → `<PII>`（Presidio NER 识别）

### 语义过滤测试

开启：**语义过滤**

```text
项目进度报告
====================
第 1 页 / 共 3 页

本周完成了语义过滤功能的开发与测试。
核心逻辑基于正则模式匹配，支持自定义扩展规则。

免责声明：本文仅供参考，不构成投资建议

机密文件，未经授权不得转载

www.mycompany.com
```

预期效果：分隔线、页码、免责声明、保密标记、网址行全部移除，只保留正文。

**默认规则列表（无需自定义规则就能测）**:

| 规则         | 测试输入（直接复制）                               | 匹配效果   |
| :----------- | :------------------------------------------------- | :--------- |
| 页码         | `第 1 页 / 共 20 页`                               | ✅ 整行移除 |
| 页码英文     | `Page 1 of 20`                                     | ✅ 整行移除 |
| 简写页码     | `3 / 15`                                           | ✅ 整行移除 |
| 免责声明     | `免责声明：本文仅供参考，不构成投资建议`           | ✅ 整行移除 |
| 免责声明英文 | `Disclaimer: This document is for reference only.` | ✅ 整行移除 |
| 保密标记     | `机密文件，未经授权不得复制`                       | ✅ 整行移除 |
| 保密标记英文 | `Confidential`                                     | ✅ 整行移除 |
| 版权声明     | `© 2026 公司名称 All Rights Reserved`              | ✅ 整行移除 |
| 网址页脚     | `www.example.com`                                  | ✅ 整行移除 |
| 装饰分隔线   | `====================`                             | ✅ 整行移除 |

### 自定义规则测试

开启 **语义过滤** → 在"自定义过滤规则"输入框添加（支持 Enter 快速添加）：

| 输入 | 效果 |
|------|------|
| `^本期内容.*$` | 移除所有以"本期内容"开头的行 |
| `^.*转载请联系.*$` | 移除所有包含"转载请联系"的行 |

### 混合策略测试

同时开启启发式 + PII + 语义过滤，一次执行验证多策略级联效果：

```text
第 1 页 / 共 2 页

â€"â€" 会议纪要 â€"â€"

参会人员：张三（13800138000）、李四（13912345678）

机密内容：项目计划书

© 2026 公司内部资料
```

预期结果：页码移除 → 乱码修复 → 电话掩码 → 姓名显示名 → 保密标记移除 → 版权声明移除。

## Embedding 服务

- 基于 `AsyncOpenAI` 客户端，兼容 OpenAI API 与 llama-server 本地部署
- 自动添加 `passage:` / `query:` 前缀（gte-Qwen2 规范，区分文档向量与查询向量）
- 批量 Embedding + 指数退避重试

## 检索 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/retrieval/search` | 向量检索 + 元数据硬过滤 |
| `POST` | `/api/v1/retrieval/clean-text` | 数据清洗管道执行 |

检索流程：查询向量化 → 数据库读取 chunks（metadata_filters 过滤）→ 余弦相似度排序 → 返回 top_k 结果。

## 快速启动

### 1. 准备基础设施

```bash
# PostgreSQL
docker run -d --name rag-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=rag_chunking \
  -p 5432:5432 postgres:16

# Redis
docker run -d --name rag-redis -p 6379:6379 redis:7
```

### 2. 启动后端

```bash
cd backend
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # Linux/macOS

cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问：http://localhost:5173

## API 端点总览

### 文档分块

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/documents/strategies` | 获取所有分块策略及参数 schema |
| `POST` | `/api/documents/upload` | 上传文档，返回 `doc_id` |
| `POST` | `/api/documents/{doc_id}/preview` | 预览前 N 个分块（不落库），支持可选 `cleaning_config` |
| `POST` | `/api/documents/{doc_id}/execute` | 全量分块并写入 PostgreSQL，支持可选 `cleaning_config` |
| `POST` | `/api/documents/test-embedding` | 测试 Embedding API 连接 |

预览 / 执行请求示例（携带清洗配置）：

```json
{
  "strategy_name": "recursive_character",
  "params": { "chunk_size": 500, "chunk_overlap": 50 },
  "cleaning_config": {
    "enable_heuristic": true,
    "enable_pii": true,
    "enable_semantic_filter": false,
    "custom_filter_rules": []
  }
}
```

当 `cleaning_config` 为 `null` 或省略时，分块直接对原始文本执行，不走清洗。

### 数据清洗

```bash
curl -X POST http://localhost:8000/api/v1/retrieval/clean-text \
  -H "Content-Type: application/json" \
  -d '{
    "text": "联系电话：13800138000，邮箱：test@example.com",
    "enable_heuristic": true,
    "enable_pii": true,
    "enable_semantic_filter": true,
    "enable_layout": false,
    "use_presidio": false,
    "custom_filter_rules": []
  }'
```

### 向量检索

```bash
curl -X POST http://localhost:8000/api/v1/retrieval/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "查询内容",
    "top_k": 10,
    "metadata_filters": {"source_doc": "档案A.pdf"}
  }'
```

### 实时日志（WebSocket）

```
ws://localhost:8000/api/logs/ws
```

## 前端功能面板

| 面板 | 说明 | 入口 |
|------|------|------|
| 分块配置 | 文件上传、策略选择、参数配置、预览联动（高亮原文 ↔ 分块列表） | 主界面 |
| 数据清洗 | 5 策略开关、参数配置、文本测试、前后对比 + 变更记录表格；支持「应用配置到分块流程」 | 左侧底部「数据清洗管道」 |
| 高级检索 | 查询文本、Top-K 滑块、元数据过滤、结果列表（分数圆环 + 展开详情） | 右侧底部「高级语义检索」 |
| 实时日志 | WebSocket 日志流、级别过滤、自动滚动、连接状态 | 右侧默认展示 |

## 架构设计

### 策略模式 (Strategy Pattern)

```
BaseChunker (抽象接口)
    ├── RecursiveCharacterChunker
    ├── SemanticChunker
    ├── MarkdownStructureChunker
    ├── PDFTableLayoutChunker
    ├── ParentChildChunker
    └── DialogueAwareSemanticChunker

ChunkerFactory.create(strategy_name, params) → BaseChunker 实例
```

### 管道模式 (Pipeline Pattern) — 数据清洗

```
CleaningPipeline.run(text) → CleaningResult
    ├── HeuristicCleaner        # ftfy 编码修复 + 空白标准化
    ├── LayoutAwareCleaner      # PDF 版面分析（条件启用）
    ├── PIIRedactionCleaner     # 隐私脱敏（正则 + 可选 presidio）
    └── SemanticFilterCleaner   # 语义过滤
```

按注册表 `CLEANER_REGISTRY` 顺序级联执行，上一个的输出作为下一个的输入。

### 工厂模式 (Factory Pattern)

`ChunkerFactory` 通过 `STRATEGY_REGISTRY` 注册表将 `strategy_name` 映射到具体 Chunker 类，新增策略只需注册即可。

### 前端防抖预览

`useChunkPreview` Hook 监听策略和参数变化，500ms 防抖后自动请求预览 API，避免 Slider 快速拖动时产生大量请求。

### 实时日志

- `useLogStream` Hook：自动连接 WebSocket、断线重连（指数退避 1s→30s 封顶）
- `LogPanel` 组件：按级别过滤、自动滚动、清除、连接状态指示
- `emit_log()` 后端服务：FastAPI WebSocket 广播，各业务模块可注入自定义日志

## 数据库表结构

- `documents` — 上传文档元数据
- `chunk_jobs` — 分块任务（每次 execute 一条）
- `chunks` — 分块结果（含 metadata JSON、parent_id 父子映射）

## 环境变量

参见 `backend/.env.example`：

- `DATABASE_URL` — PostgreSQL 连接串
- `REDIS_URL` — Redis 连接串
- `OPENAI_API_KEY` — SemanticChunker 所需
- `OPENAI_BASE_URL` — 自定义 API 端点（兼容本地 llama-server）
- `EMBEDDING_MODEL` — Embedding 模型名称（默认 text-embedding-3-small）

## 依赖管理

### 后端

```bash
# 核心
pip install fastapi uvicorn python-multipart pydantic pydantic-settings

# 数据库
pip install sqlalchemy[asyncio] asyncpg redis

# LangChain
pip install langchain langchain-core langchain-text-splitters langchain-openai

# 文档解析
pip install "unstructured[pdf]"
pip install docling                   # 高精度 PDF 解析（可选，需开启 Docling 开关）
pip install pypdf                      # 轻量 PDF 页数读取（Docling 分批处理依赖）

# 数据清洗（可选）
pip install ftfy                          # 乱码修复
pip install presidio-analyzer presidio-anonymizer  # PII 增强
python -m spacy download zh_core_web_sm   # Presidio 中文模型
```

### 前端

```json
{
  "vue": "^3.5",
  "element-plus": "^2.9",
  "axios": "^1.7",
  "@element-plus/icons-vue": "^2.3"
}
```

## 扩展指南

### 新增分块策略

1. 在 `backend/app/strategies/` 下继承 `BaseChunker` 实现 `chunk()` 方法
2. 在 `factory.py` 的 `STRATEGY_REGISTRY` 和 `STRATEGY_META` 中注册
3. 在 `schemas.py` 的 `StrategyName` 枚举中添加名称

前端策略选择器会自动从 `/api/documents/strategies` 获取新策略。

### 新增清洗策略

1. 在 `backend/app/cleaning/` 下继承 `BaseCleaningStrategy` 实现 `process()` 方法
2. 在 `pipeline.py` 的 `CLEANER_REGISTRY` 中注册
3. 在 `api/retrieval.py` 的 `CleanTextRequest` schema 中添加配置字段
4. 前端配置面板会自动渲染新增的开关

## 代码约定

### \b 正则与中文文本

Python `re` 模块的 `\b` 词边界将 CJK 字符视为 `\w`（Unicode 字母），导致 `\b1[3-9]\d{9}\b` 在中文后不生效。所有数字匹配使用 `(?<!\d)/(?!\d)` 替代 `\b/\B`。

## 更新日志

### 2026-06-22 — 复杂多维表格 + 前端优化

#### 新增：复杂多维表格分块引擎
- **`table_chunker.py`**：4 个新模块 — `HTMLMatrixRestorer`（矩阵还原器）、`DimensionParser`（多维表头解析器）、`LongTableTransformer`（宽表→长表转换器）、`ComplexTableChunker`（主类）
- 自动识别多层合并单元格（rowspan/colspan）、自动检测员工列及对应的评分/评分理由列
- 支持两种模板模式：
  - **`indicator_split`**（默认）：每个（员工 × 指标）生成独立 Chunk
  - **`weight_split`**：按权重分组，同权重下所有指标合并为一条 Chunk（更符合评分场景）
- 字段截断：`max_field_length` 参数控制指标/考核标准字段最大字符数，超出以 `…` 截断
- 注册为 `complex_table` 策略，前端自动渲染分块模式下拉框和截断长度滑块

#### 新增：HTML 表格分块策略
- `HTMLTableChunker` 包装类，基于 `TableParser` + `RowTemplateEngine` + `ReferenceResolver`
- 支持 `default` 模式（每行一键生成键值对描述）和 `primary_key_based` 模式（按主键聚合）
- 跨行引用解析：「同上」→ 上一行、「特征同X层」→ 第X行、「见/参见第X行」→ 指定行
- 注册为 `html_table` 策略

#### Bug 修复
- **ffill(axis=0) 污染空白分数单元格**：移除 `HTMLMatrixRestorer` 中冗余的向下填充逻辑，空白分数不再被前一行值传染；同时增强 `_transform_by_pairs` 跳过 `/`、`—`、`#N/A` 等无数据标记
- **`<p>` 标签多余空格**：`_clean_cell_text` 提取文本前为段落追加空格分隔符，避免多段落文字无间隔拼接
- **`source_preview` 截断**：`chunk_service.py` 中，当表格策略 Chunk 的 `char_end` 全部为 0 时，使用完整文本作为预览原文

#### 前端优化
- 策略 radio 卡片左对齐（覆盖 Element Plus 默认 `white-space: nowrap`）
- 新增「执行全量分块并入库」「清除」按钮（上传文档后显示）
- 数据清洗管道按钮移至上传区域下方：启用后按钮变绿，显示（清洗已启用）及 X 关闭图标
- 修复 TypeScript 类型错误（`CleaningConfig` 类型对齐），`npm run build` 零错误通过

# RAG知识库分块系统 - 运行指南

## Python虚拟环境设置完成

已成功创建并配置Python虚拟环境，以下是详细说明：

### 📁 项目结构
```
chunking/
├── backend/              # Python FastAPI后端
├── frontend/             # Vue 3 TypeScript前端
├── venv/                 # Python虚拟环境 ✓
└── README.md             # 项目说明
```

### ✅ 已完成的工作

1. **Python虚拟环境** - 已在 `venv/` 目录创建
2. **基础依赖安装** - 已安装：
   - FastAPI + Uvicorn
   - SQLAlchemy (支持异步)
   - pydantic-settings
   - langchain-text-splitters
   - langchain-openai
   - 其他工具包

3. **环境配置** - 已创建 `.env` 文件（使用SQLite测试数据库）
4. **代码验证** - 基础模块导入测试通过

### 🚀 快速启动

#### 1. 激活虚拟环境
```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

#### 2. 启动后端服务器
```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
服务启动后访问：
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

#### 3. 启动前端开发服务器
```bash
cd frontend
npm install    # 首次需要安装依赖
npm run dev
```
前端运行在: http://localhost:5173

### ⚠️ 注意事项

1. **缺少的依赖**: `langchain-experimental` 包因网络问题未安装成功
   - 可以离线安装或等网络恢复后执行: `pip install langchain-experimental`
   - 这将启用 Semantic 分块策略

2. **数据库**: 当前使用SQLite测试数据库
   - 生产环境建议使用PostgreSQL
   - 配置: `DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname`

3. **OpenAI API**: 当前使用测试密钥
   - 请替换 `.env` 文件中的 `OPENAI_API_KEY`

### 🧪 功能验证

激活虚拟环境后，可以运行简单测试：
```bash
cd backend
python -c "from app.config import settings; print(f'应用: {settings.APP_NAME}')"
```

### 📋 依赖清单

已安装的主要包：
```
fastapi==0.137.1
uvicorn==0.49.0
sqlalchemy==2.0.51
pydantic==2.13.4
pydantic-settings==2.14.1
langchain==1.3.9
langchain-text-splitters==1.1.2
langchain-openai==1.3.2
python-dotenv==1.2.2
asyncpg==0.31.0
```

### 🔧 故障排除

**问题**: 导入时提示缺少 `langchain_experimental`
**解决**: 临时注释掉 `backend/app/strategies/factory.py` 中的相关导入

**问题**: 无法启动FastAPI服务器
**检查**:
1. 虚拟环境是否激活: `which python` / `where python`
2. 依赖是否完整: `pip list | findstr "fastapi uvicorn"`

### 🎯 下一步建议

1. 启动后端测试基础API
2. 安装前端依赖并启动开发服务器
3. 测试文档上传和分块功能
4. 根据实际需求配置数据库和OpenAI密钥

虚拟环境已准备就绪，可以开始进行代码运行测试！


## ✅ 数据库兼容性问题已修复

### 问题解决
原始代码使用了PostgreSQL特有的数据类型（JSONB、UUID类型），与SQLite不兼容。我已修复：

1. **JSONB → JSON**：将PostgreSQL的JSONB类型改为标准的SQLAlchemy JSON类型
2. **UUID类型 → 字符串**：将UUID类型改为字符串存储（36字符）
3. **动态类型选择**：代码现在能根据数据库URL自动选择适当的数据类型

### 当前状态
✅ **后端服务器已成功启动**：http://localhost:8000  
✅ **数据库表已创建**：documents, chunk_jobs, chunks  
✅ **健康检查通过**：http://localhost:8000/health  
✅ **API文档可用**：http://localhost:8000/docs  

### 已修复的模型
1. **Document** - 文档元数据表
2. **ChunkJob** - 分块任务表（使用JSON存储参数）
3. **Chunk** - 分块结果表（使用JSON存储元数据）

现在后端已经完全可以在SQLite上运行，可以进行全面的测试了！
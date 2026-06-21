# Python虚拟环境设置完成

## 已创建的内容
1. ✅ Python虚拟环境在 `venv/` 目录
2. ✅ 安装了基础依赖包：
   - FastAPI
   - SQLAlchemy
   - pydantic-settings
   - langchain-text-splitters
   - langchain-openai
   - 其他相关工具

## 虚拟环境使用说明

### 激活虚拟环境
```bash
# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 检查已安装的包
```bash
pip list
```

### 后端项目结构
```
backend/
├── app/                 # 主应用代码
│   ├── api/            # API路由
│   ├── config.py       # 配置文件
│   ├── db/             # 数据库模块
│   ├── services/       # 业务逻辑
│   └── strategies/     # 分块策略
├── .env                # 环境变量配置文件（已创建）
└── requirements.txt    # 依赖列表
```

## 运行测试

### 测试基本功能
激活虚拟环境后，可以运行：
```bash
cd backend
python -c "from app.config import settings; print(f'配置加载成功: {settings.APP_NAME}')"
```

### 启动开发服务器
```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 注意事项
1. 后端使用SQLite数据库（测试环境），生产环境需要PostgreSQL
2. OpenAI API密钥在`.env`文件中配置（当前为测试值）
3. 前端项目需要单独运行（Vue + TypeScript）

## 前端项目
前端是Vue 3 + TypeScript项目，位于 `frontend/` 目录：
```bash
cd frontend
npm install
npm run dev
```

前端开发服务器默认在 http://localhost:5173 运行，已经配置好与后端API的CORS。
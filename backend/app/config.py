"""应用配置模块 - 使用 pydantic-settings 管理环境变量。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    APP_NAME: str = "RAG Chunking System"
    DEBUG: bool = True

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_chunking"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # 文件存储
    UPLOAD_DIR: Path = Path(__file__).parent.parent / "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Embedding（SemanticChunker 使用）
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # 预览限制
    PREVIEW_CHUNK_LIMIT: int = 5


settings = Settings()
# 确保上传目录存在
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

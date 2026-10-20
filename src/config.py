"""集中配置：从 .env 加载，并以仓库根目录作为路径锚点。

字段与 `.env.example` 一一对应。路径锚定仓库根（而非当前工作目录），
保证任意位置启动都指向同一批产物。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """应用配置。字段名与 `.env.example` 的键一一对应。"""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 文本模型（DeepSeek）----
    deepseek_api_key: str = ""
    llm_provider: str = "deepseek"
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_timeout: int = 120
    llm_max_retries: int = 2
    llm_temperature: float = 0.0
    llm_max_tokens: int = 32768
    llm_thinking: str = "disabled"

    # ---- 嵌入与检索（智谱 + Chroma）----
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    embedding_model: str = "embedding-3"
    embedding_dimensions: int = 2048
    embedding_batch_size: int = 16
    chroma_dir: str = "./chroma_db"

    # ---- 接口服务 ----
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    api_max_upload_mb: int = 200
    api_max_concurrent_parses: int = 1
    api_max_pending_jobs: int = 2
    api_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,null"

    # ---- 任务队列（Celery + Redis）----
    task_backend: str = "celery"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_worker_pool: str = "solo"
    celery_result_expires: int = 86400
    celery_task_always_eager: bool = False

    # ---- 多 Agent 投标编写 ----
    bid_writer_max_revisions: int = 1
    bid_writer_min_score: int = 75
    bid_writer_top_k: int = 6
    bid_writer_max_concurrency: int = 3
    bid_writer_target_words: int = 1200

    # ---- 存储（PostgreSQL + Redis）----
    database_url: str = "postgresql+asyncpg://postgres:123456@localhost:5432/bidding"
    redis_url: str = "redis://localhost:6379/0"

    # ---- 云端 PDF 解析（MinerU）----
    mineru_mode: str = "cloud"
    mineru_api_key: str = ""
    mineru_endpoint: str = "https://mineru.net/api/v4/extract/task"
    mineru_batch_upload_endpoint: str = "https://mineru.net/api/v4/file-urls/batch"
    mineru_batch_result_endpoint: str = (
        "https://mineru.net/api/v4/extract-results/batch/{batch_id}"
    )
    mineru_timeout: int = 180
    mineru_download_timeout: int = 300
    mineru_model_version: str = "vlm"
    mineru_poll_interval: int = 5
    mineru_max_polls: int = 120
    mineru_max_retries: int = 2
    mineru_retry_backoff: float = 2.0
    mineru_language: str = "ch"
    mineru_enable_formula: bool = True
    mineru_enable_table: bool = True
    mineru_is_ocr: bool = False
    mineru_max_file_mb: int = 200
    mineru_max_pages: int = 200

    @field_validator("chroma_dir", mode="after")
    @classmethod
    def _anchor_chroma_dir(cls, v: str) -> str:
        """相对路径锚定到仓库根，绝对路径原样保留。"""
        p = Path(v)
        return str(p) if p.is_absolute() else str((REPO_ROOT / p).resolve())

    @property
    def cors_origins(self) -> list[str]:
        """逗号分隔的跨域来源，解析为列表供 CORSMiddleware 使用。"""
        return [item.strip() for item in self.api_cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """返回缓存的 Settings 单例，便于测试时替换/重置。"""
    return Settings()


settings = get_settings()

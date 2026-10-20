"""集中配置：从 .env 加载，并以仓库根目录作为路径锚点。

路径锚定仓库根（而非当前工作目录），保证任意位置启动都指向同一批产物。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """应用配置，字段在实现阶段按 .env.example 全量补齐。"""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8001


settings = Settings()

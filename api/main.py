"""FastAPI 应用工厂，uvicorn 启动入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import library, routes
from src.config import settings
from src.db.session import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时建表（尽力而为）：数据库不可用仅告警，不阻断服务。"""
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001 - 持久化是增强能力
        logger.warning("数据库初始化失败（忽略）: %s", exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="招标文件多智能体系统", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router)
    app.include_router(library.router)

    return app


app = create_app()

"""FastAPI 应用工厂，uvicorn 启动入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import library, routes
from src.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="招标文件多智能体系统", version="0.1.0")

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

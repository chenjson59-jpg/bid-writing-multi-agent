"""FastAPI 应用工厂，uvicorn 启动入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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

    @app.middleware("http")
    async def api_token_guard(request: Request, call_next):
        """简易鉴权：设置 API_TOKEN 后，/api 需携带 X-API-Token 头或 ?token= 参数。

        健康检查与 CORS 预检（OPTIONS）放行；静态页面不拦截，拦的是烧 LLM 的接口。
        """
        required = settings.api_token
        if (
            required
            and request.method != "OPTIONS"
            and request.url.path.startswith("/api")
            and request.url.path != "/api/health"
        ):
            provided = request.headers.get("x-api-token") or request.query_params.get("token")
            if provided != required:
                return JSONResponse({"detail": "未授权：缺少有效的访问令牌"}, status_code=401)
        return await call_next(request)

    # 单端口部署：挂载前端打包产物（dist 不存在说明是开发环境，走 vite dev server）
    dist_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="spa")

    return app


app = create_app()

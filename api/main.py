"""FastAPI 应用工厂，uvicorn 启动入口。"""
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="招标文件多智能体系统")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()

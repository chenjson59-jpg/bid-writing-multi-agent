"""`python -m api` 启动入口。"""
import uvicorn

from src.config import settings


def main() -> None:
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()

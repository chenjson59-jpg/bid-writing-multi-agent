"""`python -m src.cli` 入口，退出码透传给 shell。"""
import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())

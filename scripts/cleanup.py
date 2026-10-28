"""按保留期清理演示数据（P1-7）：解析结果、标书产物与上传的原件。

用法：
    uv run python scripts/cleanup.py                  # 按 DATA_RETENTION_DAYS（默认 30 天）
    uv run python scripts/cleanup.py --days 7         # 临时指定保留天数
    uv run python scripts/cleanup.py --dry-run        # 只打印待删内容
    uv run python scripts/cleanup.py --all            # 清空全部（显式确认，忽略保留期）
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _old_dirs(root: Path, cutoff: float) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and d.stat().st_mtime < cutoff)


def main() -> int:
    from src.config import settings
    from src.pipeline import BIDS_DIR, FILES_DIR, PARSED_DIR

    parser = argparse.ArgumentParser(description="按保留期清理解析结果、标书产物与上传原件")
    parser.add_argument("--days", type=int, default=settings.data_retention_days)
    parser.add_argument("--dry-run", action="store_true", help="只打印，不实际删除")
    parser.add_argument("--all", action="store_true", help="忽略保留期，删除全部")
    args = parser.parse_args()

    if args.all:
        days, cutoff = 0, float("inf")
    else:
        days = args.days
        if days <= 0:
            print(f"保留期为 {days} 天，未启用自动清理；如确需清空请加 --all")
            return 1
        cutoff = time.time() - days * 86400

    dirs: list[Path] = []
    for root in (PARSED_DIR, BIDS_DIR):
        dirs.extend(_old_dirs(root, cutoff))
    originals = []
    if FILES_DIR.is_dir():
        originals = sorted(
            p for p in FILES_DIR.iterdir() if p.is_file() and p.stat().st_mtime < cutoff
        )

    prefix = "[dry-run] " if args.dry_run else ""
    for target in dirs:
        print(f"{prefix}删除目录 {target}")
        if not args.dry_run:
            shutil.rmtree(target)
    for path in originals:
        print(f"{prefix}删除文件 {path}")
        if not args.dry_run:
            path.unlink(missing_ok=True)

    scope = "全部" if args.all else f"早于 {days} 天"
    print(f"完成：{len(dirs)} 个目录、{len(originals)} 个文件（范围：{scope}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""按保留期清理演示数据（P1-7/T-016）：向量片段、解析结果、标书产物与上传原件。

删除解析目录前先级联清理该文档在 Chroma 两个集合中的片段；向量清理失败时
保留该文档的解析目录与原件并以非零码退出，可重新运行脚本重试，避免孤儿向量。

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

    parser = argparse.ArgumentParser(description="按保留期清理向量片段、解析结果、标书产物与上传原件")
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

    from src.errors import IngestError
    from src.vectorstore.ingest import delete_document_vectors

    parsed_dirs = _old_dirs(PARSED_DIR, cutoff)
    bid_dirs = _old_dirs(BIDS_DIR, cutoff)
    originals = []
    if FILES_DIR.is_dir():
        originals = sorted(
            p for p in FILES_DIR.iterdir() if p.is_file() and p.stat().st_mtime < cutoff
        )

    prefix = "[dry-run] " if args.dry_run else ""
    failures = 0
    failed_doc_ids: set[str] = set()

    # 解析目录对应向量片段：先清向量再删目录，失败则保留该文档全部文件以待重试
    for target in parsed_dirs:
        doc_id = target.name
        print(f"{prefix}删除文档 {doc_id}（向量片段、解析目录与原件）")
        if args.dry_run:
            continue
        try:
            delete_document_vectors(doc_id)
        except IngestError as exc:
            failures += 1
            failed_doc_ids.add(doc_id)
            print(f"向量清理失败，保留文档 {doc_id} 以待重试：{exc}")
            continue
        shutil.rmtree(target)

    # 标书产物不对应向量，直接删除产物目录
    for target in bid_dirs:
        print(f"{prefix}删除标书产物目录 {target}")
        if not args.dry_run:
            shutil.rmtree(target)

    for path in originals:
        if not args.dry_run and any(
            path.name.startswith(f"{doc_id}_") for doc_id in failed_doc_ids
        ):
            print(f"保留原件 {path}（对应文档向量清理失败）")
            continue
        print(f"{prefix}删除文件 {path}")
        if not args.dry_run:
            path.unlink(missing_ok=True)

    scope = "全部" if args.all else f"早于 {days} 天"
    dir_count = len(parsed_dirs) + len(bid_dirs)
    print(f"完成：{dir_count} 个目录、{len(originals)} 个文件（范围：{scope}）")
    if failures:
        print(f"警告：{failures} 个文档向量清理失败，其文件已保留，请重新运行本脚本")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

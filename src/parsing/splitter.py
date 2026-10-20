"""PDF 均衡分片（核心算法 A5）。"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    """一个分片的页区间，页码均为 0-based 闭区间。"""

    from_page: int
    to_page: int
    page_offset: int  # 分片在原始文档中的 0-based 起始页


def plan_splits(page_count: int, max_pages: int = 200) -> list[Split]:
    """按页均衡拆分，每片不超过 max_pages，片间大小差不超过 1。"""
    if page_count <= 0:
        return []
    if page_count <= max_pages:
        return [Split(0, page_count - 1, 0)]

    parts = math.ceil(page_count / max_pages)
    base, remainder = divmod(page_count, parts)

    splits: list[Split] = []
    start = 0
    for i in range(parts):
        size = base + (1 if i < remainder else 0)
        splits.append(Split(start, start + size - 1, start))
        start += size
    return splits

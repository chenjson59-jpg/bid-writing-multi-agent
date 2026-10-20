"""PDF 均衡分片（A5）测试。"""
from src.parsing.splitter import Split, plan_splits


def test_empty_or_zero_pages():
    assert plan_splits(0) == []
    assert plan_splits(-1) == []


def test_single_shard_within_limit():
    splits = plan_splits(100, max_pages=200)
    assert splits == [Split(0, 99, 0)]


def test_exact_limit():
    splits = plan_splits(200, max_pages=200)
    assert splits == [Split(0, 199, 0)]


def test_even_split():
    splits = plan_splits(450, max_pages=200)
    assert len(splits) == 3
    assert [s.from_page for s in splits] == [0, 150, 300]
    assert [s.to_page for s in splits] == [149, 299, 449]
    assert [s.page_offset for s in splits] == [0, 150, 300]


def test_uneven_split_covers_all_pages():
    page_count = 401
    splits = plan_splits(page_count, max_pages=200)
    # 每片不超过上限，且片间大小差不超过 1
    sizes = [s.to_page - s.from_page + 1 for s in splits]
    assert all(size <= 200 for size in sizes)
    assert max(sizes) - min(sizes) <= 1
    # 覆盖所有页、无重叠、无空洞
    covered = sorted(
        page for s in splits for page in range(s.from_page, s.to_page + 1)
    )
    assert covered == list(range(page_count))

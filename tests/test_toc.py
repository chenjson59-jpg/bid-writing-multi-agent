"""书签读取、页面归属与章节组装（A1–A4、A24）测试。"""
from src.parsing.toc import (
    ancestor_titles,
    assign_pages_to_entries,
    chapter_indices,
    compose_sections,
    looks_like_toc_page,
    read_toc,
    section_number,
    subtree_range,
    subtree_text,
)


def test_read_toc_page_ranges():
    raw = [
        (1, "第一章 投标邀请", 1),
        (2, "1.1 概述", 3),
        (1, "第二章 投标人须知", 5),
    ]
    entries = read_toc(raw, page_count=10)
    assert len(entries) == 3
    # 第一章：后继同级第二章从第 5 页开始，故到第 4 页
    assert entries[0].page == 1 and entries[0].page_end == 4
    # 1.1：与第一章同区间终点，页码从 3 开始
    assert entries[1].page == 3 and entries[1].page_end == 4
    # 第二章：无后继同级，延伸到末页
    assert entries[2].page == 5 and entries[2].page_end == 10


def test_read_toc_clamps_out_of_range_page():
    entries = read_toc([(1, "A", 0), (1, "B", 999)], page_count=5)
    assert entries[0].page == 1
    assert entries[1].page == 5


def test_assign_pages_to_entries_deepest_entry():
    raw = [
        (1, "第一章", 1),
        (2, "1.1", 3),
        (1, "第二章", 5),
    ]
    entries = read_toc(raw, page_count=10)
    pages = {1: "封面", 3: "1.1 正文", 5: "第二章正文", 7: "第二章续", 12: "游离页"}

    assigned, uncovered = assign_pages_to_entries(entries, pages)

    # 第 1 页只被第一章覆盖
    assert assigned[0][0].endswith("封面")
    # 第 3 页同时落在第一章与 1.1，取最深的 1.1
    assert assigned[1][0].endswith("1.1 正文")
    # 第 5、7 页归属第二章
    assert assigned[2][0].endswith("第二章正文")
    assert assigned[2][1].endswith("第二章续")
    # 第 12 页超出所有书签区间，进入 uncovered
    assert uncovered and uncovered[0].endswith("游离页")


def test_compose_sections_heading_levels():
    raw = [(1, "第一章", 1), (2, "1.1", 2)]
    entries = read_toc(raw, page_count=2)
    assigned = {0: ["<!-- page: 1 -->\n第一章正文"], 1: ["<!-- page: 2 -->\n1.1正文"]}

    markdown, sections = compose_sections(entries, assigned, [])

    assert "## 第一章" in markdown
    assert "### 1.1" in markdown
    assert "第一章正文" in markdown
    assert "1.1正文" in markdown
    assert len(sections) == 2
    assert sections[0].text == "<!-- page: 1 -->\n第一章正文"
    assert sections[1].path == ["第一章"]


def test_compose_sections_uncovered_first():
    raw = [(1, "第一章", 1)]
    entries = read_toc(raw, page_count=1)
    markdown, _ = compose_sections(entries, {0: []}, ["<!-- page: 1 -->\n未覆盖"])

    assert markdown.startswith("<!-- page: 1 -->\n未覆盖")


def test_looks_like_toc_page():
    toc_text = "第一章 投标邀请....2\n第二章 投标人须知....5\n第三章 评标办法....10"
    assert looks_like_toc_page(toc_text) is True

    normal = "本项目采购内容如下\n具体见正文说明\n无引导点内容"
    assert looks_like_toc_page(normal) is False


def test_section_number():
    assert section_number("第一章 投标邀请") == "第一章"
    assert section_number("1.2 概述") == "1.2"
    assert section_number("附件一 格式") == "附件一"
    assert section_number("（一）说明") == "（一）"
    assert section_number("普通标题") == ""


def test_ancestor_titles():
    raw = [(1, "A", 1), (2, "B", 2), (3, "C", 3), (1, "D", 10)]
    entries = read_toc(raw, page_count=10)
    assert ancestor_titles(entries, 2) == ["A", "B"]
    assert ancestor_titles(entries, 0) == []
    assert ancestor_titles(entries, 3) == []


def test_subtree_range():
    raw = [(1, "A", 1), (2, "B", 2), (3, "C", 3), (1, "D", 10)]
    entries = read_toc(raw, page_count=10)
    assert subtree_range(entries, 0) == (0, 3)
    assert subtree_range(entries, 1) == (1, 3)
    assert subtree_range(entries, 2) == (2, 3)
    assert subtree_range(entries, 3) == (3, 4)


def test_subtree_text_joins_descendants():
    from types import SimpleNamespace

    raw = [(1, "A", 1), (2, "B", 2), (1, "C", 3)]
    entries = read_toc(raw, page_count=3)
    sections = [
        SimpleNamespace(text="root"),
        SimpleNamespace(text="child"),
        SimpleNamespace(text="sibling"),
    ]
    assert subtree_text(sections, entries, 0) == "root\n\nchild"


def test_chapter_indices():
    raw = [(1, "第一章", 1), (2, "1.1", 3), (1, "附件一", 5)]
    entries = read_toc(raw, page_count=5)
    assert chapter_indices(entries) == [0]

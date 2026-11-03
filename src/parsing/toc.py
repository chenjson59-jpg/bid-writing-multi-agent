"""书签树读取、页面归属与章节组装（核心算法 A1–A4、A24）。

书签是招标文件唯一可靠的结构来源（目录页会污染正则标题识别）。
"""
from __future__ import annotations

import re

from src.parsing.models import Section, TocEntry

# 目录页引导点 + 页码（如「第一章 投标邀请....2」）
TOC_LEADER_RE = re.compile(r"[.．·]{3,}\s*\d+\s*$")
# 目录条目：标题 + 引导点 + 印刷页码
_TOC_ENTRY_RE = re.compile(r"^(.*?)[.．·]{3,}\s*(\d{1,4})\s*$")

# 章节编号前缀，按优先级匹配
SECTION_NUMBER_RES = [
    re.compile(r"^第[一二三四五六七八九十百]+[章卷节]"),
    re.compile(r"^附件[一二三四五六七八九十\d]+"),
    re.compile(r"^表\d+"),
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^\d+(?:\.\d+)*(?=[.、\s])"),
    re.compile(r"^[（(][一二三四五六七八九十\d]+[）)]"),
]

# 仅匹配「第X章」，作为抽取 map 起点
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百]+章")


def _toc_lines_to_raw(text: str) -> list[tuple[int, str, int]]:
    """把目录页文本解析为 `(层级, 标题, 印刷页码)`。

    兼容两种排版：标题与引导点同行；编号（如「第一章」）单独成行、
    标题在下一行。
    """
    raw: list[tuple[int, str, int]] = []
    pending_number: str | None = None

    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        match = _TOC_ENTRY_RE.match(line)
        if not match:
            if len(line) <= 12 and SECTION_NUMBER_RES[0].match(line):
                pending_number = line
            continue
        title = match.group(1).strip()
        page = int(match.group(2))
        if pending_number:
            title = f"{pending_number} {title}".strip()
            pending_number = None
        if not title:
            continue
        raw.append((1, title, page))

    return raw


def _printed_to_pdf_offset(
    raw: list[tuple[int, str, int]], pages: dict[int, str]
) -> int | None:
    """探测印刷页码与 PDF 页码的偏移量（前置目录页数）。

    目录页自身也含章节名，故排除形似目录的页面，避免偏移被判成 0。
    """
    candidates = range(0, 5)
    best_offset: int | None = None
    best_score = 0

    for offset in candidates:
        score = 0
        for _level, title, page in raw:
            pdf_page = page + offset
            text = pages.get(pdf_page, "")
            if not text or looks_like_toc_page(text):
                continue
            core = SECTION_NUMBER_RES[0].sub("", title).strip()
            if core and core[:12] in text:
                score += 1
        if score > best_score:
            best_offset, best_score = offset, score

    # 至少半数目录条目能定位到正文页，才认为偏移可信
    if best_score >= max(1, len(raw) // 2):
        return best_offset
    return None


def reconstruct_toc(pages: dict[int, str], page_count: int) -> list[TocEntry]:
    """无书签时从目录页还原章节结构，返回与 `read_toc` 同构的条目。"""
    raw: list[tuple[int, str, int]] = []
    for page_no in sorted(pages):
        if looks_like_toc_page(pages[page_no]):
            raw.extend(_toc_lines_to_raw(pages[page_no]))
    if not raw:
        return []

    offset = _printed_to_pdf_offset(raw, pages)
    if offset is None:
        return []

    shifted = [
        (level, title, max(1, min(page + offset, page_count)))
        for level, title, page in raw
    ]
    return read_toc(shifted, page_count)


def read_toc(raw_toc: list[tuple[int, str, int]], page_count: int) -> list[TocEntry]:
    """把 `get_toc(simple=True)` 的原始结果转为带页码范围的书签条目。

    页码范围规则：节点默认延伸到文档末页；向后扫描第一个层级不深于自己的
    节点，则在该节点开始前结束。
    """
    entries: list[TocEntry] = []
    for i, (level, title, page) in enumerate(raw_toc):
        page = max(1, min(page, page_count))
        end = page_count
        for later_level, _later_title, later_page in raw_toc[i + 1 :]:
            if later_level <= level:
                end = max(page, later_page - 1)
                break
        entries.append(TocEntry(level=level, title=title, page=page, page_end=end))
    return entries


def assign_pages_to_entries(
    entries: list[TocEntry], pages: dict[int, str]
) -> tuple[dict[int, list[str]], list[str]]:
    """把页面块归属到「最深书签」，未覆盖的页面进入 uncovered。

    pages 的 key 为 1-based 页码。返回 (assigned, uncovered)，
    assigned 的 key 是书签下标。
    """
    assigned: dict[int, list[str]] = {}
    uncovered: list[str] = []

    for page_no in sorted(pages):
        text = pages[page_no]
        if not text.strip():
            continue
        block = f"<!-- page: {page_no} -->\n{text}"
        covering = [
            i
            for i, entry in enumerate(entries)
            if entry.page <= page_no <= entry.page_end
        ]
        if covering:
            best = max(covering, key=lambda i: (entries[i].level, i))
            assigned.setdefault(best, []).append(block)
        else:
            uncovered.append(block)

    return assigned, uncovered


def compose_sections(
    entries: list[TocEntry],
    assigned: dict[int, list[str]],
    uncovered: list[str],
) -> tuple[str, list[Section]]:
    """组装 Markdown 与章节列表。uncovered 内容置于 Markdown 最前。"""
    parts: list[str] = []
    if uncovered:
        parts.append("\n\n".join(uncovered))

    sections: list[Section] = []
    for i, entry in enumerate(entries):
        body = "\n\n".join(assigned.get(i, []))
        heading = "#" * min(entry.level + 1, 6) + " " + entry.title
        parts.append(f"{heading}\n\n{body}".rstrip())
        sections.append(
            Section(
                index=i,
                level=entry.level,
                number=section_number(entry.title),
                title=entry.title,
                path=ancestor_titles(entries, i),
                page_start=entry.page,
                page_end=entry.page_end,
                text=body,
            )
        )

    return "\n\n".join(parts), sections


def looks_like_toc_page(text: str) -> bool:
    """判断一页文本是否为目录页（用于分块时跳过）。"""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    leaders = sum(
        1
        for line in lines
        if TOC_LEADER_RE.search(line) or re.search(r"[.．]{6,}", line)
    )
    return leaders >= 2 and leaders / len(lines) >= 0.3


def section_number(title: str) -> str:
    """提取标题的编号前缀，如「第一章」「附件2」「1.1」，无则返回空串。"""
    for pattern in SECTION_NUMBER_RES:
        m = pattern.match(title)
        if m:
            return m.group(0)
    return ""


def ancestor_titles(entries: list[TocEntry], index: int) -> list[str]:
    """返回从根到父节点的标题链（不含自身）。"""
    chain: list[str] = []
    target = entries[index].level - 1
    for entry in reversed(entries[:index]):
        if entry.level == target:
            chain.append(entry.title)
            target -= 1
            if target < 1:
                break
    chain.reverse()
    return chain


def subtree_range(entries: list[TocEntry], index: int) -> tuple[int, int]:
    """返回节点子树的左闭右开区间 [index, end)。"""
    level = entries[index].level
    for j in range(index + 1, len(entries)):
        if entries[j].level <= level:
            return index, j
    return index, len(entries)


def subtree_text(
    sections: list[Section], entries: list[TocEntry], index: int
) -> str:
    """拼接子树内所有非空章节正文。"""
    start, end = subtree_range(entries, index)
    parts = [sections[i].text for i in range(start, end) if sections[i].text]
    return "\n\n".join(parts)


def chapter_indices(entries: list[TocEntry]) -> list[int]:
    """返回标题形如「第X章」的节点下标，作为抽取的 map 起点。"""
    return [i for i, e in enumerate(entries) if CHAPTER_RE.match(e.title)]

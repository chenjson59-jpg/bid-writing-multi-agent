"""结构化抽取（A9–A14）测试。"""
import json

import pytest

from src.errors import ExtractError
from src.extraction.extractor import (
    MIN_BISECT_CHARS,
    ExtractStats,
    Unit,
    _ensure_bid_format_items,
    _extract_unit,
    build_units,
    ground_check,
    merge_dict,
    merge_list,
    merge_partials,
)
from src.extraction.prompts import focus_for_chapter
from src.extraction.schema import (
    normalize_bool,
    normalize_category,
    normalize_chapter_extraction,
    normalize_number,
    normalize_str_list,
)
from src.parsing.models import Section, TocEntry

# 一段合法的模型 JSON 输出
_OK_JSON = json.dumps(
    {
        "basic": {"project_name": "测试项目"},
        "evaluation": {"factors": [], "preliminary_review": [], "score_table": []},
        "qualification": {"items": []},
        "bid_format": {"items": []},
        "contract": {"highlights": []},
        "tech": {"requirements": []},
    },
    ensure_ascii=False,
)


def _ok_result() -> dict:
    return {"content": _OK_JSON, "finish_reason": "stop", "json_mode_used": True}


def _sections() -> list[Section]:
    return [
        Section(index=0, level=1, number="第一章", title="第一章 投标邀请",
                path=[], page_start=1, page_end=1, text="项目名称为测试项目。"),
        Section(index=1, level=1, number="第二章", title="第二章 评标办法",
                path=[], page_start=2, page_end=2, text="价格因素为30分。"),
    ]


def _entries() -> list[TocEntry]:
    return [
        TocEntry(level=1, title="第一章 投标邀请", page=1, page_end=1),
        TocEntry(level=1, title="第二章 评标办法", page=2, page_end=2),
    ]


# ---- A9 单元切分 ----


def test_build_units_empty_raises():
    with pytest.raises(ExtractError):
        build_units([], [], markdown="")


def test_build_units_by_chapters():
    units = build_units(_sections(), _entries())
    assert len(units) == 2
    assert units[0].title == "第一章 投标邀请"
    assert "测试项目" in units[0].text


def test_build_units_no_chapter_fallback():
    units = build_units([], [], markdown="第一段\n\n第二段")
    assert len(units) == 1
    assert units[0].title == "全文"
    assert "第一段" in units[0].text


# ---- A10 章节重点路由 ----


def test_focus_routing_priority():
    assert focus_for_chapter([], "第三章 评标办法") == "evaluation"
    assert focus_for_chapter([], "第八章 投标文件格式") == "bid_format"
    assert focus_for_chapter(["第七章 合同"], "合同技术条款") == "tech"
    assert focus_for_chapter([], "第五章 合同条款") == "contract"
    assert focus_for_chapter([], "第二章 投标人须知") == "qualification"
    assert focus_for_chapter([], "第一章 采购需求") == "basic"


# ---- A11 宽容归一化 ----


def test_normalize_number():
    assert normalize_number("30分") == 30.0
    assert normalize_number(45) == 45.0
    assert normalize_number(None) is None
    assert normalize_number(True) is None
    assert normalize_number("abc") is None


def test_normalize_bool():
    assert normalize_bool("是") is True
    assert normalize_bool("否") is False
    assert normalize_bool(True) is True
    assert normalize_bool(None) is None
    assert normalize_bool("未知") is None


def test_normalize_str_list():
    assert normalize_str_list(None) == []
    assert normalize_str_list("abc") == ["abc"]
    assert normalize_str_list(["a", None, "b"]) == ["a", "b"]


def test_normalize_category():
    categories = ("资质", "财务", "业绩")
    assert normalize_category("资质要求", categories) == "资质"
    assert normalize_category("其他", categories) == "其他"


def test_normalize_chapter_extraction_expands_basic_container():
    raw = {
        "project": {"project_name": "某采购项目", "tender_no": "X-1"},
        "evaluation": {
            "factors": [{"name": "价格分", "score": "30分", "source_quote": "价格30分"}]
        },
    }
    norm, _warnings = normalize_chapter_extraction(raw)

    assert norm["basic"]["project_name"] == "某采购项目"
    assert norm["basic"]["tender_no"] == "X-1"
    assert norm["evaluation"]["factors"][0]["score"] == 30.0
    assert norm["qualification"]["items"] == []


# ---- A12 结果合并 ----


def test_merge_list_dedup():
    base = [{"name": "价格", "score": 30}, {"name": "技术", "score": 40}]
    extra = [{"name": "价格", "score": 30}, {"name": "商务", "score": 20}]

    merged = merge_list(base, extra)

    assert len(merged) == 3
    assert [m["name"] for m in merged] == ["价格", "技术", "商务"]


def test_merge_dict_scalar_first_wins():
    base = {"basic": {"project_name": "甲项目"}}
    extra = {"basic": {"project_name": "乙项目", "tender_no": "T-1"}}

    merged = merge_dict(base, extra)

    assert merged["basic"]["project_name"] == "甲项目"  # 先到先得
    assert merged["basic"]["tender_no"] == "T-1"


def test_merge_partials_accumulates():
    p1 = {"basic": {"project_name": "甲"}, "evaluation": {"factors": [{"name": "价格"}]}}
    p2 = {"evaluation": {"factors": [{"name": "技术"}]}}

    merged = merge_partials([p1, p2])

    assert merged["basic"]["project_name"] == "甲"
    assert [f["name"] for f in merged["evaluation"]["factors"]] == ["价格", "技术"]


# ---- A13 溯源核对 ----


def test_ground_check_normalizes_pipes_and_whitespace():
    doc = {"project_name": "智慧城市 建设项目", "evaluation_factors": [], "qualifications": []}
    full_text = "智慧城市｜建设项目"

    assert ground_check(doc, full_text) == []


def test_ground_check_reports_unverified():
    doc = {"budget_amount": "预算 100 万", "evaluation_factors": [], "qualifications": []}
    full_text = "本项目预算为壹佰万元"

    assert "预算金额" in ground_check(doc, full_text)


def test_ground_check_factor_quote():
    doc = {
        "evaluation_factors": [{"source_quote": "价格分30分"}],
        "qualifications": [],
    }
    full_text = "价格分30分"

    assert ground_check(doc, full_text) == []


# ---- 投标格式兜底（6.8）----

_EMPTY_PARTIAL = {
    "basic": {"project_name": ""},
    "evaluation": {"factors": [], "preliminary_review": [], "score_table": []},
    "qualification": {"items": []},
    "bid_format": {"items": []},
    "contract": {"highlights": []},
    "tech": {"requirements": []},
}


def test_ensure_bid_format_item_fills_blank():
    result = _ensure_bid_format_items([_EMPTY_PARTIAL], ["第八章 投标文件格式"], "法定代表人身份证明")
    assert result[0]["bid_format"]["items"] == [{"title": "法定代表人身份证明"}]


def test_ensure_bid_format_item_skips_chapter_title():
    result = _ensure_bid_format_items([_EMPTY_PARTIAL], [], "第八章 投标文件格式")
    assert result[0]["bid_format"]["items"] == []


def test_ensure_bid_format_item_skips_when_present():
    partial = {**_EMPTY_PARTIAL, "bid_format": {"items": [{"title": "已有条目"}]}}
    result = _ensure_bid_format_items([partial], ["第八章 投标文件格式"], "法定代表人身份证明")
    assert result[0]["bid_format"]["items"] == [{"title": "已有条目"}]


# ---- A14 截断二分 ----


class _TruncatingCaller:
    """第一次返回截断，之后返回正常结果。"""

    def __init__(self):
        self.calls = 0

    def generate(self, messages, json_mode):
        self.calls += 1
        if self.calls == 1:
            return {"content": "", "finish_reason": "length", "json_mode_used": True}
        return _ok_result()


def test_extract_unit_bisects_on_truncation():
    caller = _TruncatingCaller()
    unit = Unit("长章节", [], "章" * MIN_BISECT_CHARS)
    stats = ExtractStats()

    result = _extract_unit(unit, caller, stats)

    assert len(result) == 2  # 二分后上下两部分
    assert caller.calls == 3
    assert stats.calls == 3


def test_extract_unit_short_text_no_bisect():
    caller = _TruncatingCaller()
    unit = Unit("短章节", [], "短" * (MIN_BISECT_CHARS - 100))
    stats = ExtractStats()

    with pytest.raises(ExtractError):
        _extract_unit(unit, caller, stats)
"""WriteOptions.from_dict 回归：显式 0/False 必须保留，只有缺键和 None 才回退默认值。

背景：旧实现用 `raw.get(key, default) or default`，导致合法的 0 被默认值覆盖
（max_revisions=0 变成 1、min_score=0 变成 75），与 API 层 ge=0 的契约冲突。
"""
from __future__ import annotations

import pytest

from src.bid_writer.schema import WriteOptions

DEFAULTS = WriteOptions()


def test_none_and_empty_input_use_dataclass_defaults():
    assert WriteOptions.from_dict(None) == DEFAULTS
    assert WriteOptions.from_dict({}) == DEFAULTS


def test_explicit_zero_values_are_preserved():
    options = WriteOptions.from_dict(
        {
            "max_revisions": 0,
            "min_score": 0,
            "max_sections": 0,
            "target_words": 0,
            "top_k": 0,
        }
    )
    assert options.max_revisions == 0
    assert options.min_score == 0
    assert options.max_sections == 0
    assert options.target_words == 0
    assert options.top_k == 0


def test_explicit_none_falls_back_to_default_but_keeps_other_values():
    options = WriteOptions.from_dict(
        {"max_revisions": None, "min_score": 0, "top_k": 3}
    )
    assert options.max_revisions == DEFAULTS.max_revisions
    assert options.min_score == 0
    assert options.top_k == 3


def test_explicit_false_is_preserved():
    assert WriteOptions.from_dict({"export_word": False}).export_word is False
    assert WriteOptions.from_dict({"export_word": None}).export_word is True


def test_normal_values_pass_through():
    options = WriteOptions.from_dict(
        {
            "max_revisions": 2,
            "min_score": 88,
            "max_sections": 10,
            "export_word": False,
            "target_words": 2000,
            "top_k": 8,
        }
    )
    assert options == WriteOptions(
        max_revisions=2,
        min_score=88,
        max_sections=10,
        export_word=False,
        target_words=2000,
        top_k=8,
    )


def test_round_trip_through_dict_is_stable():
    """产物持久化后再读回（sections.rebuild_artifact 路径）必须保持原值。"""
    original = WriteOptions(
        max_revisions=0, min_score=0, target_words=0, top_k=0, export_word=False
    )
    assert WriteOptions.from_dict(original.__dict__) == original


@pytest.mark.parametrize(
    ("field", "zero_value"),
    [
        ("max_revisions", 0),
        ("min_score", 0),
        ("target_words", 0),
        ("top_k", 0),
    ],
)
def test_no_field_uses_truthiness_default(field, zero_value):
    options = WriteOptions.from_dict({field: zero_value})
    assert getattr(options, field) == zero_value

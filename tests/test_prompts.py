"""Prompt 回归：JSON 输出示例必须真实包含在提示词文本中，且 format 不报错。"""

from src.bid_writer.prompts import _PLANNER_SYSTEM, _REVIEWER_SYSTEM


def test_planner_prompt_contains_json_example_and_formats():
    rendered = _PLANNER_SYSTEM.format(max_sections=8)
    assert '"sections"' in rendered
    assert '{"title": "章节标题"' in rendered
    assert "不超过 8 个" in rendered


def test_reviewer_prompt_contains_json_example():
    assert '"covered_points"' in _REVIEWER_SYSTEM
    assert '"placeholder_count"' in _REVIEWER_SYSTEM

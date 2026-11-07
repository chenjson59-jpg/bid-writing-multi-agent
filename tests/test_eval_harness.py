"""T-020 eval harness 机械正确性测试：用内联迷你样本，不依赖 eval/ 数据集与网络。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.harness.loader import GoldenCaller, load_sample, markdown_to_parsed_doc
from eval.harness.runner import project_document, run_sample

MARKDOWN = """# 第一章 投标邀请

项目名称：测试采购项目。
采购编号：TEST-2026-001。

# 第二章 评标办法

价格因素为 30 分，技术因素为 20 分。
"""

META = {
    "doc_id": "evaltest00000001",
    "doc_type": "tender_file",
    "parser": "local",
    "source_name": "golden_test.pdf",
    "scenario": "内联迷你样本",
}


def _fixture(content: dict) -> dict:
    return {
        "content": json.dumps(content, ensure_ascii=False),
        "finish_reason": "stop",
        "json_mode_used": True,
    }


def test_markdown_to_parsed_doc_splits_chapters():
    doc = markdown_to_parsed_doc(MARKDOWN, META)
    assert doc.doc_id == "evaltest00000001"
    assert [e.title for e in doc.toc] == ["第一章 投标邀请", "第二章 评标办法"]
    assert len(doc.sections) == 2
    assert "TEST-2026-001" in doc.sections[0].text
    assert all(e.level == 1 for e in doc.toc)
    # markdown 全文保留，供溯源核对使用
    assert "价格因素为 30 分" in doc.markdown


def test_golden_caller_consumes_queue_and_supports_retry():
    fixtures = {
        "第一章 投标邀请": [
            _fixture({"basic": {}}),  # 空结果会触发 _extract_unit 重试一次
            _fixture({"basic": {"project_name": "测试采购项目"}}),
        ],
    }
    caller = GoldenCaller(fixtures)
    messages = [{"role": "user", "content": "章节：第一章 投标邀请\n本章重点提取：基础信息。"}]
    first = caller.generate(messages, json_mode=True)
    second = caller.generate(messages, json_mode=True)
    assert json.loads(first["content"]) == {"basic": {}}
    assert "测试采购项目" in second["content"]
    # 队列耗尽后再次调用应显式报错，而不是静默返回空
    with pytest.raises(KeyError, match="第一章 投标邀请"):
        caller.generate(messages, json_mode=True)


def test_golden_caller_unknown_title_raises():
    caller = GoldenCaller({"第一章 投标邀请": [_fixture({"basic": {}})]})
    messages = [{"role": "user", "content": "章节：第九章 不存在\n正文"}]
    with pytest.raises(KeyError, match="第九章 不存在"):
        caller.generate(messages, json_mode=True)


def test_project_document_drops_volatile_meta():
    projected = project_document(
        {
            "doc_id": "x",
            "project_name": "项目",
            "extraction_meta": {
                "model": "deepseek",
                "prompt_version": "v5",
                "calls": 2,
                "cache_hit": False,
                "elapsed_seconds": 1.23,
                "content_sha256": "abc",
                "ungrounded_fields": [],
                "warnings": [],
                "json_mode_used": True,
            },
        }
    )
    meta = projected["extraction_meta"]
    assert "elapsed_seconds" not in meta
    assert "cache_hit" not in meta
    assert "model" not in meta
    assert "content_sha256" not in meta
    assert meta["calls"] == 2
    assert meta["prompt_version"] == "v5"


def _write_sample(root: Path) -> Path:
    sample_dir = root / "mini"
    sample_dir.mkdir()
    (sample_dir / "meta.json").write_text(json.dumps(META, ensure_ascii=False), encoding="utf-8")
    (sample_dir / "tender.md").write_text(MARKDOWN, encoding="utf-8")
    fixtures = {
        "第一章 投标邀请": [
            _fixture({"basic": {}}),
            _fixture(
                {
                    "basic": {
                        "project_name": "测试采购项目",
                        "tender_no": "TEST-2026-001",
                    }
                }
            ),
        ],
        "第二章 评标办法": [
            _fixture(
                {
                    "evaluation": {
                        "factors": [
                            {"category": "报价", "name": "价格因素", "score": "30分",
                             "source_quote": "价格因素为 30 分"},
                            {"category": "技术", "name": "技术因素", "score": 20,
                             "source_quote": "技术因素为 20 分"},
                        ]
                    }
                }
            )
        ],
    }
    (sample_dir / "llm_fixtures.json").write_text(
        json.dumps(fixtures, ensure_ascii=False), encoding="utf-8"
    )
    return sample_dir


def test_run_sample_matches_expected(tmp_path):
    sample_dir = _write_sample(tmp_path)
    expected = {
        "doc_id": "evaltest00000001",
        "doc_type": "tender_file",
        "project_name": "测试采购项目",
        "tender_no": "TEST-2026-001",
        "budget_amount": "",
        "bid_deadline": "",
        "bid_open_place": "",
        "evaluation_factors": [
            {"category": "报价", "name": "价格因素", "score": 30.0,
             "source_quote": "价格因素为 30 分"},
            {"category": "技术", "name": "技术因素", "score": 20.0,
             "source_quote": "技术因素为 20 分"},
        ],
        "preliminary_review": [],
        "score_table": [],
        "qualifications": [],
        "bid_format_items": [],
        "contract_highlights": [],
        "technical_requirements": [],
        "service_requirements": [],
        "extraction_meta": {
            "prompt_version": "v5",
            "parser": "local",
            "calls": 3,
            "json_mode_used": True,
            "ungrounded_fields": [],
            "warnings": [],
        },
    }
    (sample_dir / "expected_extraction.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = run_sample(load_sample(sample_dir), tmp_path / "work")
    assert result.passed is True, result.diffs


def test_run_sample_reports_mismatch(tmp_path):
    sample_dir = _write_sample(tmp_path)
    wrong = {
        "doc_id": "evaltest00000001",
        "doc_type": "tender_file",
        "project_name": "错误的项目名",
        "extraction_meta": {"calls": 99},
    }
    (sample_dir / "expected_extraction.json").write_text(
        json.dumps(wrong, ensure_ascii=False), encoding="utf-8"
    )
    result = run_sample(load_sample(sample_dir), tmp_path / "work")
    assert result.passed is False
    assert any("project_name" in d for d in result.diffs)

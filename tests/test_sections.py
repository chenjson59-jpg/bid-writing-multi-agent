"""章节级编辑、重写与锁定（P1-2）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bid_writer import sections, supplement
from src.bid_writer.schema import BidderProfile
from src.errors import BidWriteError
from src.extraction.schema import TenderDocument

BID_ID = "0123456789abcdef-20260101-000000"

_MARKDOWN = (
    "# 示例项目\n\n**投标人：测试公司**\n**日期：2026年01月01日**\n\n---\n\n"
    "## 目录\n1. 施工组织设计\n2. 项目管理机构\n\n"
    "## 施工组织设计\n\n原始正文一\n\n"
    "## 项目管理机构\n\n原始正文二\n"
)


class EchoCaller:
    """固定正文模型调用，用于章节重写。"""

    def __init__(self, content: str = "重写后的正文") -> None:
        self.content = content

    def generate(self, messages, json_mode):
        return {
            "content": self.content,
            "finish_reason": "stop",
            "json_mode_used": json_mode,
            "model": "fake-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }


def make_tender() -> TenderDocument:
    return TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="示例项目",
        evaluation_factors=[{"name": "施工组织设计", "category": "技术", "score": 40}],
        bid_format_items=[],
    )


def _outline() -> list[dict]:
    return [
        {
            "id": "section_1",
            "title": "施工组织设计",
            "order": 1,
            "strategy": "generate",
            "target_words": 100,
            "scoring_factors": ["施工组织设计"],
            "requirements": "",
            "retrieval_query": "施工组织设计",
        },
        {
            "id": "section_2",
            "title": "项目管理机构",
            "order": 2,
            "strategy": "generate",
            "target_words": 100,
            "scoring_factors": [],
            "requirements": "",
            "retrieval_query": "项目管理机构",
        },
    ]


def _make_bid_dir(root: Path) -> Path:
    """构造一份旧版产物（没有 sections/），用于验证反推补齐。"""
    bid_dir = root / BID_ID
    bid_dir.mkdir(parents=True)
    report = {
        "doc_id": "0123456789abcdef",
        "project_name": "示例项目",
        "bidder": "测试公司",
        "generated_at": "2026-01-01T00:00:00",
        "total_sections": 2,
        "total_words": 10,
        "sections": [
            {"id": "section_1", "score_before": 80, "score_after": 85, "revisions": 1},
            {"id": "section_2", "score_before": 70, "score_after": 75, "revisions": 1},
        ],
        "format_coverage": [],
        "evaluation_coverage": [],
        "placeholders": [],
        "missing_profile_fields": [],
        "warnings": [],
        "write_options": {"max_revisions": 1, "min_score": 75, "target_words": 100},
    }
    (bid_dir / "bid.md").write_text(_MARKDOWN, encoding="utf-8")
    (bid_dir / "outline.json").write_text(
        json.dumps(_outline(), ensure_ascii=False), encoding="utf-8"
    )
    (bid_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    return bid_dir


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "bids"
    root.mkdir()
    bid_dir = _make_bid_dir(root)
    monkeypatch.setattr(sections, "BIDS_DIR", root)
    monkeypatch.setattr(supplement, "BIDS_DIR", root)
    monkeypatch.setattr(sections, "load_extraction", lambda doc_id: make_tender())
    monkeypatch.setattr(
        sections, "markdown_to_docx", lambda md, path: Path(path).write_bytes(b"docx")
    )
    monkeypatch.setattr(sections, "_retriever", lambda tender: None)
    monkeypatch.setattr(
        supplement, "_load_profile", lambda name: BidderProfile(company_name=name)
    )
    return bid_dir


def test_ensure_sections_derives_from_markdown(env):
    infos = sections.section_infos(env)

    assert [info["id"] for info in infos] == ["section_1", "section_2"]
    assert all(info["locked"] is False for info in infos)
    assert (env / "sections" / "index.json").is_file()
    assert (env / "sections" / "01-section_1.md").is_file()


def test_save_section_updates_only_target(env):
    report = sections.save_section(env, "section_1", "人工改写后的正文")

    markdown = (env / "bid.md").read_text(encoding="utf-8")
    assert "人工改写后的正文" in markdown
    assert "原始正文一" not in markdown
    assert "原始正文二" in markdown  # 其他章节不受影响
    assert any("已人工编辑" in w for w in report.warnings)
    assert report.updated_at


def test_save_section_lock_and_unlock(env):
    sections.save_section(env, "section_2", locked=True)
    infos = {info["id"]: info for info in sections.section_infos(env)}
    assert infos["section_2"]["locked"] is True
    assert infos["section_1"]["locked"] is False

    sections.save_section(env, "section_2", locked=False)
    infos = {info["id"]: info for info in sections.section_infos(env)}
    assert infos["section_2"]["locked"] is False


def test_rewrite_skips_locked_sections(env):
    sections.save_section(env, "section_1", locked=True)

    report = sections.rewrite_sections(BID_ID, caller=EchoCaller())

    sec1 = (env / "sections" / "01-section_1.md").read_text(encoding="utf-8")
    sec2 = (env / "sections" / "02-section_2.md").read_text(encoding="utf-8")
    assert "原始正文一" in sec1  # 锁定章节保持不变
    assert "重写后的正文" in sec2
    assert any("跳过 1 个锁定章节" in w for w in report.warnings)
    assert report.llm_usage["calls"] > 0


def test_rewrite_all_locked_raises(env):
    sections.save_section(env, "section_1", locked=True)
    sections.save_section(env, "section_2", locked=True)

    with pytest.raises(BidWriteError):
        sections.rewrite_sections(BID_ID, caller=EchoCaller())


def test_rewrite_unknown_section_raises(env):
    with pytest.raises(BidWriteError):
        sections.rewrite_sections(BID_ID, ["section_x"], caller=EchoCaller())


def test_section_detail_and_missing_body(env):
    detail = sections.section_detail(env, "section_1")
    assert detail["title"] == "施工组织设计"
    assert "原始正文一" in detail["markdown"]

    (env / "sections" / "01-section_1.md").unlink()
    with pytest.raises(BidWriteError):
        sections.section_detail(env, "section_1")

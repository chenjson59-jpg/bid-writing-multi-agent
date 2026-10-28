"""未覆盖评分因素补写（P1-1）与模型用量计量（P1-5）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bid_writer import supplement
from src.bid_writer.schema import BidderProfile
from src.bid_writer.usage import UsageMeter
from src.errors import BidWriteError
from src.extraction.schema import TenderDocument

BID_ID = "0123456789abcdef-20260101-000000"


class EchoCaller:
    """返回固定正文并附带 usage，用于计量与写作。"""

    def __init__(self, content: str = "补充说明正文", usage: dict | None = None) -> None:
        self.content = content
        self.usage = usage or {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        self.calls = 0

    def generate(self, messages, json_mode):
        self.calls += 1
        return {
            "content": self.content,
            "finish_reason": "stop",
            "json_mode_used": json_mode,
            "model": "fake-model",
            "usage": self.usage,
        }


def make_tender() -> TenderDocument:
    return TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="示例项目",
        evaluation_factors=[
            {"name": "施工组织设计", "category": "技术", "score": 40},
            {"name": "投标报价", "category": "报价", "score": 30},
        ],
        bid_format_items=[{"title": "施工组织设计", "content": ""}],
    )


def _make_bid_dir(root: Path, *, covered_all: bool = False) -> Path:
    """构造一份最小产物目录（bid.md + outline.json + report.json）。"""
    bid_dir = root / BID_ID
    bid_dir.mkdir(parents=True)

    outline = [
        {
            "id": "section_1",
            "title": "施工组织设计",
            "order": 1,
            "strategy": "generate",
            "target_words": 100,
            "scoring_factors": ["施工组织设计"],
            "requirements": "",
            "retrieval_query": "施工组织设计",
        }
    ]
    markdown = (
        "# 示例项目\n\n**投标人：测试公司**\n**日期：2026年01月01日**\n\n---\n\n"
        "## 目录\n1. 施工组织设计\n\n"
        "## 施工组织设计\n\n正文内容\n"
    )
    coverage = [
        {
            "name": "施工组织设计",
            "category": "技术",
            "score": 40,
            "covered": True,
            "section_id": "section_1",
        },
        {
            "name": "投标报价",
            "category": "报价",
            "score": 30,
            "covered": covered_all,
            "section_id": "section_1" if covered_all else None,
        },
    ]
    report = {
        "doc_id": "0123456789abcdef",
        "project_name": "示例项目",
        "bidder": "测试公司",
        "generated_at": "2026-01-01T00:00:00",
        "total_sections": 1,
        "total_words": 4,
        "sections": [
            {"id": "section_1", "score_before": 80, "score_after": 85, "revisions": 1}
        ],
        "format_coverage": [],
        "evaluation_coverage": coverage,
        "placeholders": [],
        "missing_profile_fields": [],
        "warnings": [],
    }

    (bid_dir / "bid.md").write_text(markdown, encoding="utf-8")
    (bid_dir / "outline.json").write_text(
        json.dumps(outline, ensure_ascii=False), encoding="utf-8"
    )
    (bid_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    return bid_dir


@pytest.fixture
def bid_env(tmp_path, monkeypatch):
    """把补写模块的产物根目录与外部依赖重定向到临时环境。"""
    root = tmp_path / "bids"
    root.mkdir()
    _make_bid_dir(root)
    monkeypatch.setattr(supplement, "BIDS_DIR", root)
    monkeypatch.setattr(supplement, "load_extraction", lambda doc_id: make_tender())
    monkeypatch.setattr(
        supplement, "_load_profile", lambda name: BidderProfile(company_name=name)
    )
    return root


# ---- P1-5 用量计量 ----


def test_usage_meter_summary():
    meter = UsageMeter(price_input=1.0, price_output=2.0)
    caller = meter.wrap(EchoCaller())
    caller.generate([], False)
    caller.generate([], False)

    summary = meter.summary()
    assert summary["calls"] == 2
    assert summary["prompt_tokens"] == 200
    assert summary["completion_tokens"] == 100
    assert summary["total_tokens"] == 300
    assert summary["models"] == ["fake-model"]
    # 200/1000*1 + 100/1000*2 = 0.4
    assert summary["estimated_cost"] == pytest.approx(0.4)
    assert summary["cost_estimated"] is True


def test_usage_meter_without_usage_field():
    class SilentCaller:
        def generate(self, messages, json_mode):
            return {"content": "x"}

    meter = UsageMeter()
    meter.wrap(SilentCaller()).generate([], False)

    summary = meter.summary()
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 0
    assert summary["cost_estimated"] is False


# ---- P1-1 补写 ----


def test_restore_drafts_from_artifacts(bid_env):
    drafts, report_data, _bid_dir = supplement.restore_drafts(BID_ID)

    assert [d.plan.title for d in drafts] == ["施工组织设计"]
    assert drafts[0].markdown == "正文内容"
    assert drafts[0].score_after == 85
    assert report_data["bidder"] == "测试公司"


def test_supplement_adds_uncovered_factor(bid_env):
    report = supplement.run_supplement(BID_ID, caller=EchoCaller())

    titles = [section["title"] for section in report.sections]
    assert titles == ["施工组织设计", "补充：投标报价"]
    assert report.supplement_of == BID_ID

    covered = sum(1 for c in report.evaluation_coverage if c["covered"])
    assert covered == 2
    assert report.summary()["uncovered_factors"] == []
    assert report.llm_usage["calls"] >= 1
    assert report.llm_usage["models"] == ["fake-model"]

    out = Path(report.output_dir)
    assert (out / "report.json").is_file()
    assert (out / "bid.md").is_file()
    assert "补充：投标报价" in (out / "bid.md").read_text(encoding="utf-8")
    # 原产物仍保留，可与新产物对比
    assert (bid_env / BID_ID / "report.json").is_file()


def test_supplement_rejects_fully_covered(tmp_path, monkeypatch):
    root = tmp_path / "bids"
    root.mkdir()
    _make_bid_dir(root, covered_all=True)
    monkeypatch.setattr(supplement, "BIDS_DIR", root)

    with pytest.raises(BidWriteError, match="已覆盖全部评分因素"):
        supplement.run_supplement(BID_ID, caller=EchoCaller())


def test_supplement_plan_lists_names(tmp_path, monkeypatch):
    root = tmp_path / "bids"
    root.mkdir()
    _make_bid_dir(root)
    monkeypatch.setattr(supplement, "BIDS_DIR", root)

    assert supplement.supplement_plan(BID_ID) == ["投标报价"]

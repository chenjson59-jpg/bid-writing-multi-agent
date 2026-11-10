"""T-032 进度回调补点测试：bid/parse 链路消息契约、None 兼容与并发聚合。

全部离线：ScriptCaller/假检索/本地 pymupdf 解析，禁止真实 LLM/Embedding/Chroma/网络。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import ClassVar

import pymupdf
import pytest

from src.bid_writer.graph import run_write_pipeline
from src.bid_writer.schema import BidderProfile, SectionPlan, WriteOptions
from src.extraction.schema import TenderDocument

_EXTRACTION = {
    "basic": {"project_name": "测试项目", "tender_no": "T-032"},
    "evaluation": {
        "factors": [
            {"name": "施工组织设计", "category": "技术", "score": 40},
            {"name": "投标报价", "category": "报价", "score": 30},
        ],
        "preliminary_review": [],
        "score_table": [],
    },
    "qualification": {"items": []},
    "bid_format": {"items": [{"title": "投标函"}, {"title": "施工组织设计", "content": ""}]},
    "contract": {"highlights": []},
    "tech": {"requirements": []},
}


class ScriptCaller:
    """按脚本顺序返回预设响应，记录 json_mode；队列耗尽后返回空内容。"""

    def __init__(self, script: list[tuple[bool, str]]):
        self.script = list(script)
        self.calls: list[bool] = []

    def generate(self, messages, json_mode):
        self.calls.append(json_mode)
        content = self.script.pop(0)[1] if self.script else ""
        return {"content": content, "finish_reason": "stop", "json_mode_used": json_mode}


class RoleCaller:
    """按 json_mode 区分角色：文本调用返回固定正文，JSON 调用返回固定评审分。"""

    def __init__(self, review_score: int = 95, writer_text: str = "章节正文内容"):
        self.review_score = review_score
        self.writer_text = writer_text
        self.text_calls = 0

    def generate(self, messages, json_mode):
        if json_mode:
            content = json.dumps(
                {"score": self.review_score, "covered_points": [], "issues": [], "suggestions": []},
                ensure_ascii=False,
            )
        else:
            self.text_calls += 1
            content = self.writer_text
        return {"content": content, "finish_reason": "stop", "json_mode_used": json_mode}


class BarrierCaller:
    """writer 调用在事件屏障上等待，强制 max_workers 个线程真实并发，无 sleep。"""

    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._entered = 0
        self._lock = threading.Lock()
        self._barrier = threading.Event()

    def generate(self, messages, json_mode):
        if not json_mode:
            with self._lock:
                self._entered += 1
                if self._entered >= self._workers:
                    self._barrier.set()
            if not self._barrier.wait(timeout=5):
                raise RuntimeError("屏障等待超时，测试夹具失效")
            return {
                "content": "屏障章节正文",
                "finish_reason": "stop",
                "json_mode_used": False,
            }
        return {
            "content": json.dumps({"score": 95, "issues": [], "suggestions": []}, ensure_ascii=False),
            "finish_reason": "stop",
            "json_mode_used": True,
        }


def _tender() -> TenderDocument:
    return TenderDocument(
        doc_id="0123456789abcdef",
        doc_type="tender_file",
        project_name="示例项目",
        tender_no="示例编号",
        evaluation_factors=[{"name": "施工组织设计", "category": "技术", "score": 40}],
        bid_format_items=[{"title": "施工组织设计", "content": ""}],
    )


def _retrieve(_query: str, _top_k: int) -> list[dict]:
    return [
        {
            "id": "0123456789abcdef:0000",
            "text": "检索证据原文",
            "metadata": {"doc_id": "0123456789abcdef"},
        }
    ]


def _write_options(**overrides) -> WriteOptions:
    return WriteOptions(**overrides)


def _completion_messages(messages: list[str]) -> list[str]:
    return [m for m in messages if m.startswith("章节完成 ")]


# ---- run_write_pipeline：消息序列、修订后缀、并发聚合 ----


def test_write_pipeline_completion_messages_ordered():
    plans = [
        SectionPlan(id="p1", title="投标函", order=1, strategy="placeholder"),
        SectionPlan(id="g1", title="施工组织设计", order=2, strategy="generate"),
        SectionPlan(id="g2", title="项目管理机构", order=3, strategy="generate"),
    ]
    messages: list[str] = []

    drafts = run_write_pipeline(
        plans,
        _tender(),
        BidderProfile(),
        _write_options(),
        _retrieve,
        RoleCaller(),
        max_concurrency=3,
        report=messages.append,
    )

    assert messages[0] == "章节检索"
    completions = _completion_messages(messages)
    # 占位章同样占完成名额；严格 1/N..N/N，无重复无跳跃，且无其他消息插入
    assert completions == ["章节完成 1/3", "章节完成 2/3", "章节完成 3/3"]
    assert len(messages) == 4
    assert [d.plan.order for d in drafts] == [1, 2, 3]


def test_write_pipeline_revision_count_in_completion_message():
    plans = [SectionPlan(id="g1", title="施工组织设计", order=1, strategy="generate")]
    caller = ScriptCaller(
        [
            (False, "初稿"),
            (True, json.dumps({"score": 60, "issues": ["不足"], "suggestions": []})),
            (False, "修订稿"),
            (True, json.dumps({"score": 90, "issues": [], "suggestions": []})),
        ]
    )
    messages: list[str] = []

    drafts = run_write_pipeline(
        plans,
        _tender(),
        BidderProfile(),
        _write_options(max_revisions=1),
        _retrieve,
        caller,
        max_concurrency=1,
        report=messages.append,
    )

    assert drafts[0].revisions == 1
    assert drafts[0].markdown == "修订稿"
    assert messages == ["章节检索", "章节完成 1/1（含 1 次修订）"]


def test_write_pipeline_concurrent_counter_strict_sequence():
    total = 6
    plans = [
        SectionPlan(id=f"g{i}", title=f"技术章{i}", order=i, strategy="generate")
        for i in range(1, total + 1)
    ]
    messages: list[str] = []

    drafts = run_write_pipeline(
        plans,
        _tender(),
        BidderProfile(),
        _write_options(),
        _retrieve,
        BarrierCaller(workers=4),
        max_concurrency=4,
        report=messages.append,
    )

    assert messages[0] == "章节检索"
    assert _completion_messages(messages) == [
        f"章节完成 {i}/{total}" for i in range(1, total + 1)
    ]
    # 草稿仍按 plan.order 排序，与完成消息顺序解耦
    assert [d.plan.order for d in drafts] == list(range(1, total + 1))


def test_write_pipeline_none_report_matches_omitted():
    plans = [
        SectionPlan(id="p1", title="投标函", order=1, strategy="placeholder"),
        SectionPlan(id="g1", title="施工组织设计", order=2, strategy="generate"),
    ]
    common = (plans, _tender(), BidderProfile(), _write_options(), _retrieve, RoleCaller())

    with_report_none = run_write_pipeline(*common, max_concurrency=2, report=None)
    without_report = run_write_pipeline(*common, max_concurrency=2)

    def signature(drafts):
        return [(d.plan.id, d.markdown, d.revisions, d.score_after) for d in drafts]

    assert signature(with_report_none) == signature(without_report)


def test_write_pipeline_empty_plans_only_emits_retrieval():
    messages: list[str] = []
    drafts = run_write_pipeline(
        [],
        _tender(),
        BidderProfile(),
        _write_options(),
        _retrieve,
        RoleCaller(),
        max_concurrency=1,
        report=messages.append,
    )
    assert drafts == []
    assert messages == ["章节检索"]


# ---- run_bid_writer：端到端离线集成（本地 PDF + 假 caller + 空检索器） ----


class _ExtractCaller:
    def generate(self, messages, json_mode):
        return {
            "content": json.dumps(_EXTRACTION, ensure_ascii=False),
            "finish_reason": "stop",
            "json_mode_used": True,
        }


class _BidCaller:
    """策划返回空走确定性兜底大纲；评审恒 95 分不触发修订。"""

    def generate(self, messages, json_mode):
        system = messages[0]["content"]
        if json_mode:
            content = ""
            if "评审" in system:
                content = json.dumps(
                    {"score": 95, "covered_points": [], "issues": [], "suggestions": []},
                    ensure_ascii=False,
                )
            return {"content": content, "finish_reason": "stop", "json_mode_used": True}
        return {
            "content": "我方完全响应招标文件要求。",
            "finish_reason": "stop",
            "json_mode_used": False,
        }


class _EmptyRetriever:
    degraded = False

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def search(self, _query, _limit=None):
        return []


@pytest.fixture
def bid_workspace(tmp_path, monkeypatch):
    from src import pipeline
    from src.bid_writer import agents
    from src.extraction.extractor import run_extraction
    from src.vectorstore import retrieval

    monkeypatch.setattr(pipeline, "FILES_DIR", tmp_path / "upload" / "files")
    monkeypatch.setattr(pipeline, "PARSED_DIR", tmp_path / "upload" / "parsed")
    monkeypatch.setattr(pipeline, "IMAGES_DIR", tmp_path / "upload" / "images")
    monkeypatch.setattr(pipeline, "BIDS_DIR", tmp_path / "upload" / "bids")

    pdf = tmp_path / "demo.pdf"
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"第{i + 1}页：施工组织设计与投标报价内容")
    doc.set_toc([[1, "第一章 投标邀请", 1], [1, "第二章 评标办法", 2]])
    doc.save(pdf)
    doc.close()

    parsed = pipeline.run_parse(pdf, parser="local")
    run_extraction(parsed, caller=_ExtractCaller(), force=True)

    monkeypatch.setattr(agents, "default_caller", lambda: _BidCaller())
    monkeypatch.setattr(retrieval, "ChromaRetriever", _EmptyRetriever)
    return parsed.doc_id


def test_run_bid_writer_full_progress_sequence(bid_workspace):
    from src.pipeline import run_bid_writer

    messages: list[str] = []
    report = run_bid_writer(
        bid_workspace,
        profile={"company_name": "测试公司"},
        options={},
        report=messages.append,
    )

    n = report.total_sections
    assert n >= 2
    assert messages[0] == f"规划完成：共 {n} 章"
    assert messages.index("章节检索") == 1
    completions = _completion_messages(messages)
    assert completions == [f"章节完成 {i}/{n}" for i in range(1, n + 1)]
    assert messages.index("汇总标书") == messages.index(completions[-1]) + 1
    assert messages[-1] == "导出 Word"
    # 全程无修订后缀（评审恒 95）
    assert not any("次修订" in m for m in messages)


def test_run_bid_writer_word_failure_emits_degradation_message(bid_workspace, monkeypatch):
    from src.bid_writer import docx_export
    from src.pipeline import run_bid_writer

    def _boom(*_args, **_kwargs):
        raise RuntimeError("模拟 docx 导出失败")

    monkeypatch.setattr(docx_export, "markdown_to_docx", _boom)
    messages: list[str] = []

    report = run_bid_writer(
        bid_workspace,
        profile={"company_name": "测试公司"},
        options={},
        report=messages.append,
    )

    assert "导出 Word" in messages
    failure = "Word 导出失败，已保留 Markdown 与报告"
    assert messages.index(failure) == messages.index("导出 Word") + 1
    # 主流程成功：报告返回、warning 保留、Markdown 落盘且无 docx
    assert any(w.startswith("Word 导出失败") for w in report.warnings)
    assert not report.docx_path
    markdown_path = report.markdown_path
    assert markdown_path and Path(markdown_path).is_file()
    assert not Path(markdown_path).with_name("bid.docx").exists()


def test_run_bid_writer_disabled_word_export_has_no_export_messages(bid_workspace):
    from src.pipeline import run_bid_writer

    messages: list[str] = []
    report = run_bid_writer(
        bid_workspace,
        profile={"company_name": "测试公司"},
        options={"export_word": False},
        report=messages.append,
    )

    assert "汇总标书" in messages
    assert not any("导出 Word" in m or "Word 导出" in m for m in messages)
    assert not report.docx_path


def test_run_bid_writer_none_report_equivalent_artifacts(bid_workspace):
    from src.pipeline import run_bid_writer

    first = run_bid_writer(
        bid_workspace, profile={"company_name": "测试公司"}, options={}, report=None
    )
    second = run_bid_writer(bid_workspace, profile={"company_name": "测试公司"}, options={})

    markdown_a = Path(first.markdown_path).read_text(encoding="utf-8")
    markdown_b = Path(second.markdown_path).read_text(encoding="utf-8")
    assert markdown_a == markdown_b
    assert first.total_sections == second.total_sections
    assert first.total_words == second.total_words
    assert [s["title"] for s in first.to_dict()["sections"]] == [
        s["title"] for s in second.to_dict()["sections"]
    ]


# ---- run_bid_task：回调透传 ----


def test_run_bid_task_passes_reporter_through(monkeypatch):
    from src import tasks

    captured: dict = {}

    class _FakeBidReport:
        def summary(self):
            return {"total_sections": 1}

    def _fake_writer(doc_id, *, profile=None, options=None, caller=None, report=None):
        captured["doc_id"] = doc_id
        captured["report"] = report
        report("规划完成：共 1 章")
        return _FakeBidReport()

    monkeypatch.setattr(tasks, "run_bid_writer", _fake_writer)
    messages: list[str] = []

    result = tasks.run_bid_task("doc-1", None, None, messages.append)

    assert messages == ["规划章节", "规划完成：共 1 章"]
    assert captured["report"] == messages.append
    assert captured["doc_id"] == "doc-1"
    assert result == {"bid": {"total_sections": 1}}


# ---- run_parse_task：参数化消息与条件分支 ----


class _FakeParsed:
    doc_id = "parsed-1"
    doc_type = "tender_file"
    parser = "local"
    pages = 3
    sections: ClassVar[list] = [object(), object()]
    source_name = "demo.pdf"


class _FakeExtraction:
    project_name = "测试项目"
    evaluation_factors: ClassVar[list] = [{"name": "因素甲"}, {"name": "因素乙"}]
    bid_format_items: ClassVar[list] = []
    qualifications: ClassVar[list] = []


class _FakeIngest:
    def to_dict(self):
        return {
            "collection": "tender_document",
            "added": 5,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
            "total": 5,
        }


def test_run_parse_task_enriched_messages(monkeypatch):
    from src import tasks

    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeParsed())
    monkeypatch.setattr(tasks, "run_extraction", lambda *a, **k: _FakeExtraction())
    monkeypatch.setattr(tasks, "ingest_document", lambda *a, **k: _FakeIngest())

    messages: list[str] = []
    result = tasks.run_parse_task(
        "demo.pdf",
        {"parser": "local", "extract": True, "ingest": True},
        messages.append,
    )

    assert messages == [
        "解析文档（3 页）",
        "结构化抽取（评分因素 2 项）",
        "建立检索索引（片段 5 条，新增 5）",
    ]
    assert result["pages"] == 3
    assert result["extraction"]["evaluation_factors"] == 2
    assert result["ingest"]["total"] == 5


def test_run_parse_task_skips_optional_stage_messages(monkeypatch):
    from src import tasks

    monkeypatch.setattr(tasks, "run_parse", lambda *a, **k: _FakeParsed())

    def _must_not_run(*_a, **_k):
        raise AssertionError("未开启 extract/ingest 时不应调用")

    monkeypatch.setattr(tasks, "run_extraction", _must_not_run)
    monkeypatch.setattr(tasks, "ingest_document", _must_not_run)

    messages: list[str] = []
    result = tasks.run_parse_task("demo.pdf", {"parser": "local"}, messages.append)

    assert messages == ["解析文档（3 页）"]
    assert result["extraction"] is None
    assert result["ingest"] is None
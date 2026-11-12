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


# ---- T-039 标书级占位符批量回填（规格 docs/specs/2026-11-12-t039-placeholder-workbench.md）----

_MARKER_MANAGER = "【待填写：项目经理】"
_MARKER_DURATION = "【待填写：工期】"
_MARKER_PRICE = "【待填写：投标报价】"


def _fill_item(marker: str, value: str, section_ids=None) -> dict:
    return {"marker": marker, "value": value, "section_ids": section_ids}


def _seed_placeholder_bodies(bid_dir: Path) -> None:
    """写入两章含占位符的正文：项目经理×2、工期×2（跨章重复）、报价×1。"""
    entries = {entry["id"]: entry for entry in sections.ensure_sections(bid_dir)}
    sections._write_body(
        bid_dir,
        entries["section_1"],
        f"# 施工组织设计\n\n{_MARKER_MANAGER}负责实施，{_MARKER_MANAGER}再次出现。\n"
        f"{_MARKER_DURATION}日历天。\n",
    )
    sections._write_body(
        bid_dir,
        entries["section_2"],
        f"# 项目管理机构\n\n{_MARKER_DURATION}见本节。\n报价：{_MARKER_PRICE}\n",
    )


def _patch_rebuild_spy(monkeypatch) -> dict[str, int]:
    """包装真实 rebuild_artifact 计数：整条填充链路只允许重建一次。"""
    counts = {"calls": 0}
    original = sections.rebuild_artifact

    def spy(bid_dir, **kwargs):
        counts["calls"] += 1
        return original(bid_dir, **kwargs)

    monkeypatch.setattr(sections, "rebuild_artifact", spy)
    return counts


def _patch_docx_export(monkeypatch) -> list[str]:
    """隔离 Word 导出并记录调用次数（兼容 rebuild 的关键字参数）。"""
    calls: list[str] = []

    def fake_docx(markdown, path, **kwargs):
        calls.append(str(path))
        Path(path).write_bytes(b"docx")

    monkeypatch.setattr(sections, "markdown_to_docx", fake_docx)
    return calls


def test_fill_placeholders_replaces_all_instances_and_rebuilds_once(env, monkeypatch):
    _seed_placeholder_bodies(env)
    rebuild_counts = _patch_rebuild_spy(monkeypatch)
    docx_calls = _patch_docx_export(monkeypatch)

    result = sections.fill_placeholders(
        env,
        [
            _fill_item(_MARKER_MANAGER, "张三"),
            _fill_item(_MARKER_DURATION, "90"),
            _fill_item(_MARKER_PRICE, "壹佰万元整"),
        ],
    )

    # 一次请求只触发一次产物重建与一次 Word 导出
    assert rebuild_counts == {"calls": 1}
    assert len(docx_calls) == 1

    body1 = (env / "sections" / "01-section_1.md").read_text(encoding="utf-8")
    body2 = (env / "sections" / "02-section_2.md").read_text(encoding="utf-8")
    # 同标记多实例一次全替换，其余章节与标记互不影响
    assert body1.count("张三") == 2 and _MARKER_MANAGER not in body1
    assert _MARKER_DURATION not in body1 and _MARKER_DURATION not in body2
    assert "90" in body1 and "90" in body2
    assert "壹佰万元整" in body2 and _MARKER_PRICE not in body2
    assert "【待填写" not in (env / "bid.md").read_text(encoding="utf-8")

    # filled 按 items 顺序返回，replacements 按实际替换实例数统计
    assert [item["marker"] for item in result["filled"]] == [
        _MARKER_MANAGER,
        _MARKER_DURATION,
        _MARKER_PRICE,
    ]
    filled = {item["marker"]: item for item in result["filled"]}
    assert filled[_MARKER_MANAGER]["replacements"] == 2
    assert filled[_MARKER_MANAGER]["section_ids"] == ["section_1"]
    assert filled[_MARKER_DURATION]["replacements"] == 2
    assert filled[_MARKER_DURATION]["section_ids"] == ["section_1", "section_2"]
    assert filled[_MARKER_PRICE]["replacements"] == 1
    assert filled[_MARKER_PRICE]["section_ids"] == ["section_2"]
    assert result["skipped_locked"] == []
    assert result["remaining_placeholder_count"] == 0
    assert result["summary"]["placeholder_count"] == 0


def test_fill_placeholders_respects_section_whitelist(env, monkeypatch):
    _seed_placeholder_bodies(env)
    _patch_rebuild_spy(monkeypatch)
    _patch_docx_export(monkeypatch)

    result = sections.fill_placeholders(
        env, [_fill_item(_MARKER_DURATION, "90", section_ids=["section_1"])]
    )

    body1 = (env / "sections" / "01-section_1.md").read_text(encoding="utf-8")
    body2 = (env / "sections" / "02-section_2.md").read_text(encoding="utf-8")
    assert _MARKER_DURATION not in body1
    assert _MARKER_DURATION in body2  # 白名单外章节保持原样
    assert result["filled"][0]["section_ids"] == ["section_1"]
    assert result["filled"][0]["replacements"] == 1
    # 项目经理、工期（section_2 残留）、报价三种标记仍在
    assert result["remaining_placeholder_count"] == 3


@pytest.mark.parametrize(
    "items",
    [
        # marker 不符合同源正则
        [_fill_item("../etc/passwd", "x")],
        # value 为空 / 纯空白
        [_fill_item(_MARKER_MANAGER, "")],
        [_fill_item(_MARKER_MANAGER, "   ")],
        # value 自身再引入占位符
        [_fill_item(_MARKER_MANAGER, f"前缀{_MARKER_DURATION}后缀")],
        # section_id 不在 index 白名单
        [_fill_item(_MARKER_MANAGER, "张三", section_ids=["section_x"])],
        # 空章节白名单
        [_fill_item(_MARKER_MANAGER, "张三", section_ids=[])],
        # 同请求 marker 重复
        [_fill_item(_MARKER_MANAGER, "张三"), _fill_item(_MARKER_MANAGER, "李四")],
        # marker 在任何章节都不存在
        [_fill_item("【待填写：不存在字段】", "x")],
        # items 为空
        [],
    ],
)
def test_fill_placeholders_validation_errors(env, items):
    _seed_placeholder_bodies(env)
    with pytest.raises(BidWriteError):
        sections.fill_placeholders(env, items)


def test_fill_placeholders_skips_locked_sections(env, monkeypatch):
    _seed_placeholder_bodies(env)
    sections.save_section(env, "section_2", locked=True)
    _patch_docx_export(monkeypatch)

    result = sections.fill_placeholders(
        env,
        [_fill_item(_MARKER_DURATION, "90"), _fill_item(_MARKER_PRICE, "壹佰万")],
    )

    body1 = (env / "sections" / "01-section_1.md").read_text(encoding="utf-8")
    body2 = (env / "sections" / "02-section_2.md").read_text(encoding="utf-8")
    assert _MARKER_DURATION not in body1  # 未锁定章正常替换
    assert _MARKER_DURATION in body2       # 锁定章原样保留
    assert _MARKER_PRICE in body2          # 仅命中锁定章 → 跳过不写

    filled = {item["marker"]: item for item in result["filled"]}
    assert filled[_MARKER_DURATION]["section_ids"] == ["section_1"]
    assert _MARKER_PRICE not in filled
    skipped = {item["marker"]: item for item in result["skipped_locked"]}
    assert skipped[_MARKER_DURATION]["section_ids"] == ["section_2"]
    assert skipped[_MARKER_PRICE]["section_ids"] == ["section_2"]
    # 部分锁定不报错：项目经理未填、工期/报价在锁定章残留，共 3 种标记
    assert result["remaining_placeholder_count"] == 3


def test_fill_placeholders_all_locked_raises(env):
    _seed_placeholder_bodies(env)
    sections.save_section(env, "section_1", locked=True)
    sections.save_section(env, "section_2", locked=True)

    with pytest.raises(BidWriteError, match="锁定"):
        sections.fill_placeholders(env, [_fill_item(_MARKER_MANAGER, "张三")])
    # 全部锁定时正文零改动
    assert _MARKER_MANAGER in (
        env / "sections" / "01-section_1.md"
    ).read_text(encoding="utf-8")


def test_fill_placeholders_lock_switch_defaults_to_unlocked(env):
    _seed_placeholder_bodies(env)

    sections.fill_placeholders(env, [_fill_item(_MARKER_PRICE, "壹佰万")])

    entries = {entry["id"]: entry for entry in sections.ensure_sections(env)}
    assert entries["section_2"]["edited"] is True
    assert entries["section_2"]["locked"] is False
    # 未实际写入的章节不加 edited 标记、不改锁定
    assert "edited" not in entries["section_1"]
    assert entries["section_1"]["locked"] is False


def test_fill_placeholders_lock_true_locks_only_written_sections(env):
    _seed_placeholder_bodies(env)

    sections.fill_placeholders(
        env, [_fill_item(_MARKER_DURATION, "90")], lock=True
    )

    entries = {entry["id"]: entry for entry in sections.ensure_sections(env)}
    assert entries["section_1"]["locked"] is True
    assert entries["section_2"]["locked"] is True
    assert entries["section_1"]["edited"] is True
    assert entries["section_2"]["edited"] is True
    report = json.loads((env / "report.json").read_text(encoding="utf-8"))
    assert any("回填" in warning for warning in report["warnings"])
    assert any("加锁" in warning and "2" in warning for warning in report["warnings"])


def test_fill_placeholders_validation_failure_writes_nothing(env, monkeypatch):
    _seed_placeholder_bodies(env)
    rebuild_counts = _patch_rebuild_spy(monkeypatch)

    section_paths = sorted((env / "sections").glob("*.md"))
    snapshot = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in section_paths}
    index_path = env / "sections" / "index.json"
    report_path = env / "report.json"
    index_snapshot = (index_path.read_bytes(), index_path.stat().st_mtime_ns)
    report_snapshot = (report_path.read_bytes(), report_path.stat().st_mtime_ns)

    with pytest.raises(BidWriteError):
        sections.fill_placeholders(env, [_fill_item("【待填写：坏标记", "x")])

    for path, (content, mtime_ns) in snapshot.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns
    assert index_path.read_bytes() == index_snapshot[0]
    assert index_path.stat().st_mtime_ns == index_snapshot[1]
    assert report_path.read_bytes() == report_snapshot[0]
    assert report_path.stat().st_mtime_ns == report_snapshot[1]
    assert not list((env / "sections").glob("*.tmp"))
    assert rebuild_counts == {"calls": 0}


def test_fill_placeholders_malicious_marker_is_text_only(env):
    _seed_placeholder_bodies(env)
    evil_marker = "【待填写：../../etc/passwd】"
    first_entry = sections.ensure_sections(env)[0]
    sections._write_body(env, first_entry, f"正文 {evil_marker} 结尾\n")

    result = sections.fill_placeholders(env, [_fill_item(evil_marker, "安全值")])

    body1 = (env / "sections" / "01-section_1.md").read_text(encoding="utf-8")
    assert evil_marker not in body1 and "安全值" in body1
    assert result["remaining_placeholder_count"] == 2
    # marker 永不参与路径拼接：产物目录下不得出现穿越/同名文件
    for path in env.rglob("*"):
        assert "passwd" not in path.name
        assert ".." not in path.name
    assert not list((env / "sections").glob("*.tmp"))

    # 不符正则的恶意 marker 直接拒绝，同样不产生任何文件访问
    with pytest.raises(BidWriteError):
        sections.fill_placeholders(env, [_fill_item("../evil", "x")])


def test_fill_placeholders_does_not_call_model_or_agent(env, monkeypatch):
    from src.bid_writer import agents

    def boom_caller():
        raise AssertionError("占位符回填不得实例化模型调用器")

    def boom_agent(*args, **kwargs):
        raise AssertionError("占位符回填不得调用写作 Agent")

    monkeypatch.setattr(agents, "default_caller", boom_caller)
    monkeypatch.setattr(agents, "section_agent", boom_agent)

    # 先准备含占位符的章节，否则会在「标记不存在」校验处提前 422，
    # 无法验证完整回填链路的零 LLM 约束
    _seed_placeholder_bodies(env)

    # 不抛 AssertionError 即证明整条链路零 LLM 调用
    sections.fill_placeholders(env, [_fill_item(_MARKER_PRICE, "壹佰万")])


def test_fill_placeholders_derives_legacy_artifact(env):
    # env 夹具本身是没有 sections/ 的旧产物
    assert not (env / "sections").exists()
    bid_md = env / "bid.md"
    bid_md.write_text(
        bid_md.read_text(encoding="utf-8") + f"\n\n{_MARKER_PRICE}\n",
        encoding="utf-8",
    )

    result = sections.fill_placeholders(env, [_fill_item(_MARKER_PRICE, "壹佰万元整")])

    assert (env / "sections" / "index.json").is_file()
    assert (env / "sections" / "02-section_2.md").is_file()
    assert result["remaining_placeholder_count"] == 0
    assert _MARKER_PRICE not in bid_md.read_text(encoding="utf-8")
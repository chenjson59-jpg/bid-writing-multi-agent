"""T-022 编写阶段离线评测指标测试：纯函数口径 + 全 Fake 端到端，零网络。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.harness.writing_metrics import (
    ARM_A,
    ARM_B,
    DELTA_PATHS,
    build_writing_report,
    citation_validity,
    compare_metrics,
    coverage_metrics,
    placeholder_metrics,
    project_volatile,
    rule_violations,
    ungrounded_signals,
)
from eval.harness.writing_runner import (
    ScriptedRetriever,
    WritingCaller,
    WritingSample,
    execute_arm,
    load_writing_sample,
    run_all,
    run_sample,
)
from src.bid_writer.schema import BidderProfile
from src.extraction.schema import TenderDocument

# ---------------------------------------------------------------------------
# 内联数据构造工具
# ---------------------------------------------------------------------------


def make_tender() -> TenderDocument:
    return TenderDocument(
        doc_id="t1doc000000001",
        doc_type="tender_file",
        project_name="测试采购项目",
        tender_no="TEST-2026-001",
        evaluation_factors=[
            {"name": "施工方案", "category": "技术", "score": 40},
            {"name": "投标报价", "category": "报价", "score": 20},
        ],
        bid_format_items=[{"title": "投标书"}],
    )


def _usage(prompt: int = 100, completion: int = 50) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def text_response(content: str, *, prompt: int = 100, completion: int = 50) -> dict:
    return {
        "content": content,
        "finish_reason": "stop",
        "json_mode_used": False,
        "usage": _usage(prompt, completion),
        "model": "fake-model",
    }


def json_response(payload: dict, *, prompt: int = 80, completion: int = 40) -> dict:
    return {
        "content": json.dumps(payload, ensure_ascii=False),
        "finish_reason": "stop",
        "json_mode_used": True,
        "usage": _usage(prompt, completion),
        "model": "fake-model",
    }


def planner_fixture() -> dict:
    return json_response(
        {
            "sections": [
                {
                    "title": "施工方案",
                    "strategy": "generate",
                    "target_words": 1200,
                    "scoring_factors": ["施工方案"],
                    "requirements": "完整响应",
                    "retrieval_query": "施工方案",
                },
                {
                    "title": "投标报价",
                    "strategy": "placeholder",
                    "target_words": 0,
                    "scoring_factors": ["投标报价"],
                    "requirements": "",
                    "retrieval_query": "投标报价",
                },
                {
                    "title": "投标书",
                    "strategy": "placeholder",
                    "target_words": 0,
                    "scoring_factors": [],
                    "requirements": "",
                    "retrieval_query": "投标书",
                },
            ]
        },
        prompt=200,
        completion=100,
    )


def review(score: int) -> dict:
    return json_response(
        {
            "score": score,
            "covered_points": [],
            "issues": [],
            "suggestions": [],
            "placeholder_count": 0,
        }
    )


def clean_fixtures(writer_content: str, reviewer_score: int = 80) -> dict:
    return {
        "planner": [planner_fixture()],
        "writer:施工方案": [text_response(writer_content)],
        "reviewer:施工方案": [review(reviewer_score)],
        "reviser:施工方案": [text_response(writer_content, prompt=180, completion=220)],
    }


def clean_retrieval() -> dict:
    doc_id = "t1doc000000001"
    return {
        "施工方案": [
            {"id": f"{doc_id}:0000", "text": "施工方案原文片段", "metadata": {"doc_id": doc_id}},
        ]
    }


def make_sample(
    fixtures: dict | None = None,
    retrieval: dict | None = None,
    *,
    config: dict | None = None,
    expected: dict | None = None,
) -> WritingSample:
    return WritingSample(
        name="inline_sample",
        tender=make_tender(),
        profile=BidderProfile(company_name="测试公司"),
        fixtures=fixtures or clean_fixtures("依据原文[1]展开施工方案。"),
        retrieval=retrieval if retrieval is not None else clean_retrieval(),
        config=config or {},
        expected=expected
        or {
            "expected_placeholders": {
                "present": ["【待填写：投标报价】", "【待填写：投标书】"],
                "absent": ["【待填写：主讲教师】"],
            }
        },
    )


# ---------------------------------------------------------------------------
# 纯函数：占位符 P/R/F1
# ---------------------------------------------------------------------------


def test_placeholder_metrics_perfect():
    metrics = placeholder_metrics(
        actual=["【待填写：投标报价】"],
        expected_present=["【待填写：投标报价】"],
        expected_absent=["【待填写：主讲教师】"],
    )
    assert metrics["tp"] == 1
    assert metrics["fp"] == 0
    assert metrics["fn"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["forbidden_hits"] == []


def test_placeholder_metrics_missing_spurious_and_forbidden():
    metrics = placeholder_metrics(
        actual=["【待填写：主讲教师】", "【待填写：多余】"],
        expected_present=["【待填写：投标报价】", "【待填写：投标书】"],
        expected_absent=["【待填写：主讲教师】"],
    )
    assert metrics["tp"] == 0
    assert metrics["fn"] == 2
    # 禁现占位与未声明占位都只计一次 FP
    assert metrics["fp"] == 2
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["forbidden_hits"] == ["【待填写：主讲教师】"]


def test_placeholder_metrics_empty_denominators_are_none():
    metrics = placeholder_metrics(actual=[], expected_present=[], expected_absent=[])
    assert metrics["tp"] == metrics["fp"] == metrics["fn"] == 0
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None


def test_placeholder_present_absent_must_be_disjoint():
    with pytest.raises(ValueError, match="互不相交"):
        placeholder_metrics(
            actual=[],
            expected_present=["【待填写：x】"],
            expected_absent=["【待填写：x】"],
        )


# ---------------------------------------------------------------------------
# 纯函数：规则聚合与引用有效率
# ---------------------------------------------------------------------------


def _rule_payload(errors: list[dict], markers: list[str] | None = None) -> dict:
    return {
        "passed": not errors,
        "citation_markers": markers if markers is not None else [],
        "errors": errors,
        "placeholders": [],
    }


def test_rule_violations_groups_by_code_and_sections():
    checks = [
        _rule_payload([{"code": "CITE_OUT_OF_RANGE", "message": ""}]),
        _rule_payload(
            [
                {"code": "CITE_DOC_MISMATCH", "message": ""},
                {"code": "QUOTE_FORBIDDEN", "message": ""},
            ]
        ),
        _rule_payload([]),
    ]
    result = rule_violations(checks)
    assert result["rule_error_sections"] == 2
    assert result["rule_total_errors"] == 3
    assert result["rule_errors_by_code"] == {
        "CITE_EMPTY": 0,
        "CITE_OUT_OF_RANGE": 1,
        "CITE_DOC_MISMATCH": 1,
        "QUOTE_FORBIDDEN": 1,
    }


def test_citation_validity_section_level():
    checks = [
        _rule_payload([], markers=["[1]", "[2]"]),  # 有效
        _rule_payload([{"code": "CITE_OUT_OF_RANGE", "message": ""}], markers=["[3]"]),
        _rule_payload([], markers=[]),  # 无引用，不进分母
    ]
    result = citation_validity(checks)
    assert result["sections_with_markers"] == 2
    assert result["valid_sections"] == 1
    assert result["invalid_sections"] == 1
    assert result["citation_valid_rate"] == 0.5


def test_citation_validity_quote_error_does_not_count_as_citation_invalid():
    checks = [
        _rule_payload([{"code": "QUOTE_FORBIDDEN", "message": ""}], markers=["[1]"]),
    ]
    result = citation_validity(checks)
    assert result["sections_with_markers"] == 1
    assert result["valid_sections"] == 1
    assert result["citation_valid_rate"] == 1.0


def test_citation_validity_no_markers_is_none():
    result = citation_validity([_rule_payload([]), _rule_payload([])])
    assert result["sections_with_markers"] == 0
    assert result["citation_valid_rate"] is None


# ---------------------------------------------------------------------------
# 纯函数：无依据信号与三层覆盖
# ---------------------------------------------------------------------------


def test_ungrounded_signals_body_but_not_evidence():
    coverage = [
        {"name": "甲", "plan_covered": True, "body_covered": True, "evidence_covered": True},
        {"name": "乙", "plan_covered": True, "body_covered": True, "evidence_covered": False},
        {"name": "丙", "plan_covered": True, "body_covered": False, "evidence_covered": False},
    ]
    result = ungrounded_signals(coverage)
    assert result["count"] == 1
    assert result["names"] == ["乙"]


def test_coverage_metrics_rates_and_null_total():
    summary = {
        "plan": {"total": 3, "covered": 3, "gaps": []},
        "body": {"total": 3, "covered": 2, "gaps": ["丙"]},
        "evidence": {"total": 0, "covered": 0, "gaps": []},
    }
    metrics = coverage_metrics(summary)
    assert metrics["plan"]["rate"] == 1.0
    assert metrics["body"]["rate"] == pytest.approx(2 / 3)
    assert metrics["evidence"]["rate"] is None


# ---------------------------------------------------------------------------
# 纯函数：易变键投影与冻结比较
# ---------------------------------------------------------------------------


def test_project_volatile_strips_duration_only():
    metrics = {
        "llm_usage": {"calls": 3, "total_tokens": 450, "duration_ms": 1234},
        "total_revisions": 0,
    }
    projected = project_volatile(metrics)
    assert "duration_ms" not in projected["llm_usage"]
    assert projected["llm_usage"]["calls"] == 3
    # 原对象不被修改
    assert metrics["llm_usage"]["duration_ms"] == 1234


def test_compare_metrics_reports_paths():
    actual = {
        "llm_usage": {"calls": 3, "total_tokens": 450},
        "placeholders": {"tp": 2, "fp": 0},
        "rule_errors_by_code": {"CITE_EMPTY": 0},
    }
    expected = {
        "llm_usage": {"calls": 4, "total_tokens": 450},
        "placeholders": {"tp": 2, "fp": 1},
        "rule_errors_by_code": {"CITE_EMPTY": 0},
    }
    diffs = compare_metrics(actual, expected)
    assert any("llm_usage.calls" in d for d in diffs)
    assert any("placeholders.fp" in d for d in diffs)
    assert compare_metrics(actual, actual) == []


# ---------------------------------------------------------------------------
# WritingCaller：角色 + 标题键控
# ---------------------------------------------------------------------------


def _messages(role_system: str, user_first_line: str) -> list[dict]:
    return [
        {"role": "system", "content": role_system},
        {"role": "user", "content": f"{user_first_line}\n其余内容"},
    ]


def test_writing_caller_routes_four_roles_and_counts_consumption():
    fixtures = {
        "planner": [json_response({"sections": []})],
        "writer:施工方案": [text_response("正文")],
        "reviewer:施工方案": [review(80)],
        "reviser:施工方案": [text_response("修订正文")],
    }
    caller = WritingCaller(fixtures)
    planner_msg = _messages("你是投标文件编写策划助手。", "项目名称：x")
    writer_msg = _messages("你是投标文件编写助手。", "章节标题：施工方案")
    reviewer_msg = _messages("你是投标文件评审助手。", "章节标题：施工方案")
    reviser_msg = _messages("你是投标文件修订助手。", "章节标题：施工方案")

    assert caller.generate(planner_msg, json_mode=True)["content"] == "{\"sections\": []}"
    assert caller.generate(writer_msg, json_mode=False)["content"] == "正文"
    assert "80" in caller.generate(reviewer_msg, json_mode=True)["content"]
    assert caller.generate(reviser_msg, json_mode=False)["content"] == "修订正文"
    assert caller.consumption == {
        "planner": 1,
        "writer:施工方案": 1,
        "reviewer:施工方案": 1,
        "reviser:施工方案": 1,
    }


def test_writing_caller_missing_key_and_exhaustion_raise():
    caller = WritingCaller({"planner": [json_response({"sections": []})]})
    writer_msg = _messages("你是投标文件编写助手。", "章节标题：未知章节")
    with pytest.raises(KeyError, match="writer:未知章节"):
        caller.generate(writer_msg, json_mode=False)

    planner_msg = _messages("你是投标文件编写策划助手。", "项目名称：x")
    caller.generate(planner_msg, json_mode=True)
    with pytest.raises(KeyError, match="planner"):
        caller.generate(planner_msg, json_mode=True)


def test_writing_caller_unknown_role_and_title_parse_failure_raise():
    caller = WritingCaller({"planner": [json_response({"sections": []})]})
    with pytest.raises(RuntimeError, match="角色"):
        caller.generate(_messages("你是某个未知助手。", "章节标题：x"), json_mode=False)
    with pytest.raises(KeyError, match="章节标题"):
        caller.generate(_messages("你是投标文件编写助手。", "没有首行标题"), json_mode=False)


def test_writing_caller_json_mode_guard():
    caller = WritingCaller({"planner": [json_response({"sections": []})]})
    planner_msg = _messages("你是投标文件编写策划助手。", "项目名称：x")
    with pytest.raises(TypeError, match="json_mode"):
        caller.generate(planner_msg, json_mode=False)


# ---------------------------------------------------------------------------
# ScriptedRetriever
# ---------------------------------------------------------------------------


def test_scripted_retriever_returns_copy_and_missing_key_raises():
    retriever = ScriptedRetriever(clean_retrieval())
    first = retriever("施工方案", top_k=6)
    first.append({"id": "mutated"})
    second = retriever("施工方案", top_k=6)
    assert all(chunk["id"] != "mutated" for chunk in second)
    with pytest.raises(KeyError, match="缺失章节"):
        retriever("不存在章节", top_k=6)


# ---------------------------------------------------------------------------
# 端到端：A/B 两臂
# ---------------------------------------------------------------------------


def test_execute_arm_a_single_pass_metrics():
    metrics = execute_arm(make_sample(), ARM_A)
    assert metrics["llm_usage"]["calls"] == 3  # planner + writer + reviewer
    assert metrics["llm_usage"]["total_tokens"] == 300 + 150 + 120
    assert metrics["total_revisions"] == 0
    assert metrics["retrieval_degraded"] is False
    # 两层章节：技术因素三层全覆盖；报价因素仅计划层
    assert metrics["coverage"]["plan"]["covered"] == 2
    assert metrics["coverage"]["body"]["covered"] == 1
    assert metrics["coverage"]["evidence"]["covered"] == 1
    assert metrics["citation"]["citation_valid_rate"] == 1.0
    assert metrics["rules"]["rule_total_errors"] == 0
    assert metrics["ungrounded_signal_count"] == 0
    assert metrics["placeholders"]["tp"] == 2
    assert metrics["placeholders"]["fp"] == 0
    # placeholder 章节不调模型、固定 100 分，平均分只算 LLM 评审章节
    assert metrics["avg_score_after"] == 80.0
    compact = {s["title"]: s for s in metrics["sections"]}
    assert compact["投标报价"]["strategy"] == "placeholder"
    assert compact["投标报价"]["retrieved_count"] == 0


def test_execute_arm_b_triggers_one_revision_and_delta():
    fixtures = clean_fixtures("依据原文[1]展开施工方案。", reviewer_score=70)
    # B 臂初评 70 分触发修订，复评 85 分终止循环；A 臂只消费第一条
    fixtures["reviewer:施工方案"] = [review(70), review(85)]
    sample = make_sample(fixtures)
    arm_a = execute_arm(sample, ARM_A)
    arm_b = execute_arm(sample, ARM_B)
    assert arm_a["total_revisions"] == 0
    assert arm_a["llm_usage"]["calls"] == 3
    assert arm_b["total_revisions"] == 1
    # planner + writer + reviewer(70) + reviser + reviewer(85)
    assert arm_b["llm_usage"]["calls"] == 5
    assert arm_b["avg_score_after"] == 85.0
    delta = run_sample(sample)["ab_delta"]
    for path in DELTA_PATHS:
        assert path in delta
    assert delta[("llm_usage", "calls")] == 2
    assert delta[("llm_usage", "total_tokens")] == (
        arm_b["llm_usage"]["total_tokens"] - arm_a["llm_usage"]["total_tokens"]
    )
    assert delta[("total_revisions",)] == 1


def test_run_sample_freeze_match_and_mismatch():
    # 先按实际指标冻结期望（模拟人工冻结流程），再验证匹配
    frozen_a = project_volatile(execute_arm(make_sample(), ARM_A))
    frozen_b = project_volatile(execute_arm(make_sample(), ARM_B))
    expected = {
        "expected_placeholders": {
            "present": ["【待填写：投标报价】", "【待填写：投标书】"],
            "absent": ["【待填写：主讲教师】"],
        },
        "arms": {ARM_A: frozen_a, ARM_B: frozen_b},
    }
    result = run_sample(make_sample(expected=expected))
    assert result["arms"][ARM_A]["passed"] is True
    assert result["arms"][ARM_B]["passed"] is True
    assert result["arms"][ARM_A]["diffs"] == []

    tampered = json.loads(json.dumps(expected, ensure_ascii=False))
    tampered["arms"][ARM_A]["llm_usage"]["calls"] = 99
    broken_result = run_sample(make_sample(expected=tampered))
    assert broken_result["arms"][ARM_A]["passed"] is False
    assert any("llm_usage.calls" in d for d in broken_result["arms"][ARM_A]["diffs"])


def test_report_envelope_is_deterministic():
    result = run_sample(make_sample())
    envelope_a = json.dumps(build_writing_report([result]), ensure_ascii=False, sort_keys=True)
    envelope_b = json.dumps(build_writing_report([result]), ensure_ascii=False, sort_keys=True)
    assert envelope_a == envelope_b
    payload = json.loads(envelope_a)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "writing_eval"
    assert "duration_ms" not in envelope_a


# ---------------------------------------------------------------------------
# 边界：无检索 / 越界 / 错 doc / 报价禁令 / 禁现占位 / 降级
# ---------------------------------------------------------------------------


def test_boundary_no_retrieval_cite_empty():
    fixtures = clean_fixtures("无检索却输出引用[1]的施工方案正文。")
    # 键存在但片段列表为空 = 检索无命中；缺键才是数据集构造错误
    sample = make_sample(fixtures, retrieval={"施工方案": []})
    metrics = execute_arm(sample, ARM_A)
    assert metrics["rules"]["rule_errors_by_code"]["CITE_EMPTY"] == 1
    assert metrics["citation"]["valid_sections"] == 0
    assert metrics["citation"]["citation_valid_rate"] == 0.0
    # 正文响应了因素但无证据 → 无依据信号
    assert metrics["ungrounded_signal_count"] == 1


def test_boundary_out_of_range_and_doc_mismatch():
    doc_id = "t1doc000000001"
    retrieval = {
        "施工方案": [
            {"id": f"{doc_id}:0000", "text": "片段一", "metadata": {"doc_id": doc_id}},
            {"id": "otherdoc:0001", "text": "外文档片段", "metadata": {"doc_id": "otherdoc"}},
        ]
    }
    fixtures = clean_fixtures("引用越界[3]且混入外文档的施工方案。")
    metrics = execute_arm(make_sample(fixtures, retrieval), ARM_A)
    by_code = metrics["rules"]["rule_errors_by_code"]
    assert by_code["CITE_OUT_OF_RANGE"] == 1
    assert by_code["CITE_DOC_MISMATCH"] == 1
    assert metrics["citation"]["valid_sections"] == 0


def test_boundary_quote_forbidden():
    fixtures = clean_fixtures("施工方案中承诺让利 5%，依据[1]。")
    metrics = execute_arm(make_sample(fixtures), ARM_A)
    assert metrics["rules"]["rule_errors_by_code"]["QUOTE_FORBIDDEN"] == 1
    # 报价禁令不算引用失效
    assert metrics["citation"]["valid_sections"] == 1


def test_boundary_forbidden_placeholder_hits_precision():
    fixtures = clean_fixtures("施工方案正文[1]，缺资料处写【待填写：主讲教师】。")
    sample = make_sample(fixtures)
    metrics = execute_arm(sample, ARM_A)
    placeholders = metrics["placeholders"]
    assert placeholders["forbidden_hits"] == ["【待填写：主讲教师】"]
    assert placeholders["fp"] == 1
    assert placeholders["precision"] < 1.0
    assert placeholders["recall"] == 1.0


def test_retrieval_degraded_flag_passes_through():
    sample = make_sample(config={"retrieval_degraded": True})
    metrics = execute_arm(sample, ARM_A)
    assert metrics["retrieval_degraded"] is True


# ---------------------------------------------------------------------------
# planner 静默兜底守卫
# ---------------------------------------------------------------------------


def test_planner_guard_catches_swallowed_failure():
    class ExplodingPlanner(WritingCaller):
        def generate(self, messages, json_mode):
            role = self.detect_role(messages)
            if role == "planner":
                raise KeyError("planner 夹具爆炸")
            return super().generate(messages, json_mode)

    sample = make_sample(fixtures=clean_fixtures("正文[1]"))

    with pytest.raises(RuntimeError, match="planner"):
        execute_arm(sample, ARM_A, caller_factory=lambda f: ExplodingPlanner(f))


# ---------------------------------------------------------------------------
# 目录加载与真实 Golden 锁定
# ---------------------------------------------------------------------------


def test_load_writing_sample_reads_dataset(tmp_path):
    sample_dir = tmp_path / "01_engineering" / "writing"
    sample_dir.mkdir(parents=True)
    parent = sample_dir.parent
    (parent / "expected_extraction.json").write_text(
        json.dumps(
            {
                "doc_id": "t1doc000000001",
                "doc_type": "tender_file",
                "project_name": "测试采购项目",
                "evaluation_factors": [
                    {"name": "施工方案", "category": "技术", "score": 40},
                ],
                "bid_format_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (sample_dir / "fixtures.json").write_text(
        json.dumps(clean_fixtures("正文[1]"), ensure_ascii=False), encoding="utf-8"
    )
    (sample_dir / "retrieval.json").write_text(
        json.dumps(clean_retrieval(), ensure_ascii=False), encoding="utf-8"
    )
    (sample_dir / "profile.json").write_text(
        json.dumps({"company_name": "测试公司"}, ensure_ascii=False), encoding="utf-8"
    )
    (sample_dir / "config.json").write_text(
        json.dumps({"retrieval_degraded": False}, ensure_ascii=False), encoding="utf-8"
    )
    (sample_dir / "expected_writing_metrics.json").write_text(
        json.dumps(
            {"expected_placeholders": {"present": [], "absent": []}, "arms": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sample = load_writing_sample(sample_dir)
    assert sample.tender.doc_id == "t1doc000000001"
    assert sample.profile.company_name == "测试公司"
    assert sample.config["retrieval_degraded"] is False


GOLDEN_ROOT = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "golden"


def test_golden_writing_baseline_locked():
    results = run_all(GOLDEN_ROOT)
    assert len(results) == 3
    by_name = {result["name"]: result for result in results}
    for result in results:
        for arm in (ARM_A, ARM_B):
            assert result["arms"][arm]["passed"] is True, (
                result["name"],
                arm,
                result["arms"][arm]["diffs"],
            )

    # 01 工程：干净基线，B 臂一轮修订，零规则错误
    eng_a = by_name["01_engineering"]["arms"][ARM_A]["metrics"]
    eng_b = by_name["01_engineering"]["arms"][ARM_B]["metrics"]
    assert eng_a["llm_usage"]["calls"] == 7
    assert eng_a["llm_usage"]["total_tokens"] == 1710
    assert eng_b["llm_usage"]["calls"] == 9
    assert eng_b["llm_usage"]["total_tokens"] == 2230
    assert eng_b["total_revisions"] == 1
    assert eng_b["sections"][0]["score_before"] == 70
    assert eng_b["sections"][0]["score_after"] == 88
    for metrics in (eng_a, eng_b):
        assert metrics["rules"]["rule_total_errors"] == 0
        assert metrics["citation"]["citation_valid_rate"] == 1.0
        assert metrics["ungrounded_signal_count"] == 0
        assert metrics["placeholders"]["tp"] == 5
        assert metrics["placeholders"]["fp"] == 0
        assert metrics["coverage"]["plan"]["covered"] == 5
        assert metrics["coverage"]["body"]["covered"] == 4
        assert metrics["coverage"]["evidence"]["covered"] == 4

    # 02 货物：越界 + 错 doc + 报价禁令，两臂指标完全一致
    goods_a = by_name["02_goods"]["arms"][ARM_A]["metrics"]
    goods_b = by_name["02_goods"]["arms"][ARM_B]["metrics"]
    assert goods_a == goods_b
    by_code = goods_a["rules"]["rule_errors_by_code"]
    assert by_code == {
        "CITE_EMPTY": 0,
        "CITE_OUT_OF_RANGE": 1,
        "CITE_DOC_MISMATCH": 1,
        "QUOTE_FORBIDDEN": 1,
    }
    assert goods_a["rules"]["rule_error_sections"] == 2
    assert goods_a["citation"] == {
        "sections_with_markers": 3,
        "valid_sections": 2,
        "invalid_sections": 1,
        "citation_valid_rate": pytest.approx(2 / 3),
    }
    assert goods_a["ungrounded_signals"]["names"] == ["技术参数响应情况"]
    assert goods_a["placeholders"]["tp"] == 3

    # 03 服务：检索降级 + CITE_EMPTY + 禁现占位符
    serv_a = by_name["03_service"]["arms"][ARM_A]["metrics"]
    assert serv_a["retrieval_degraded"] is True
    assert serv_a["rules"]["rule_errors_by_code"]["CITE_EMPTY"] == 1
    assert serv_a["ungrounded_signals"]["names"] == ["培训方案"]
    placeholders = serv_a["placeholders"]
    assert placeholders["tp"] == 3
    assert placeholders["fp"] == 1
    assert placeholders["forbidden_hits"] == ["【待填写：主讲教师】"]
    assert placeholders["precision"] == 0.75
    assert placeholders["recall"] == 1.0

    # A/B 差值锁定：仅 01 因修订产生增量
    assert by_name["01_engineering"]["ab_delta"][("llm_usage", "calls")] == 2
    assert by_name["01_engineering"]["ab_delta"][("llm_usage", "total_tokens")] == 520
    assert by_name["01_engineering"]["ab_delta"][("total_revisions",)] == 1
    for name in ("02_goods", "03_service"):
        assert all(value == 0 for value in by_name[name]["ab_delta"].values())

    # 跨样本聚合锁定
    envelope = build_writing_report(results)
    aggregate = envelope["aggregate"]
    assert aggregate[ARM_A]["calls"] == 21
    assert aggregate[ARM_A]["total_tokens"] == 5130
    assert aggregate[ARM_B]["calls"] == 23
    assert aggregate[ARM_B]["total_tokens"] == 5650
    assert aggregate[ARM_B]["total_revisions"] == 1
    assert aggregate[ARM_A]["rule_total_errors"] == 4
    assert aggregate[ARM_A]["ungrounded_signal_count"] == 2
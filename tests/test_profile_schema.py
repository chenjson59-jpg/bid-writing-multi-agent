"""投标人资料 schema 推断（profile_schema）测试。

覆盖（T-040 白名单收敛后，schema 推断只产出 7 个白名单键）：
- 核心 5 字段（公司名/法人/注册资本/成立日期/地址）始终 required
- 工程类 / 服务类 / 抽取结果全空 / ISO 认证等典型场景
- 移出字段（similar_projects/personnel/financials/contact）不再出现在推断结果中
- 非法输入防御（evaluation_factors 混入非 dict、source_quote 非 str 等）
- API 端点 /api/documents/{doc_id}/profile-schema 的 200 / 404 / 422
"""
from __future__ import annotations

import json
from pathlib import Path

from src.bid_writer.profile_schema import infer_profile_schema
from src.bid_writer.schema import PROFILE_FIELD_LABELS

# T-040：表单 / prompt 外发 / 缺失判定 / schema 推断统一收敛为 7 字段白名单
EXPECTED_KEYS = {
    "company_name",
    "legal_representative",
    "registered_capital",
    "established_date",
    "address",
    "qualifications",
    "certifications",
}
# 已移出白名单的高敏 / 单标书字段：推断结果中不允许再出现这些键
REMOVED_KEYS = {"similar_projects", "personnel", "financials", "contact"}


def _levels(result: dict) -> dict[str, str]:
    """只取字段名 → level，方便断言。"""
    return {key: value["level"] for key, value in result.items()}


# ---------- 纯函数测试 ----------


def test_core_fields_always_required_even_when_extraction_empty():
    """任何情况下公司名等 5 项都是 required。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    assert result["company_name"]["level"] == "required"
    assert result["legal_representative"]["level"] == "required"
    assert result["registered_capital"]["level"] == "required"
    assert result["established_date"]["level"] == "required"
    assert result["address"]["level"] == "required"


def test_result_has_exactly_seven_whitelist_keys():
    """返回字典键集合恰为 7 个白名单键，与 PROFILE_FIELD_LABELS 一致，不多不少。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    assert set(result.keys()) == EXPECTED_KEYS
    assert set(PROFILE_FIELD_LABELS.keys()) == EXPECTED_KEYS
    assert REMOVED_KEYS.isdisjoint(result.keys())


def test_engineering_tender_keeps_only_whitelist_fields():
    """工程类标书即便大量提到业绩/人员/财务，移出字段也不再推断，只剩 7 键。"""
    extraction = {
        "evaluation_factors": [
            {"name": "项目管理机构", "source_quote": "项目经理、技术负责人及主要管理人员的资格与业绩"},
            {"name": "类似项目业绩", "source_quote": "近三年同类供水工程业绩数量与规模"},
            {"name": "财务状况", "source_quote": "近三年营业收入、资产负债率与净利润情况"},
            {"name": "投标报价", "source_quote": "以评标基准价为基准"},
        ],
        "qualification_items": [
            {"requirement": "具备市政公用工程施工总承包一级及以上资质"},
            {"requirement": "近三年至少完成 1 类工程业绩"},
            {"requirement": "拟派项目经理须具备一级建造师执业资格"},
            {"requirement": "提供近三年财务报表审计报告"},
        ],
        "bid_format_items": [
            {"title": "项目管理机构", "content": "列明项目经理、技术负责人及主要管理人员资格、业绩与社保证明"},
            {"title": "类似项目业绩", "content": "提供合同关键页与竣工验收证明"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    levels = _levels(result)

    # 键集合被白名单锁死
    assert set(result.keys()) == EXPECTED_KEYS
    assert REMOVED_KEYS.isdisjoint(result.keys())
    # qualifications 命中“资质/一级”硬关键词 → required
    assert levels["qualifications"] == "required"
    # certifications 工程标书没提 → optional
    assert levels["certifications"] == "optional"


def test_service_tender_without_hard_project_keywords():
    """服务类标书：白名单内两个数组字段都没命中关键词 → optional，且不含移出键。"""
    extraction = {
        "evaluation_factors": [
            {"name": "过往服务业绩", "source_quote": "近五年类似咨询服务合同数量与金额"},
        ],
        "qualification_items": [
            {"requirement": "具备咨询服务类营业执照，注册资本 500 万元以上"},
        ],
        "bid_format_items": [
            {"title": "商务部分", "content": "包含服务方案、报价、承诺书等"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    levels = _levels(result)

    assert set(result.keys()) == EXPECTED_KEYS
    assert REMOVED_KEYS.isdisjoint(result.keys())
    assert levels["qualifications"] == "optional"
    assert levels["certifications"] == "optional"


def test_certifications_required_when_iso_mentioned():
    """ISO 9001 / CMMI 等硬关键词 → certifications required。"""
    extraction = {
        "qualification_items": [
            {"requirement": "投标人须具备 ISO 9001 质量管理体系认证"},
            {"requirement": "具备 CMMI 3 级及以上认证"},
        ],
        "evaluation_factors": [],
        "bid_format_items": [],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result["certifications"]["level"] == "required"
    assert set(result.keys()) == EXPECTED_KEYS


def test_all_non_core_optional_when_extraction_empty():
    """抽取结果完全为空 → 只有 core 5 项 required，白名单内其余 2 项 optional。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    levels = _levels(result)
    assert levels["qualifications"] == "optional"
    assert levels["certifications"] == "optional"
    assert set(result.keys()) == EXPECTED_KEYS


def test_qualifications_required_when_only_qualification_keyword():
    """只在 qualification_items 里提到“资质" → 硬匹配直接进 required。"""
    extraction = {
        "qualification_items": [
            {"requirement": "投标人须具备相应资质"},
        ],
        "evaluation_factors": [],
        "bid_format_items": [],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result["qualifications"]["level"] == "required"


def test_bank_account_keywords_create_no_fields_after_whitelist():
    """T-040：银行/保证金类关键词过去会推断 contact，contact 移出后不得产生任何白名单外字段。"""
    extraction = {
        "bid_format_items": [
            {"title": "投标保证金", "content": "人民币 60 万元，形式为银行保函或转账，付款账户见投标须知"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert set(result.keys()) == EXPECTED_KEYS
    assert "contact" not in result
    # 白名单内字段没有对应关键词 → 两个数组字段保持 optional
    assert _levels(result)["qualifications"] == "optional"
    assert _levels(result)["certifications"] == "optional"


# ---------- 防御性测试：坏数据不崩 ----------


def test_defense_against_bad_types_in_extraction():
    """evaluation_factors 里混入非 dict、source_quote 不是字符串 → 不崩溃，照样推断。"""
    extraction = {
        "evaluation_factors": [
            "这是一条坏数据",
            None,
            {"name": "项目管理机构", "source_quote": 12345},  # source_quote 是 int
        ],
        "qualification_items": [
            {"requirement": "拟派项目经理须具备一级建造师执业资格"},
        ],
        "bid_format_items": "not a list",  # 错类型
        "basic": None,
    }
    # 不应抛任何异常
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert set(result.keys()) == EXPECTED_KEYS
    # “一级建造师”命中 qualifications 的“一级”硬关键词 → required
    assert result["qualifications"]["level"] == "required"
    # 人员字段已移出，结果中不得再出现
    assert "personnel" not in result


def test_defense_against_missing_top_level_keys():
    """extraction 里只给 evaluation_factors，其他块全缺；业绩关键词不再产生移出键。"""
    extraction = {
        "evaluation_factors": [
            {"name": "类似项目业绩", "source_quote": "近三年同类项目业绩"},
        ]
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert set(result.keys()) == EXPECTED_KEYS
    assert "similar_projects" not in result
    assert "financials" not in result
    # 白名单数组字段没有对应关键词 → optional
    assert result["qualifications"]["level"] == "optional"
    assert result["certifications"]["level"] == "optional"


def test_matched_flag_true_only_when_keywords_hit():
    """matched 字段语义：硬关键词命中 → True，core 必填 → False，optional → False。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    # core 5 项是“通用必填”，不是从抽取结果匹配来的 → matched=False
    assert result["company_name"]["matched"] is False
    assert result["legal_representative"]["matched"] is False
    # optional 也没匹配 → matched=False
    assert result["certifications"]["matched"] is False
    assert "similar_projects" not in result

    # 有硬关键词命中时 matched=True
    extraction = {
        "qualification_items": [
            {"requirement": "投标人须具备 ISO 9001 质量管理体系认证"},
        ],
    }
    result2 = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result2["certifications"]["matched"] is True


def test_reason_field_contains_context_snippet_when_hit():
    """命中时 reason 应包含抽取结果里的原文片段，供前端展示。"""
    extraction = {
        "evaluation_factors": [
            {"name": "企业资质要求", "source_quote": "具备市政公用工程施工总承包一级及以上资质"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    reason = result["qualifications"]["reason"]
    # 应该包含原文片段
    assert "资质" in reason
    assert "总承包" in reason


# ---------- API 端点测试（走 TestClient） ----------


def _write_extraction(tmp_path: Path, doc_id: str, extraction: dict) -> Path:
    """在测试的 parsed 目录下放一份 extraction.json。"""
    doc_dir = tmp_path / "upload" / "parsed" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "extraction.json"
    path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_profile_schema_endpoint_200(client, local_backend):
    """正常调用 → 200，返回结构符合预期且 fields 恰为 7 个白名单键。"""
    doc_id = "5e4083e5c89826f8"
    _write_extraction(
        local_backend,
        doc_id,
        {
            "evaluation_factors": [
                {"name": "项目管理机构", "source_quote": "项目经理、技术负责人"},
                {"name": "类似项目业绩", "source_quote": "近三年同类项目业绩"},
            ],
            "qualifications": [
                {"requirement": "具备市政公用工程施工总承包一级资质"},
            ],
            "bid_format_items": [
                {"title": "类似项目业绩", "content": "提供合同关键页与竣工验收证明"},
            ],
            "doc_type": "tender_file",
        },
    )

    resp = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp.status_code == 200
    data = resp.json()

    assert data["doc_id"] == doc_id
    assert data["doc_type"] == "tender_file"
    assert set(data["fields"].keys()) == EXPECTED_KEYS
    assert REMOVED_KEYS.isdisjoint(data["fields"].keys())

    # 每个字段都有 level / label / reason / matched
    for key, item in data["fields"].items():
        assert item["level"] in ("required", "recommended", "optional")
        assert isinstance(item["label"], str) and item["label"], key
        assert isinstance(item["reason"], str)
        assert isinstance(item["matched"], bool)

    # 推断结果应合理：资质关键词命中 qualifications，认证未提及 → optional
    fields = data["fields"]
    assert fields["qualifications"]["level"] == "required"
    assert fields["certifications"]["level"] == "optional"
    # inferred_from 应该包含有数据的块
    assert "evaluation_factors" in data["inferred_from"]


def test_profile_schema_endpoint_404_when_no_extraction(client, local_backend):
    """文档存在但没有 extraction.json → 404。"""
    doc_id = "deadbeef12345678"
    # 不写 extraction.json
    resp = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp.status_code == 404
    assert "没有结构化抽取结果" in resp.text


def test_profile_schema_endpoint_422_when_doc_id_malformed(client, local_backend):
    """doc_id 不是 16 位 hex → 422。"""
    resp = client.get("/api/documents/not-a-hex/profile-schema")
    assert resp.status_code == 422


def test_profile_schema_endpoint_422_when_extraction_json_corrupted(client, local_backend):
    """extraction.json 损坏（不是合法 JSON 或不是 dict）→ 422。"""
    doc_id = "cafebabedeadbeef"
    doc_dir = local_backend / "upload" / "parsed" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # 非法 JSON
    (doc_dir / "extraction.json").write_text("{not valid", encoding="utf-8")
    resp = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp.status_code == 422

    # 不是 dict
    (doc_dir / "extraction.json").write_text('["list", "not", "dict"]', encoding="utf-8")
    resp2 = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp2.status_code == 422


def test_profile_schema_endpoint_with_empty_extraction(client, local_backend):
    """抽取结果全空 → 只有 core 5 项 required，白名单内其余 2 项 optional。"""
    doc_id = "1234567890abcdef"
    _write_extraction(local_backend, doc_id, {"doc_type": "tender_file"})

    resp = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp.status_code == 200
    data = resp.json()

    core = {"company_name", "legal_representative", "registered_capital", "established_date", "address"}
    for key in core:
        assert data["fields"][key]["level"] == "required"

    non_core = {"qualifications", "certifications"}
    for key in non_core:
        assert data["fields"][key]["level"] == "optional"

    # 移出字段不应出现在响应中
    assert REMOVED_KEYS.isdisjoint(data["fields"].keys())
    # inferred_from 应该是空列表（三个块都没数据）
    assert data["inferred_from"] == []

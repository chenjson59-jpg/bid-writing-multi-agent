"""投标人资料 schema 推断（profile_schema）测试。

覆盖：
- 核心 5 字段（公司名/法人/注册资本/成立日期/地址）始终 required
- 工程类 / 服务类 / 抽取结果全空 / 只有模糊关键词 / ISO 认证等典型场景
- 非法输入防御（evaluation_factors 混入非 dict、source_quote 非 str 等）
- API 端点 /api/documents/{doc_id}/profile-schema 的 200 / 404 / 422
"""
from __future__ import annotations

import json
from pathlib import Path

from src.bid_writer.profile_schema import infer_profile_schema
from src.bid_writer.schema import PROFILE_FIELD_LABELS


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


def test_all_fields_present_for_every_profile_key():
    """返回字典必须覆盖 PROFILE_FIELD_LABELS 里的全部 key，不多不少。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    assert set(result.keys()) == set(PROFILE_FIELD_LABELS.keys())


def test_engineering_tender_detects_required_fields():
    """典型工程类标书：评分因素明确提到类似项目业绩、项目管理机构、财务状况。"""
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

    assert levels["similar_projects"] == "required"
    assert levels["personnel"] == "required"
    assert levels["financials"] == "required"
    # qualifications 应该也是 required（有"资质要求"关键词）
    assert levels["qualifications"] == "required"
    # certifications 工程标书没提 → optional
    assert levels["certifications"] == "optional"


def test_service_tender_without_hard_project_keywords():
    """服务类标书：没有"类似项目业绩"、"项目经理"等硬关键词，只提"过往业绩"。"""
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

    # 没有"类似项目业绩"硬关键词，只有模糊"过往服务业绩"→ 变成 recommended
    assert levels["similar_projects"] in ("recommended", "optional")
    # 没有项目经理/项目管理机构 → personnel optional
    assert levels["personnel"] == "optional"
    # 财务也没提 → optional
    assert levels["financials"] == "optional"


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


def test_all_non_core_optional_when_extraction_empty():
    """抽取结果完全为空 → 只有 core 5 项 required，其余全 optional。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    levels = _levels(result)
    assert levels["similar_projects"] == "optional"
    assert levels["personnel"] == "optional"
    assert levels["financials"] == "optional"
    assert levels["contact"] == "optional"
    assert levels["certifications"] == "optional"


def test_qualifications_required_when_only_qualification_keyword():
    """只在 qualification_items 里提到"资质" → 硬匹配不到时走 recommended 兜底。"""
    extraction = {
        "qualification_items": [
            {"requirement": "投标人须具备相应资质"},
        ],
        "evaluation_factors": [],
        "bid_format_items": [],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    # "资质" 是 hard required 关键词 → 直接进 required
    assert result["qualifications"]["level"] == "required"


def test_contact_required_when_bank_account_mentioned():
    """抽取结果里明确提到"开户银行"、"银行账号"、"投标保证金" → contact required。"""
    extraction = {
        "bid_format_items": [
            {"title": "投标保证金", "content": "人民币 60 万元，形式为银行保函或转账，付款账户见投标须知"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result["contact"]["level"] == "required"


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
    # 项目经理那项还是能匹配上
    assert result["personnel"]["level"] == "required"
    # bid_format_items 因为是错类型 → 被当成空列表处理，不影响
    assert _levels(result)["similar_projects"] in ("optional", "recommended")


def test_defense_against_missing_top_level_keys():
    """extraction 里只给 evaluation_factors，其他块全缺。"""
    extraction = {
        "evaluation_factors": [
            {"name": "类似项目业绩", "source_quote": "近三年同类项目业绩"},
        ]
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result["similar_projects"]["level"] == "required"
    # 缺的块里自然没有关键词 → optional
    assert result["financials"]["level"] == "optional"


def test_matched_flag_true_only_when_keywords_hit():
    """matched 字段语义：硬关键词命中 → True，core 必填 → False，optional → False。"""
    result = infer_profile_schema({}, PROFILE_FIELD_LABELS)
    # core 5 项是"通用必填"，不是从抽取结果匹配来的 → matched=False
    assert result["company_name"]["matched"] is False
    assert result["legal_representative"]["matched"] is False
    # optional 也没匹配 → matched=False
    assert result["certifications"]["matched"] is False
    assert result["similar_projects"]["matched"] is False

    # 有硬关键词命中时 matched=True
    extraction = {
        "evaluation_factors": [
            {"name": "类似项目业绩", "source_quote": "近三年同类项目业绩数量与规模"},
        ],
    }
    result2 = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    assert result2["similar_projects"]["matched"] is True


def test_reason_field_contains_context_snippet_when_hit():
    """命中时 reason 应包含抽取结果里的原文片段，供前端展示。"""
    extraction = {
        "evaluation_factors": [
            {"name": "类似项目业绩", "source_quote": "近三年同类供水工程业绩数量与规模"},
        ],
    }
    result = infer_profile_schema(extraction, PROFILE_FIELD_LABELS)
    reason = result["similar_projects"]["reason"]
    # 应该包含原文
    assert "类似项目业绩" in reason
    assert "同类供水工程业绩" in reason


# ---------- API 端点测试（走 TestClient） ----------


def _write_extraction(tmp_path: Path, doc_id: str, extraction: dict) -> Path:
    """在测试的 parsed 目录下放一份 extraction.json。"""
    doc_dir = tmp_path / "upload" / "parsed" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "extraction.json"
    path.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_profile_schema_endpoint_200(client, local_backend):
    """正常调用 → 200，返回结构符合预期。"""
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
    assert set(data["fields"].keys()) == set(PROFILE_FIELD_LABELS.keys())

    # 每个字段都有 level / label / reason / matched
    for key, item in data["fields"].items():
        assert item["level"] in ("required", "recommended", "optional")
        assert isinstance(item["label"], str) and item["label"], key
        assert isinstance(item["reason"], str)
        assert isinstance(item["matched"], bool)

    # 推断结果应合理
    fields = data["fields"]
    assert fields["similar_projects"]["level"] == "required"
    assert fields["personnel"]["level"] == "required"
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
    """抽取结果全空 → 只有 core 5 项 required，其余 optional。"""
    doc_id = "1234567890abcdef"
    _write_extraction(local_backend, doc_id, {"doc_type": "tender_file"})

    resp = client.get(f"/api/documents/{doc_id}/profile-schema")
    assert resp.status_code == 200
    data = resp.json()

    core = {"company_name", "legal_representative", "registered_capital", "established_date", "address"}
    for key in core:
        assert data["fields"][key]["level"] == "required"

    non_core = {"similar_projects", "personnel", "financials", "contact", "certifications"}
    for key in non_core:
        assert data["fields"][key]["level"] == "optional"

    # inferred_from 应该是空列表（三个块都没数据）
    assert data["inferred_from"] == []

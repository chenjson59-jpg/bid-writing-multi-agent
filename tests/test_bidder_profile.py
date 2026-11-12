"""T-040 投标人资料白名单收敛与旧 JSON 兼容锁定测试。

锁定两条行为：
1. 白名单常量（PROFILE_FIELD_LABELS / MISSING_FIELD_ORDER）恰为 7 个保留字段；
2. BidderProfile dataclass 保留全部历史字段，含 11 字段 + notes 的旧版 JSON 仍可
   反序列化，旧值在实例上可读、to_dict 仍输出全部字段（旧资料库数据不丢、不报错）。
"""
from __future__ import annotations

from src.bid_writer.schema import (
    MISSING_FIELD_ORDER,
    PROFILE_FIELD_LABELS,
    BidderProfile,
)

EXPECTED_KEYS = {
    "company_name",
    "legal_representative",
    "registered_capital",
    "established_date",
    "address",
    "qualifications",
    "certifications",
}
REMOVED_KEYS = {"similar_projects", "personnel", "financials", "contact"}

# 含全部 11 个资料字段 + notes 的旧版 JSON（T-040 前的表单产物形态）
LEGACY_PROFILE_DICT = {
    "company_name": "旧版示例工程有限公司",
    "legal_representative": "张三",
    "registered_capital": "5000 万元",
    "established_date": "2005-06-01",
    "address": "示例省示例市示例区示例路 1 号",
    "qualifications": ["水利水电工程施工总承包二级"],
    "certifications": ["安全生产许可证（编号：示例）"],
    "similar_projects": [
        {"name": "旧版示例供水工程", "amount": "3200 万元", "date": "2024-10-01"},
    ],
    "personnel": [{"name": "李四", "role": "项目经理"}],
    "financials": {"2024": "营业收入 1.5 亿元，净利润 950 万元"},
    "contact": {
        "person": "王五",
        "phone": "13800000000",
        "email": "legacy@example.com",
    },
    "notes": ["旧资料里的自由文本备注"],
}


def test_whitelist_constants_shrink_to_seven_keys():
    assert set(PROFILE_FIELD_LABELS.keys()) == EXPECTED_KEYS
    assert set(MISSING_FIELD_ORDER) == EXPECTED_KEYS
    assert REMOVED_KEYS.isdisjoint(PROFILE_FIELD_LABELS)
    assert REMOVED_KEYS.isdisjoint(MISSING_FIELD_ORDER)
    # 两个常量键顺序一致（缺失中文名报告依赖固定顺序）
    assert tuple(MISSING_FIELD_ORDER) == tuple(PROFILE_FIELD_LABELS.keys())


def test_dataclass_keeps_all_legacy_fields():
    """dataclass 字段一个不删：旧 JSON 反序列化与旧资料库加载依赖这些字段。"""
    fields = set(BidderProfile().to_dict().keys())
    assert EXPECTED_KEYS <= fields
    assert REMOVED_KEYS <= fields
    assert "notes" in fields


def test_from_dict_accepts_full_legacy_json_without_error():
    profile = BidderProfile.from_dict(LEGACY_PROFILE_DICT)
    # 白名单字段正常读取
    assert profile.company_name == "旧版示例工程有限公司"
    assert profile.qualifications == ["水利水电工程施工总承包二级"]
    # 移出字段旧值仍保留在实例上可读（只是不再外发 / 不参与缺失判定）
    assert profile.similar_projects == [
        {"name": "旧版示例供水工程", "amount": "3200 万元", "date": "2024-10-01"},
    ]
    assert profile.personnel == [{"name": "李四", "role": "项目经理"}]
    assert profile.financials == {"2024": "营业收入 1.5 亿元，净利润 950 万元"}
    assert profile.contact["phone"] == "13800000000"
    assert profile.notes == ["旧资料里的自由文本备注"]


def test_from_dict_tolerates_none_unknown_and_missing_keys():
    assert BidderProfile.from_dict(None).company_name == ""
    # 未知字段忽略，不抛异常
    profile = BidderProfile.from_dict({"company_name": "公司", "unknown_field": {"x": 1}})
    assert profile.company_name == "公司"
    # 缺键按空处理，移出字段缺省值保持既有形态
    empty = BidderProfile.from_dict({})
    assert empty.similar_projects == []
    assert empty.personnel == []
    assert empty.financials is None
    assert empty.contact is None
    assert empty.notes == []


def test_to_dict_roundtrip_preserves_legacy_values():
    profile = BidderProfile.from_dict(LEGACY_PROFILE_DICT)
    payload = profile.to_dict()
    # 旧值随 to_dict 完整保留，资料库编辑回显不丢数据
    assert payload["similar_projects"] == LEGACY_PROFILE_DICT["similar_projects"]
    assert payload["financials"] == LEGACY_PROFILE_DICT["financials"]
    assert payload["notes"] == LEGACY_PROFILE_DICT["notes"]
    restored = BidderProfile.from_dict(payload)
    assert restored == profile


def test_missing_fields_never_reports_removed_keys_or_notes():
    # 白名单 7 项已填齐 → 无缺失
    assert BidderProfile.from_dict(LEGACY_PROFILE_DICT).missing_fields() == []
    # 全空资料：缺失项也只能来自 7 个白名单中文名
    all_missing = BidderProfile().missing_fields()
    assert len(all_missing) == 7
    assert "联系方式" not in all_missing
    assert "类似项目业绩" not in all_missing
    assert "财务数据" not in all_missing
    assert "项目管理人员" not in all_missing

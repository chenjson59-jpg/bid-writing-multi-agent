"""根据抽取结果推断投标人资料字段的必填/建议填级别。

不依赖 LLM，仅用关键词匹配。匹配规则覆盖工程、服务、货物、活动、咨询
等常见招标类型。每个字段返回：level（必填/建议/可选）+ reason（命中的
原始文本片段，供前端给业务人员展示"为什么需要填这个"）。
"""
from __future__ import annotations

import re
from collections.abc import Iterable


# ---------- 字段定义 ----------
# 每个 (profile_key, label, keywords_required, keywords_optional)
# - keywords_required: 命中任意一个 → 该字段 level="required"
# - keywords_optional: 命中任意一个 → 该字段 level="recommended"
# - 都没命中但在 CORE_REQUIRED 里 → level="required"（任何标书都要）
# - 都没命中 → level="optional"

_CORE_REQUIRED_KEYS = frozenset(
    {
        "company_name",
        "legal_representative",
        "registered_capital",
        "established_date",
        "address",
    }
)

_FIELD_RULES: dict[str, dict[str, list[str]]] = {
    "qualifications": {
        "required": [
            "资质",          # 标书里只要有"资质"二字，资质类字段就是必填
            "资质要求",
            "投标人资格",
            "承包资质",
            "施工资质",
            "工程资质",
            "服务资质",
            "一级资质",
            "二级资质",
            "三级资质",
            "资质等级",
            "一级",         # 可能出现"一级及以上资质"、"一级建造师"等
            "二级",
            "三级",
        ],
        "recommended": [
            "资格",
            "承包",
            "许可",
        ],
    },
    "certifications": {
        "required": [
            "ISO 9001",
            "ISO9001",
            "ISO 14001",
            "ISO14001",
            "ISO 45001",
            "ISO45001",
            "CMMI",
            "高新技术企业",
            "软件企业认定",
            "安全生产许可证",
            "ISO27001",
        ],
        "recommended": [
            "认证",
            "许可",
            "证书",
            "资质证书",
        ],
    },
    "similar_projects": {
        "required": [
            "类似项目业绩",
            "类似工程业绩",
            "同类项目业绩",
            "同类工程业绩",
            "项目业绩",
            "工程业绩",
            "近三年类似",
            "类似项目",
            "类似工程",
        ],
        "recommended": [
            "业绩",
            "类似",
            "已完成项目",
            "合同关键页",
            "竣工验收证明",
        ],
    },
    "personnel": {
        "required": [
            "项目经理",
            "技术负责人",
            "项目管理机构",
            "项目管理人员",
            "主要管理人员",
            "拟派项目",
            "拟派人员",
        ],
        "recommended": [
            "建造师",
            "造价工程师",
            "注册工程师",
            "安全员",
            "项目技术负责人",
            "项目负责人",
            "管理机构",
            "管理人员",
            "人员配备",
        ],
    },
    "financials": {
        "required": [
            "财务状况",
            "财务报表",
            "审计报告",
            "资产负债率",
            "营业收入",
            "净利润",
            "净资产",
            "资产总额",
            "负债总额",
            "近三年财务",
            "财务情况",
        ],
        "recommended": [
            "财务",
            "审计",
            "资产",
            "营收",
            "利润",
            "现金流",
        ],
    },
    "contact": {
        "required": [
            "开户银行",
            "银行账号",
            "付款账户",
            "投标保证金",
            "支付账户",
        ],
        "recommended": [
            "联系方式",
            "联系电话",
            "邮箱",
            "银行",
            "账号",
        ],
    },
}


# ---------- 抽取文本收集 ----------
def _collect_texts(extraction: dict) -> list[str]:
    """从抽取结果里把所有可能和投标人资料相关的文本拼出来。"""
    texts: list[str] = []

    # 基础字段
    basic = extraction.get("basic") if isinstance(extraction.get("basic"), dict) else {}
    for v in basic.values():
        if isinstance(v, str) and v.strip():
            texts.append(v)

    # 评分因素（最常出现业绩/人员/财务关键词的地方）
    for factor in _as_list(
        extraction.get("evaluation_factors")
        or extraction.get("evaluation", {}).get("factors")
    ):
        texts.extend([
            factor.get("name", ""),
            factor.get("category", ""),
            factor.get("source_quote", ""),
        ])

    # 资质要求
    quals = extraction.get("qualification_items") or extraction.get("qualifications")
    for item in _as_list(quals):
        texts.extend([
            item.get("requirement", ""),
            item.get("category", ""),
            item.get("source_quote", ""),
        ])
        thresholds = _as_list(item.get("thresholds"))
        texts.extend(thresholds)

    # 格式条目
    for item in _as_list(extraction.get("bid_format_items")):
        texts.extend([
            item.get("title", ""),
            item.get("content", ""),
        ])

    # 评分表（有的抽取会放 score_table 里）
    score_table = extraction.get("evaluation", {}).get("score_table") if isinstance(
        extraction.get("evaluation"), dict
    ) else []
    for row in _as_list(score_table):
        for v in row.values():
            if isinstance(v, str):
                texts.append(v)

    # 清洗空值
    return [re.sub(r"\s+", " ", t).strip() for t in texts if isinstance(t, str) and t.strip()]


def _as_list(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# ---------- 匹配 ----------
def _match_field(texts: list[str], keywords: list[str]) -> list[str]:
    """返回所有命中的原文片段（截取关键词前后各 12 字）。"""
    hits: list[str] = []
    for text in texts:
        low = text.lower()
        for kw in keywords:
            idx = low.find(kw.lower())
            if idx == -1:
                continue
            start = max(0, idx - 12)
            end = min(len(text), idx + len(kw) + 12)
            snippet = text[start:end].replace("\n", " ")
            if snippet not in hits:
                hits.append(snippet)
    return hits


# ---------- 对外接口 ----------
def infer_profile_schema(
    extraction: dict,
    label_map: dict[str, str],
) -> dict[str, dict]:
    """根据抽取结果推断每个资料字段的级别与命中理由。

    返回形如：
    {
      "company_name": {"level": "required", "reason": "标书通用必填项"},
      "similar_projects": {"level": "required", "reason": "...近三年类似供水工程业绩数量..."},
      "financials": {"level": "recommended", "reason": "...近三年营业收入..."},
      ...
    }
    """
    texts = _collect_texts(extraction)
    result: dict[str, dict] = {}

    for key, label in label_map.items():
        if key in _CORE_REQUIRED_KEYS:
            result[key] = {
                "level": "required",
                "label": label,
                "reason": "标书通用必填项（任何投标都要）",
                "matched": False,
            }
            continue

        rule = _FIELD_RULES.get(key, {})
        required_hits = _match_field(texts, rule.get("required", []))
        if required_hits:
            level = "required"
            reason = "；".join(required_hits[:3])
        else:
            recommended_hits = _match_field(texts, rule.get("recommended", []))
            if recommended_hits:
                level = "recommended"
                reason = "；".join(recommended_hits[:3])
            else:
                level = "optional"
                reason = "标书抽取结果未提及，如与贵司情况相符可填写"

        result[key] = {
            "level": level,
            "label": label,
            "reason": reason,
            "matched": level != "optional",
        }

    return result

"""章节重点路由与提示词构造（核心算法 A10）。"""
from __future__ import annotations

# 提示词版本：进入单元缓存标签 `{parser}|{PROMPT_VERSION}`（A10/缓存失效）。
PROMPT_VERSION = "v1"

# 优先级从高到低；haystack 命中任一关键词即命中该重点（A10）。
# 顺序关键：技术条款必须先于合同、投标人须知/资格垫底，避免「合同技术条款」
# 被错误归为合同、以及普通章节被资格条件干扰。
FOCUS_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("评标", "评审", "评分", "打分"), "evaluation"),
    (("投标文件格式",), "bid_format"),
    (("工程量清单",), "bill_of_quantities"),
    (("技术标准", "技术条款"), "tech"),
    (("合同",), "contract"),
    (("投标人须知", "资格"), "qualification"),
)

DEFAULT_FOCUS = "basic"

FOCUS_DESC: dict[str, str] = {
    "evaluation": "评标办法、评分因素、初步评审项、评分表",
    "bid_format": "投标文件组成与格式条目",
    "bill_of_quantities": "工程量清单可用性、项数、主要项目",
    "tech": "技术标准与技术条款要求",
    "contract": "付款、履约、质保、违约、争议等合同要点",
    "qualification": "投标人资格条件（资质、财务、业绩、信誉、人员）",
    "basic": "项目基础信息（项目名称、招标编号、预算金额、投标截止时间、开标地点）",
}


def focus_for_chapter(path: list[str], title: str) -> str:
    """返回章节重点标签（A10）。`path` 为祖先标题链，`title` 为当前标题。"""
    haystack = " ".join([*path, title])
    for keywords, focus in FOCUS_RULES:
        if any(keyword in haystack for keyword in keywords):
            return focus
    return DEFAULT_FOCUS


_SYSTEM_TEMPLATE = """你是一名招标文件结构化抽取助手。从给定的章节文本中提取以下信息，
并只输出一个 JSON 对象（不要输出额外解释）：

{
  "basic": {
    "project_name": "项目名称",
    "tender_no": "招标/采购编号",
    "budget_amount": "预算金额原文",
    "bid_deadline": "投标截止时间原文",
    "bid_open_place": "开标地点原文"
  },
  "evaluation": {
    "factors": [{"category": "商务/技术/报价/形式评审/资格评审/响应性评审", "name": "因素名称", "score": "分值（如 30 或 30分）", "source_quote": "原文引用"}],
    "preliminary_review": ["初步评审项"],
    "score_table": [{"factor": "评分项", "score": "分值", "comment": "说明"}]
  },
  "qualification": {
    "items": [{"category": "资质/财务/业绩/信誉/人员", "requirement": "资格要求", "source_quote": "原文引用", "thresholds": ["门槛/标准"]}]
  },
  "bid_format": {
    "items": [{"title": "投标文件组成条目", "content": "格式要求"}]
  },
  "contract": {
    "highlights": ["合同要点"]
  },
  "tech": {
    "requirements": ["技术需求/参数/服务要求"]
  }
}

规则：
- 找不到的字段给空字符串或空数组，不要编造；
- 分值、阈值、金额等尽量保留原文措辞；
- source_quote 必须逐字摘自原文，用于溯源核对。
"""


def build_prompt(
    path: list[str], title: str, text: str, focus: str | None = None
) -> list[dict[str, str]]:
    """构造一次抽取调用的 messages（A10）。

    `focus` 为 None 时按标题与祖先路径自动路由。
    """
    if focus is None:
        focus = focus_for_chapter(path, title)
    emphasis = FOCUS_DESC.get(focus, FOCUS_DESC[DEFAULT_FOCUS])
    breadcrumb = " > ".join([*path, title]) if path else title

    user = (
        f"章节：{breadcrumb}\n"
        f"本章重点提取：{emphasis}。\n"
        f"以下章节文本（含 Markdown 与页面标记）：\n\n{text}"
    )
    return [
        {"role": "system", "content": _SYSTEM_TEMPLATE},
        {"role": "user", "content": user},
    ]
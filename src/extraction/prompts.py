"""章节重点路由与提示词构造（docs/algorithms.md A10）。"""
from __future__ import annotations

# 提示词版本：进入单元缓存标签 `{parser}|{PROMPT_VERSION}`（A10/缓存失效）。
PROMPT_VERSION = "v5"

# 优先级从高到低；haystack 命中任一关键词即命中该重点（A10）。
# 顺序关键：评标类优先于格式，避免「评分办法」落在 basic；
# 工程类专用关键词（工程量清单）先于通用技术条款；
# 服务/咨询/活动类关键词（课程、活动、方案、培训）独立于技术条款路由。
FOCUS_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("评标", "评审", "评分", "打分", "评分办法", "评审办法"), "evaluation"),
    (("投标文件格式", "响应文件格式", "投标组成", "响应文件组成"), "bid_format"),
    (("工程量清单", "清单计价"), "bill_of_quantities"),
    # 服务/活动/咨询类的需求与方案（独立于工程技术条款）
    (("课程体系", "课程设置", "培训计划", "培训方案", "活动方案",
      "活动日程", "实施方案", "服务方案", "采购需求", "需求清单"), "service_spec"),
    # 工程类技术条款
    (("技术标准", "技术条款", "技术要求", "施工规范", "施工要求"), "tech"),
    (("合同", "履约", "验收", "质保", "付款方式"), "contract"),
    (("投标人须知", "资格要求", "资格条件", "供应商资格", "应答人资格"), "qualification"),
)

DEFAULT_FOCUS = "basic"

FOCUS_DESC: dict[str, str] = {
    "all": "全部字段（基础信息、评标办法、资格条件、投标格式、合同、技术需求、服务需求）",
    "evaluation": "评标办法、评分因素、初步评审项、评分表",
    "bid_format": "投标/响应文件组成与格式条目",
    "bill_of_quantities": "工程量/服务量清单可用性、项数、主要项目",
    "service_spec": "服务/活动/培训/咨询的需求清单、课程体系、活动日程、方案要求",
    "tech": "技术标准与技术条款要求（工程类适用）",
    "contract": "付款、履约、质保、违约、争议等合同要点",
    "qualification": "投标人/供应商资格条件（资质/财务/业绩/信誉/人员/团队）",
    "basic": "项目基础信息（项目名称、采购编号、预算/最高限价、投标截止时间、开标/截标地点）",
}


def focus_for_chapter(path: list[str], title: str) -> str:
    """返回章节重点标签（A10）。`path` 为祖先标题链，`title` 为当前标题。"""
    if title == "全文":
        # 无章节结构时整篇作为一个单元，必须全字段抽取，不能退化为 basic
        return "all"
    haystack = " ".join([*path, title])
    for keywords, focus in FOCUS_RULES:
        if any(keyword in haystack for keyword in keywords):
            return focus
    return DEFAULT_FOCUS


_SYSTEM_TEMPLATE = """你是一名招标文件结构化抽取助手。从给定的章节文本中提取信息，
只输出一个 JSON 对象（不要输出额外解释、不要用 markdown 代码块包裹）。

识别并灵活支持以下项目类型：
  - 工程施工类：含"工程量清单""施工图纸""施工组织设计"
  - 货物采购类：含"设备清单""货物规格""技术参数"
  - 服务/咨询/培训类：含"课程体系""培训计划""服务方案""实施方案"
  - 活动/运营/HR 类：含"活动日程""运营方案""人员配置"

抽取字段（找不到给空字符串或空数组，不要编造；source_quote 必须逐字摘自原文）：

{
  "basic": {
    "project_name": "项目/采购/活动/咨询名称",
    "tender_no": "招标/采购编号",
    "budget_amount": "预算金额/最高限价原文",
    "bid_deadline": "投标/应答截止时间原文",
    "bid_open_place": "开标/截标地点原文"
  },
  "evaluation": {
    "factors": [{"category": "商务/技术/报价/形式评审/资格评审/响应性评审/服务方案/实施方案/团队配置",
                 "name": "因素名称",
                 "score": "分值（如 30 或 30分）",
                 "source_quote": "原文引用"}],
    "preliminary_review": ["初步评审/符合性审查项"],
    "score_table": [{"factor": "评分项", "score": "分值", "comment": "说明"}]
  },
  "qualification": {
    "items": [{"category": "资质/财务/业绩/信誉/信用/人员/团队/师资/案例/注册资金",
               "requirement": "资格要求原文（一个审查因素一条）",
               "source_quote": "原文引用",
               "thresholds": ["门槛/标准"]}]
  },
  "bid_format": {
    "items": [{"title": "投标/响应文件中的单个格式/模板/表格名称",
               "content": "该格式的填写、装订、签署要求"}]
  },
  "contract": {
    "highlights": ["合同要点（付款节点/服务期限/验收标准/质保期/违约责任/争议解决）"]
  },
  "tech": {
    "requirements": ["技术需求/参数/标准（工程/货物/IT 类适用）"]
  },
  "service_spec": {
    "requirements": ["服务/活动/培训的需求清单、课程体系、活动日程、方案要求、人员配置表"]
  }
}

规则：
- 找不到的字段给空字符串或空数组，不要编造；
- 分值、阈值、金额等尽量保留原文措辞；
- source_quote 必须逐字摘自原文，用于溯源核对；
- 即使章节看似不属于某个重点，只要文中出现评分、资格、格式、合同、技术或服务要求，也必须抽取到对应字段，不得因「非重点」而留空；
- qualification.items 按表格逐行抽取：每个审查因素/序号（如 1、1-1、1-2、2-1、2-2、3-1）单独一条，不得合并为少数几条；
- bid_format.items 逐个格式/模板抽取：每个独立的文件模板或表格（封面、投标书、授权委托书、身份证明、开标一览表、分项报价表、偏离表、声明函、人员情况表、业绩一览表等）各一条，条目数应与原文格式数量一致；
- 服务/活动/培训/咨询需求填入 service_spec；明确标注为技术要求、技术参数、技术规格的内容仍填入 tech，不得因本章重点是服务而丢弃。
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
# 核心算法与投标编写代码精读

> 本文件由原《核心算法》《代码精读-投标编写》于 2026-11-06 归并而成；精读笔记中的行号为当前 main 快照，代码演进后可能漂移，定位以符号名为准。

## 目录

- 第一部分　核心算法（原《核心算法》）
- 第二部分　投标编写代码精读（原《代码精读-投标编写》）

## 第一部分　核心算法（原《核心算法》）

> 版本快照：2026-11-06  
> 适用代码版本：当前 main  
> 文档范围：文档结构还原、PDF 分片与合并、结构化抽取、检索索引、投标文件编写、产物汇总。  
> 配套文档：`api-contract.md`（业务与接口）、`tech-decisions.md`（技术与版本）、`AGENTS.md`（开发约定）。

---

### 1. 文档说明

#### 1.1 目标

本文档描述系统中最影响结果的算法与规则，包括：

- 输入、输出、关键参数；
- 计算步骤与判定公式；
- 伪代码或核心片段；
- 失效模式与边界条件；
- 参数默认值、复杂度与测试覆盖。

#### 1.2 阅读约定

- 伪代码使用 Python 风格，仅表达逻辑，不保证可直接运行；
- 变量名尽量与源码一致；
- “章节”通常指 PDF 书签树中的一个节点；
- “单元”指一次模型调用处理的文本块；
- “片段”指写入检索索引的最小文本块；
- 所有字符长度均按 Python `len(str)` 计算（中文按 1 个字符计）。

#### 1.3 算法总览

```text
PDF
 ├─ 书签读取 ───────────────┐
 ├─ 页面文本提取            │
 ├─ 页面归属（最深书签）     │
 ├─ 章节组装 / Markdown      │
 └─ 无书签兜底（按页）       │
                            ▼
                  ParsedDocument
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
     结构化抽取         检索索引            投标编写
   （按章 map-reduce）  （章节/按页分块）   （计划→分章节→汇总）
          │                 │                 │
          ▼                 ▼                 ▼
   extraction.json      Chroma 集合        bid.md/docx/report.json
```

算法清单：

| 编号 | 算法 | 主要代码 |
|---|---|---|
| A1 | 书签读取与页码范围 | `src/parsing/toc.py::read_toc` |
| A2 | 页面归属最深书签 | `src/parsing/toc.py::assign_pages_to_entries` |
| A3 | 章节与 Markdown 组装 | `src/parsing/toc.py::compose_sections` |
| A4 | 目录页识别 | `src/parsing/toc.py::looks_like_toc_page` |
| A5 | PDF 均衡分片 | `src/parsing/splitter.py::plan_splits` |
| A6 | 云端结果合并 | `src/parsing/builder.py::merge_mineru` |
| A7 | 本地页面清洗 | `src/parsing/local_parser.py::_clean_page_text` |
| A8 | HTML 表格扁平化 | `src/textutils.py::html_tables_to_text` |
| A9 | 抽取单元切分 | `src/extraction/extractor.py::build_units` |
| A10 | 章节重点路由 | `src/extraction/prompts.py::focus_for_chapter` |
| A11 | 宽容字段归一化 | `src/extraction/schema.py` |
| A12 | 抽取结果合并 | `src/extraction/extractor.py::merge_partials` |
| A13 | 溯源核对 | `src/extraction/extractor.py::ground_check` |
| A14 | 截断二分重试 | `src/extraction/extractor.py::_extract_unit` |
| A15 | 章节感知分块 | `src/vectorstore/chunking.py::chunk_document` |
| A16 | 幂等入库 | `src/vectorstore/ingest.py::ingest_document` |
| A17 | 共享检索 | `src/vectorstore/retrieval.py::ChromaRetriever` |
| A18 | 大纲生成与归一化 | `src/bid_writer/agents.py` |
| A19 | 章节写作—评审—修订循环 | `src/bid_writer/agents.py::section_agent` |
| A20 | 覆盖报告计算 | `src/bid_writer/assembly.py::build_report` |
| A21 | Markdown → Word | `src/bid_writer/docx_export.py` |
| A22 | 任务状态与进度节流 | `src/tasks.py`、`api/dispatch.py` |
| A23 | 文档类型判定 | `src/parsing/builder.py::detect_doc_type` |
| A24 | 章节辅助结构 | `src/parsing/toc.py` |

---

### 2. 文档结构还原

#### 2.1 书签读取与页码范围（A1）

**输入**：已打开的 PDF 文档。  
**输出**：`TocEntry` 列表，每项包含 `level`、`title`、`page`、`page_end`。

##### 步骤

1. 读取 PDF 书签 `get_toc(simple=True)`，得到 `(level, title, page)` 序列；
2. 对每个节点：
   - 页码钳制到 `[1, page_count]`；
   - 默认 `page_end = page_count`；
   - 向后扫描第一个 `level <= 当前 level` 的节点；
   - 若存在，`page_end = max(page, 后继页码 - 1)`；
   - 若不存在，延伸到文档末页。

##### 伪代码

```python
for i, (level, title, page) in enumerate(raw):
    page = clamp(page, 1, total)

    end = total
    for later_level, later_title, later_page in raw[i + 1:]:
        if later_level <= level:
            end = max(page, later_page - 1)
            break

    entries.append(TocEntry(level, title, page, end))
```

##### 设计说明

- 同层节点之间不重叠；
- 子节点区间包含在父节点区间内；
- 同级后一个节点开始时，前一个节点结束；
- 页码范围用于后续“页面归属”。

##### 复杂度

- 时间：`O(L^2)`，`L` 为书签节点数；
- 空间：`O(L)`；
- 招标文件书签通常几十到几百个，`O(L^2)` 可接受。

##### 边界

- 书签为空 → 返回空列表，后续走无书签路径；
- 页码为 0 或越界 → 钳制；
- 书签层级跳跃（如 1 → 3）→ 仍按数值层级处理，页面归属取最深节点；
- 书签标题重复 → 不合并，按顺序保留。

#### 2.2 页面归属最深书签（A2）

**输入**：

- `entries`：书签节点；
- `pages`：`{1-based 页码: 页面文本}`。

**输出**：

- `assigned`：`{节点下标: [该节点负责的页面块]}`；
- `uncovered`：没有被任何书签覆盖的页面块。

##### 步骤

1. 按页码升序遍历非空页面；
2. 为页面构造块：`<!-- page: p -->\n{text}`；
3. 找出所有满足 `entry.page <= p <= entry.page_end` 的节点；
4. 若有多个，取 `(level, index)` 最大的节点——即“最深层、同层靠后”；
5. 将页面块加入该节点；
6. 若没有节点覆盖，加入 `uncovered`。

##### 伪代码

```python
for page_no in sorted(pages):
    text = pages[page_no]
    if not text.strip():
        continue

    block = f"<!-- page: {page_no} -->\n{text}"
    covering = [
        i for i, entry in enumerate(entries)
        if entry.page <= page_no <= entry.page_end
    ]

    if covering:
        best = max(covering, key=lambda i: (entries[i].level, i))
        assigned[best].append(block)
    else:
        uncovered.append(block)
```

##### 设计说明

- 同一页可能同时落在父节点和子节点区间内，取最深节点避免正文重复；
- 同层节点区间理论上不重叠；若因异常书签重叠，取靠后节点；
- 封面、目录、未覆盖页进入 `uncovered`，不会被丢弃。

##### 复杂度

- 时间：`O(P × L)`，`P` 为页数，`L` 为书签数；
- 空间：`O(P)`；
- 200 页、200 个书签时约 4 万次比较，开销可忽略。

##### 边界

- 书签页码错误、跨页区间异常 → 页面可能落到错误节点；
- 页面文本为空 → 不参与归属；
- 目录页本身也会被归属，后续分块时再识别并跳过。

#### 2.3 章节与 Markdown 组装（A3）

**输入**：`entries`、`assigned`、`uncovered`。  
**输出**：`(markdown, sections)`。

##### 步骤

1. 若有 `uncovered`，先把它们按原顺序拼接，放在 Markdown 最前面；
2. 遍历每个书签节点：
   - 标题层级映射为 `min(level + 1, 6)` 个 `#`；
   - 正文为该节点 `assigned` 的页面块拼接；
   - 生成 `Section`：
     - `index`：书签下标；
     - `level`、`number`、`title`；
     - `path`：祖先标题链；
     - `page_start`、`page_end`；
     - `text`：该节点自己的正文；
3. Markdown 为所有部分以空行连接。

##### 伪代码

```python
parts = ["\n\n".join(uncovered)] if uncovered else []

for i, entry in enumerate(entries):
    body = "\n\n".join(assigned.get(i, []))
    heading = "#" * min(entry.level + 1, 6) + " " + entry.title
    parts.append(f"{heading}\n\n{body}".rstrip())
    sections.append(Section(
        index=i,
        level=entry.level,
        number=section_number(entry.title),
        title=entry.title,
        path=ancestor_titles(entries, i),
        page_start=entry.page,
        page_end=entry.page_end,
        text=body,
    ))

return "\n\n".join(parts), sections
```

##### 说明

- `Section.text` **只包含该节点自己的正文**，不包含子节点；
- 需要子树正文时使用 `subtree_text` 拼接后代；
- 空节点仍保留在 `sections` 中，`text` 为空；
- `uncovered` 内容进入 Markdown，但不属于任何章节。

#### 2.4 目录页识别（A4）

**目标**：分块时跳过目录页，避免把“第一章……2”这类导航文本写入检索索引。

##### 判定规则

对每行非空文本：

- 匹配 `[.．·]{3,}\s*\d+\s*$`（3 个以上引导点 + 页码）；或
- 匹配 `[.．]{6,}`（旧规则，兼容长引导点）。

若满足条件的行数 `L`：

```text
L >= 2 且 L / 非空行数 >= 0.3
```

则判定为目录页。

##### 伪代码

```python
lines = [line for line in text.splitlines() if line.strip()]
leaders = sum(
    1 for line in lines
    if TOC_LEADER_RE.search(line) or re.search(r"[.．]{6,}", line)
)
return leaders >= 2 and leaders / len(lines) >= 0.3
```

##### 设计说明

- 真实招标文件常见 4 个引导点（`第一章 投标邀请....2`），旧规则要求 6 个会漏判；
- 要求至少 2 行，避免把正文中偶尔出现的点线误判为目录；
- 比例为 0.3，兼顾目录页与“目录 + 说明”混合页。

##### 边界

- 单行目录页可能漏判；
- 正文中连续两行以“点线 + 数字”结尾可能误判；
- 目录页漏判只会增加噪声，不会导致正文丢失。

#### 2.5 无书签与无章节处理

三类情况需要区分：

| 情况 | 解析产物 | 抽取 | 分块 |
|---|---|---|---|
| 有书签、正文正常 | `sections` 非空 | 按章切分 | 按章节分块 |
| 无书签、正文正常 | `sections=[]`，Markdown 带页码注释 | 全文分段 | 按页兜底分块 |
| 无书签、正文为空 | `sections=[]`，Markdown 为空 | 报错 | 无法入库 |

无书签时的 Markdown 由页面顺序拼接，并带 `<!-- page: N -->` 注释：

```python
markdown = "\n\n".join(
    f"<!-- page: {i + 1} -->\n{text}"
    for i, text in enumerate(page_texts)
    if text
)
```

云端解析在无书签时会把所有页面放入 `uncovered`，行为与本地解析一致：正文保留、章节为空。

#### 2.6 文档类型判定（启发式）

**输入**：解析后的 Markdown 与书签数量。  
**输出**：`tender_file` / `announcement` / `unknown`。

规则：

1. 统计三个特征词是否出现：
   - `评标办法`
   - `投标人须知`
   - `投标文件格式`
2. 命中 2 个及以上 → `tender_file`；
3. 否则若出现 `招标公告` → `announcement`；
4. 否则为 `unknown`；
5. 若仍为 `unknown` 且书签数量 > 20 → `tender_file`。

用途：

- 决定检索集合：招标文件 → `tender_document`，招标公告 → `tender_announcement`；
- 影响抽取字段的默认预期；
- 只是启发式，不改变正文。

#### 2.7 章节辅助结构

##### `section_number(title)`

按优先级匹配标题编号前缀：

```text
^第[一二三四五六七八九十百]+[章卷节]
^附件[一二三四五六七八九十\d]+
^表\d+
^[一二三四五六七八九十]+、
^\d+(?:\.\d+)*[.、\s]
^[（(][一二三四五六七八九十\d]+[）)]
```

返回第一个命中的前缀，否则返回空字符串。用于检索片段的 `section_no`。

##### `ancestor_titles(entries, index)`

从当前节点向前扫描：

1. 目标层级 = 当前层级 - 1；
2. 遇到同目标层级的节点 → 加入链；
3. 目标层级减 1，直到 1；
4. 反转得到从根到父的顺序。

用于章节面包屑与提示词中的“上级章节”。

##### `subtree_range(entries, index)`

返回节点子树的左闭右开区间：

```text
start = index
end = 第一个后继节点中 level <= 当前 level 的下标
若不存在，end = len(entries)
```

##### `subtree_text(sections, entries, index)`

拼接 `subtree_range` 内所有非空章节正文，用于：

- 判断整章是否能在一次抽取调用内完成；
- 章节子树正文兜底。

##### `chapter_indices(entries)`

返回标题匹配 `^第[一二三四五六七八九十百]+章` 的节点下标，作为抽取的 map 起点。

---

### 3. PDF 均衡分片（A5）

**目标**：绕过云端解析单文件页数上限，同时保证各分片大小均衡、页码可还原。

#### 3.1 分片数量

设：

- `N`：总页数；
- `M`：单分片最大页数（默认 200）；
- `P`：分片数量。

公式：

```text
P = ceil(N / M)
```

#### 3.2 每片页数

```text
base = floor(N / P)
remainder = N mod P
```

前 `remainder` 个分片大小为 `base + 1`，其余为 `base`。

这样可保证：

- 每片不超过 `M`；
- 各片大小差不超过 1；
- 不产生空分片。

#### 3.3 伪代码

```python
if page_count <= 0:
    return []
if page_count <= max_pages:
    return [(0, page_count - 1)]

parts = ceil(page_count / max_pages)
base, remainder = divmod(page_count, parts)

ranges = []
start = 0
for i in range(parts):
    size = base + (1 if i < remainder else 0)
    ranges.append((start, start + size - 1))
    start += size
```

#### 3.4 页偏移还原

每个分片记录：

- `from_page`、`to_page`：分片内 0-based 闭区间；
- `page_offset`：分片在原始文档中的 0-based 起始页。

合并时：

```text
原始页码 = page_idx + page_offset + 1
```

#### 3.5 边界

- 无需分片时，直接返回指向原文件的单个分片，不复制文件；
- 加密 PDF 直接报错；
- 0 页 PDF 报错；
- 分片按原始页顺序排列，保证合并后的顺序稳定。

---

### 4. 云端解析结果合并（A6）

#### 4.1 content_list 解析

每个分片解压后，优先读取 `*content_list*.json`。

每个块包含：

- `page_idx`：分片内 0-based 页码；
- `type`：文本 / 表格 / 图片；
- `text`、`table_body`、`img_path`、`table_caption`、`img_caption` 等字段。

转换为文本：

| 类型 | 规则 |
|---|---|
| 表格 | `table_caption` + `table_body`（或 `text`） |
| 图片 | `![caption](img_path)` |
| 其他 | `text` |

页面聚合：

```text
pages[page_idx + page_offset + 1] += block_text
```

同一页的多个块以空行连接。

#### 4.2 多分片合并

```python
pages = {}
for output in outputs:
    if output.content_list_path is None:
        usable = False
        continue
    pages.update(read_content_list(output.content_list_path, output.page_offset))
```

注意：

- `page_offset` 保证分片页码映射回原始文档；
- 不同分片的页码区间不重叠；
- 图片按分片前缀复制到统一图片目录。

#### 4.3 组装与降级

若 `content_list` 全部可用且 `pages` 非空：

```text
assigned, uncovered = assign_pages_to_entries(entries, pages)
markdown, sections = compose_sections(entries, assigned, uncovered)
```

否则降级：

- 按分片顺序读取 `full.md` 或其他 Markdown；
- 直接以空行拼接；
- `sections=[]`；
- 标记 `structure_source="markdown_order"`、`degraded=True`；
- 记录警告“无法从 content_list 还原页码”。

#### 4.4 输出查找顺序

- Markdown：优先 `full.md`，否则取排序后的第一个 `*.md`；
- content_list：取排序后的第一个 `*content_list*.json`。

#### 4.5 失效模式

| 情况 | 结果 |
|---|---|
| 某个分片缺 content_list | 整体降级为 Markdown 拼接 |
| content_list 解析异常 | 整体降级 |
| content_list 为空 | 降级 |
| 无书签 | `sections=[]`，全文进入 `uncovered` |
| 图片复制失败 | 单张跳过，记录警告 |

---

### 5. 本地解析与文本清洗（A7、A8）

#### 5.1 页面清洗

本地解析对每页文本逐行处理：

1. 去除首尾空白；
2. 丢弃匹配以下规则的行：
   - 文档指纹：`^[0-9a-f]{20,}-\d{10,}$`；
   - 纯页码：`^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$`；
3. 保留其他行；
4. 收敛连续空行；
5. 去除首尾空行。

目的：减少页眉页脚对分块与抽取的干扰。

#### 5.2 图片抽取

- 遍历每页 `get_images(full=True)`；
- 按 `xref` 去重，同一图片多次出现只保存一次；
- 文件名：`p{页码:03d}_x{xref}.{ext}`；
- 单张图片失败只记录警告，不中断解析。

#### 5.3 无书签

- 所有页面按顺序拼接 Markdown；
- `sections=[]`；
- 记录警告；
- 元信息标记 `structure_source="page_order"`。

#### 5.4 HTML 表格扁平化

云端解析可能把表格输出为 HTML。处理步骤：

1. 用正则定位 `<table>...</table>`；
2. 用 HTML 解析器收集 `tr` / `td` / `th`；
3. 每行单元格以 ` | ` 连接；
4. 解析失败时保留原始 HTML。

示例：

```html
<table><tr><td>因素</td><td>分值</td></tr>
<tr><td>投标报价</td><td>50</td></tr></table>
```

转换为：

```text
因素 | 分值
投标报价 | 50
```

目的：去掉 HTML 噪声，保留列关系，避免定长切分打断标签。

---

### 6. 结构化抽取（A9–A14）

#### 6.1 总体思路

招标文件可能长达十几万字，单次调用既超上下文也超输出预算。算法采用**按章 map-reduce**：

1. 把文档切成不超过 12000 字的抽取单元；
2. 每个单元独立调用模型，产出 `ChapterExtraction`；
3. 合并所有部分结果；
4. 对关键字段做原文核对。

#### 6.2 单元切分（A9）

##### 6.2.1 章节选择

- 若存在 `第X章` 节点，以这些节点为 map 起点；
- 若没有章节点但存在正文，退化为全文按段落切分；
- 若正文为空，抛 `ExtractError`。

`chapter_indices` 的判定：

```text
^第[一二三四五六七八九十百]+章
```

##### 6.2.2 递归细分

对章节 `index`：

```text
own_text  = section[index].text
full_text = subtree_text(index)
```

- 若 `full_text <= limit`：整章作为一个单元；
- 否则：
  - 若有自身正文，按段落切分；
  - 对每个直接子节点递归；
  - 若既无自身正文也无子节点，则对 `full_text` 按段落切分。

##### 6.2.3 段落打包

```python
paragraphs = split_by_blank_lines(text)
buffer, size = [], 0

for paragraph in paragraphs:
    if len(paragraph) > limit:
        flush(buffer)
        units.extend(hard_slice(paragraph, limit))
        continue

    if size + len(paragraph) > limit and buffer:
        flush(buffer)
        buffer, size = [], 0

    buffer.append(paragraph)
    size += len(paragraph)

flush(buffer)
```

特点：

- 优先保持段落完整；
- 单段超长时按固定长度硬切；
- 单元之间不重叠；
- 每个单元携带标题与祖先路径。

##### 6.2.4 参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `MAX_CHARS_PER_CALL` | 12000 | 单次抽取最大字符数 |
| 截断二分最小长度 | 1200 | 低于此长度不再二分 |
| 二分最大深度 | 2 | 最多递归两层 |

#### 6.3 章节重点路由（A10）

按“祖先路径 + 当前标题”拼接成 `haystack`，按顺序匹配：

| 优先级 | 关键词 | 抽取重点 |
|---|---|---|
| 1 | 评标 / 评审 / 评分 / 打分 | 评标办法、评分因素、初步评审、评分表 |
| 2 | 投标文件格式 | 投标文件组成与格式条目 |
| 3 | 工程量清单 | 清单可用性、项数、主要项目 |
| 4 | 技术标准 / 技术条款 | 技术要求 |
| 5 | 合同 | 付款、履约、质保、违约、争议 |
| 6 | 标题含“投标人须知”或“资格” | 资格条件 |
| 7 | 其他 | 项目基础信息 |

设计原因：

- 只按当前标题判断会漏掉“第八章 投标文件格式”下的子节；
- “合同技术条款”必须优先于“合同”，否则会被归为合同要点；
- 不是所有章节都需要全字段，减少无关字段的诱导性填空。

#### 6.4 JSON 调用与容错

##### 6.4.1 调用与容错策略（与当前实现一致）

- 抽取调用固定使用 JSON 模式（`json_mode=True`），不做模式降级；
- `finish_reason == "length"` 抛「输出被 max_tokens 截断」，空内容抛「返回空内容」；
- 单元级容错在 `_extract_unit` 中实现，均为硬编码策略、不受配置驱动：
  - 结果按章节 focus 判定为空时，用相同入参原样重试一次，避免偶发空响应丢字段；
  - 截断错误且单元文本超过阈值时，按中点二分递归为上下两个单元分别抽取后合并，递归深度有上限；
- 文本 LLM 调用层没有自动指数退避；唯一接入 SDK 重试的是嵌入层
  （`EMBEDDING_MAX_RETRIES`，429 时由 OpenAI SDK 指数退避）；
- 不存在 `LLM_MAX_RETRIES`、`MINERU_RETRY_BACKOFF` 配置项，历史文档中的退避公式为过时描述，已移除。

##### 6.4.2 JSON 提取

```python
text = strip_code_fences(text)
try:
    return json.loads(text)
except JSONDecodeError:
    pass

start = text.find("{")
end = text.rfind("}")
if start >= 0 and end > start:
    return json.loads(text[start:end + 1])

raise ExtractError("未找到合法 JSON 对象")
```

##### 6.4.3 截断判定

- `finish_reason == "length"` → 抛 `ExtractError("输出被 max_tokens 截断")`；
- 空内容 → 抛 `ExtractError("返回空内容")`。

#### 6.5 宽容字段归一化（A11）

模型输出常出现：

- 列表字段给 `null`；
- 分值写成 `"30分"`；
- 布尔写成 `"是"` / `"否"`；
- 类别写成近义词；
- 基础字段包在 `basic` / `info` / `project` 子对象中。

##### 6.5.1 字符串列表

```text
None → []
"abc" → ["abc"]
["a", None, "b"] → ["a", "b"]
其他 → [str(value)]
```

##### 6.5.2 数值

```text
None / "" → None
bool → None
int / float → float
字符串 → 提取第一个 -?\d+(?:\.\d+)?，失败则 None
```

##### 6.5.3 布尔

真值集合：

```text
是, true, yes, y, 1, 需要, 必须, 须, 要求
```

假值集合：

```text
否, false, no, n, 0, 无需, 不需要, 空字符串
```

其他 → `None`。

##### 6.5.4 类别

在目标字符串中查找第一个命中的类别关键词：

- 资格：`资质`、`财务`、`业绩`、`信誉`、`人员`；
- 评分：`形式评审`、`资格评审`、`响应性评审`、`商务`、`技术`、`报价`；
- 评审阶段：`形式评审`、`资格评审`、`响应性评审`；
- 未命中 → `其他`。

##### 6.5.5 字段级降级

1. 展开 `basic` / `info` / `project` / `basic_info` / `project_info`；
2. 只保留 schema 已声明字段；
3. 先尝试整对象校验；
4. 失败后逐字段单独校验，非法字段丢弃，合法字段保留；
5. 记录警告“部分字段不符合 schema”。

#### 6.6 结果合并（A12）

##### 6.6.1 标量

- 跳过 `None`；
- 目标为空（`None`、`""`、`[]`、`{}`）时写入；
- 否则保留先到的值。

因此，靠前章节抽取到的项目名称优先。

##### 6.6.2 列表

按“去重键”合并：

```text
title → name → factor → topic → order
```

取第一个非空的键值作为键；都没有时用 JSON 规范化字符串。

只追加未出现过的项。

##### 6.6.3 字典

递归合并：

- 列表 → 列表合并；
- 字典 → 递归；
- 标量 → 目标为空时写入。

##### 6.6.4 伪代码

```python
def merge_dict(base, extra):
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, list):
            base[key] = merge_list(base.get(key) or [], value)
        elif isinstance(value, dict):
            base[key] = merge_dict(base.get(key) or {}, value)
        elif base.get(key) in (None, "", [], {}):
            base[key] = value
    return base
```

#### 6.7 溯源核对（A13）

**目的**：报告哪些关键字段无法在原文中逐字找到，不自动删除结果。

##### 6.7.1 归一化

```text
去掉所有空白、竖线 |、全角竖线 ｜
```

原因：表格抽取结果常以“单元格 | 单元格”呈现，模型引用时通常不保留竖线。

##### 6.7.2 核对字段

- 项目名称；
- 招标编号；
- 预算金额文本；
- 投标截止时间；
- 开标地点；
- 每条资格要求的 `source_quote`；
- 每条资格门槛 `thresholds`；
- 每个评分因素的 `source_quote`。

##### 6.7.3 判定

```text
normalize(value) in normalize(full_text)
```

不满足则加入 `ungrounded_fields`。

##### 6.7.4 边界

- 模型改写标点、数字格式、同义表述 → 可能误报；
- 表格单元格顺序变化 → 可能误报；
- 只报告、不清空，最终由人工判断。

#### 6.8 投标格式兜底（`_ensure_bid_format_item`）

适用条件：

1. 当前章节路径包含“投标文件格式”；
2. 标题不以“第八章”开头；
3. 当前单元的所有结果都没有 `bid_format.items`。

处理：

```text
把当前章节标题补成唯一格式条目：
bid_format.items = [{title: 当前标题}]
```

原因：空白模板（如“法定代表人身份证明”）常被模型返回空对象；按标题兜底可避免格式清单缺项。

#### 6.9 截断二分重试（A14）

触发条件：

- `ExtractError` 文本包含“截断”；
- 当前文本长度 >= 1200；
- 递归深度 < 2。

处理：

```text
middle = len(text) // 2
upper = _extract_unit(text[:middle], depth + 1)
lower = _extract_unit(text[middle:], depth + 1)
返回 upper + lower
```

特点：

- 最多递归 2 层，即最多把原单元拆成 4 份；
- 标题加“（上）/（下）”；
- 返回两部分结果拼接；
- JSON 模式使用情况取逻辑与：任一部分退化，最终标记为未使用 JSON 模式。

边界：

- 可能在句子中间切开；
- 两半可能重复同一评分项，后续按去重键合并；
- 长度小于 1200 仍被截断 → 直接失败。

#### 6.10 单元缓存

##### 6.10.1 缓存位置

```text
upload/parsed/<doc_id>/extraction_parts/
├─ 001.json
├─ 002.json
├─ ...
└─ cache_tag.txt
```

##### 6.10.2 缓存标签

```text
cache_tag = f"{doc.parser}|{PROMPT_VERSION}"
```

- 解析器变化 → 正文来源变化；
- 提示词版本变化 → 抽取口径变化；
- 任一变化都会清空单元缓存。

##### 6.10.3 整篇缓存复用条件

同时满足：

1. `extraction.json` 存在；
2. 未强制重跑；
3. `extraction_meta.parser` 与当前解析器一致；
4. 缓存标签未过期；
5. 没有单元缓存，或所有单元缓存完整。

否则：

- 若有未完成单元缓存 → 续跑；
- 若解析器变化 → 重抽；
- 若标签过期 → 清空重抽。

##### 6.10.4 单元缓存复用

- 读取 `NNN.json`；
- 反序列化为 `ChapterExtraction` 列表；
- 应用格式兜底；
- 成功则跳过模型调用；
- 缓存损坏 → 重新抽取并覆盖。

##### 6.10.5 错误隔离

- 单个单元失败 → 记录警告，继续下一个；
- 所有单元失败 → 抛 `ExtractError`；
- 部分单元失败 → 用成功部分生成结果，警告写入 `extraction_meta.warnings`。

#### 6.11 最终结果

```text
TenderDocument(
  doc_id, doc_type,
  merged fields,
  extraction_meta: {
    model, prompt_version, parser,
    elapsed_seconds, calls, json_mode_used,
    ungrounded_fields, warnings
  }
)
```

`calls` 只统计实际模型调用，不算缓存命中。

---

### 7. 检索索引（A15–A17）

#### 7.1 章节感知分块（A15）

##### 7.1.1 目标

- 不以固定窗口粗暴切断条款；
- 尽量保持章节语义完整；
- 超长章节才在句末标点处切分；
- 无书签时仍能按页建立索引。

##### 7.1.2 参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `target` | 450 | 段落打包的目标长度 |
| `hard_max` | 700 | 单片段硬上限 |
| `overlap` | 80 | 硬切时的重叠字符数 |
| `MIN_FALLBACK_CHARS` | 10 | 无书签兜底时的最短片段 |

##### 7.1.3 章节路径

对每个 `Section`：

1. 去除 `<!-- page: N -->` 注释，记录页码；
2. HTML 表格扁平化；
3. 空文本或目录页 → 跳过；
4. 面包屑：

```text
【项目名】【祖先 > 当前章节】
正文片段
```

5. 若正文 `<= hard_max` → 单片段；
6. 否则按段落打包。

##### 7.1.4 段落打包

```python
paragraphs = split_by_blank_lines(text)
pieces, buffer = [], ""

for paragraph in paragraphs:
    if len(paragraph) > hard_max:
        flush(buffer)
        pieces.extend(hard_split(paragraph, hard_max, overlap))
        continue

    if buffer and len(buffer) + len(paragraph) > target:
        flush(buffer)

    buffer = buffer + "\n\n" + paragraph if buffer else paragraph

flush(buffer)
```

##### 7.1.5 句末硬切

```python
sentences = split_after("。；！？")
pieces, buffer = [], ""

for sentence in sentences:
    if len(buffer) + len(sentence) > hard_max and buffer:
        pieces.append(buffer)
        buffer = buffer[-overlap:] if overlap else ""

    buffer += sentence

    while len(buffer) > hard_max:
        pieces.append(buffer[:hard_max])
        buffer = buffer[hard_max - overlap:]

if buffer:
    pieces.append(buffer)
```

特点：

- 优先在句末标点后切；
- 超长句仍按固定长度硬切；
- 重叠用于保持上下文连续；
- 片段首尾空白会被清理，空片段丢弃。

##### 7.1.6 无书签按页兜底

当 `sections` 为空，或所有章节都被跳过时：

1. 按 `<!-- page: N -->` 拆分 `doc.markdown`；
2. 没有页码注释 → 整篇作为一页，页码 -1；
3. 每页：
   - HTML 表格扁平化；
   - 跳过空页；
   - 跳过 `< 10` 字符的页码/页脚；
   - 跳过目录页；
   - 按 `hard_max` / `target` 切分；
   - 再过滤 `< 10` 字符的尾部碎片；
4. 面包屑用“第 N 页”或“全文”；
5. 页码写入 `page_hint`。

这条兜底解决了“无书签 PDF 有正文但 `sections=[]` 导致 0 片段、入库失败”的问题。

##### 7.1.7 元数据

每个片段记录：

| 字段 | 说明 |
|---|---|
| `doc_id` | 文档 |
| `doc_type` | 文档类型 |
| `source_name` | 源文件名 |
| `source_sha256` | 源文件内容指纹 |
| `parser` | 解析器 |
| `chunk_index` | 片段序号 |
| `chunk_total` | 片段总数 |
| `section_no` | 章节编号 |
| `section_title` | 章节标题 |
| `section_path` | 祖先路径 |
| `page_hint` | 页码提示 |
| `char_count` | 正文字符数 |
| `content_sha256` | 正文内容指纹前 16 位 |
| `created_at` | 生成时间 |
| `schema_version` | 元数据版本 |

##### 7.1.8 复杂度

- 时间：`O(C)`，`C` 为文档字符数；
- 空间：`O(C)`；
- 重叠会增加少量重复字符。

#### 7.2 幂等入库（A16）

##### 7.2.1 目标

同一文档重复入库时不重复写、不重复计费，并清理过期片段。

##### 7.2.2 步骤

1. 计算分块；若为空 → 抛 `IngestError`；
2. 获取集合；
3. 查询该 `doc_id` 的已有片段：

```python
existing = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
```

4. 建立：

```text
existing_ids   = 已有片段 id 集合
existing_hashes = {id: content_sha256}
new_ids        = 新片段 id 集合
```

5. 计算变更：

```text
changed   = [c for c in chunks
             if existing_hashes.get(c.id) != c.content_sha256]
unchanged = len(chunks) - len(changed)
added     = len(new_ids - existing_ids)
updated   = len(changed) - added
```

6. 分批 upsert（默认 64 个/批）；
7. 删除 `existing_ids - new_ids` 的旧片段；
8. 返回统计：

```text
{collection, added, updated, deleted, unchanged, total}
```

其中 `total = collection.count()`，是该集合所有文档的片段总数，不只是当前文档。

##### 7.2.3 关键性质

- 片段 id 为 `doc_id:index:04d`；
- 内容未变 → 跳过嵌入；
- 内容变化但 id 不变 → `updated`；
- 片段数量变化 → 旧尾部片段删除；
- 中途崩溃 → 旧片段仍可查询，下次运行收敛；
- `force` 参数当前不改变入库行为，变更完全由内容指纹决定。

##### 7.2.4 复杂度

- 时间：`O(C + K)`，`K` 为片段数；
- 嵌入调用成本与 `changed` 数量成正比；
- 空间：`O(K)`。

#### 7.3 共享检索（A17）

##### 7.3.1 问题

多角色并行写作时，如果每个章节新建持久化客户端，会打开多个数据库连接并触发租户错误。

##### 7.3.2 方案

- 每轮编写创建一个检索器；
- 检索器内部持有唯一集合引用；
- 查询用互斥锁串行化；
- 查询可选 `where={"doc_id": doc_id}`；
- `top_k` 默认 6。

##### 7.3.3 伪代码

```python
class Retriever:
    def __init__(...):
        try:
            client = get_client(settings)
            self.collection = client.get_collection(...)
        except Exception:
            self.collection = None

    def search(query, limit):
        if self.collection is None:
            return []
        with self.lock:
            result = self.collection.query(
                query_texts=[query],
                n_results=limit,
                where={"doc_id": doc_id} if doc_id else None,
            )
        return to_chunks(result)
```

##### 7.3.4 失效模式

- 集合不存在 → 返回空列表；
- 嵌入服务不可用 → 初始化失败 → 返回空列表；
- 检索失败不阻断写作，只降低内容针对性；
- 没有重排、没有距离阈值过滤。

---

### 8. 投标文件编写（A18–A21）

#### 8.1 大纲生成（A18）

##### 8.1.1 LLM 策划

Planner 输入：

- 评分办法与投标文件格式摘要；
- 投标人资料概况；
- 目标字数；
- 章节数量上限（默认提示不超过 25，API 可限制 0–50）。

输出 JSON：

```json
{
  "sections": [
    {
      "id": "section_1",
      "title": "施工组织设计 - 内容完整性",
      "order": 1,
      "strategy": "generate",
      "target_words": 1200,
      "scoring_factors": ["内容完整性"],
      "requirements": "…",
      "retrieval_query": "…"
    }
  ]
}
```

单条非法 → 跳过该条；整体调用异常 → 记录事件并使用确定性大纲。

##### 8.1.2 确定性兜底大纲

顺序：

1. 若投标格式条目中含“施工组织设计”：
   - 添加“施工组织设计”总章节；
   - 关联名称含“施工组织”的评分因素；
2. 若存在“施工组织设计”格式条目，添加技术类评分因素子章节：
   - 类别为“技术”；
   - 有分值；
   - 名称不含“施工组织设计”（避免与总章节重复）；
3. 遍历投标格式条目：
   - 跳过“施工组织设计”；
   - 按标题选择策略；
4. 若存在名称含“报价”的评分因素 → 添加“投标报价”占位章节；
5. 若存在类别为“其他”且有分值的评分因素 → 添加“其他评分因素”；
6. 按规范化标题去重；
7. 排序、编号、限制章节数。

##### 8.1.3 章节策略

| 标题关键词 | 策略 |
|---|---|
| 施工组织设计、项目管理机构、其他评分、其他资料 | `generate` |
| 投标函、授权委托书、身份证明、承诺、声明 | `llm_fill`（遗留名 `fill_template`，读取时归一化） |
| 其他 | `placeholder` |

##### 8.1.4 大纲归一化

1. 规范化标题去重（保留第一条）；
2. 若为空 → 兜底大纲；
3. **格式覆盖**：
   - 对每个投标格式条目，检查是否存在标题互相包含的章节；
   - 不存在 → 追加占位章节，`order = 10^6`；
4. **评分因素映射**：
   - 若评分因素名已在某章节 `scoring_factors` 中 → 跳过；
   - 若规范化名称出现在某章节标题中 → 追加到该章节；
   - 否则，仅对**有分值**的评分因素按类别选择目标章节：
     - 技术 → 施工组织设计/技术
     - 商务 → 项目管理机构/商务
     - 报价 → 投标报价/报价
     - 其他 → 其他评分/其他资料/其他
   - 找不到目标 → 不强行归属，报告标记未覆盖；
5. **排序与截断**：
   - 按 `order` 排序；
   - 若 `max_sections > 0` 且超出：
     - 优先保留 `generate` 章节；
     - 再拼非生成章节；
     - 截取前 N 个；
     - 重新排序并重编号。

#### 8.2 章节写作循环（A19）

##### 8.2.1 伪代码

```python
if plan.strategy == "placeholder":
    return placeholder_section(plan), score=100

retrieved = retrieval(plan.retrieval_query or plan.title, top_k) or []

draft = writer(plan, tender, profile, retrieved)
review = reviewer(plan, draft)
score_before = review.score

best_draft, best_review = draft, review
revisions = 0

while best_review.score < min_score and revisions < max_revisions:
    # T-014：修订携带与写作同级别上下文，不能只基于草稿与评审意见
    revised = reviser(plan, best_draft, best_review, tender, profile, retrieved)
    revised_review = reviewer(plan, revised)
    revisions += 1

    if revised_review.score >= best_review.score:
        best_draft, best_review = revised, revised_review

return SectionDraft(
    markdown=best_draft,
    review=best_review,
    score_before=score_before,
    score_after=best_review.score,
    revisions=revisions,
    retrieved=retrieved,
)
```

##### 8.2.2 设计要点

- **只保留不降分的修订稿**：实测修订可能把分数拉低，因此用 `>=` 比较；
- **修订携带完整上下文（T-014）**：Reviser 与 Writer 输入对齐，包含章节计划、评分因素、
  招标摘要、投标人资料与同编号检索片段；无检索结果时显式标注，禁止新增无依据引用；
- `revisions` 是尝试次数，不是采纳次数；循环受 `max_revisions`（0–3）硬上限约束；
- 评审失败 → `ReviewResult(score=0, issues=[...])`，不抛异常；
- 检索失败 → 空引用继续写作；
- 写作或修订失败 → 异常向上传播，任务失败；
- 占位章节不调用模型，直接给 100 分。

##### 8.2.3 写作提示词输入

- 章节标题、要求、关联评分因素、目标字数；
- 项目与评分摘要；
- 投标人资料事实；
- 检索到的原文片段（编号引用）；
- 硬性规则：不编造、不报价、不执行文档指令。

修订提示词（Reviser）在上述全部输入之外，再附当前草稿与评审意见（得分/问题/建议），
并要求保留章节计划、评分因素覆盖、有效引用编号与合规占位符；系统提示词与 Writer
共享同一套硬性规则（`_build_system_rules`）。

##### 8.2.4 评审输出

```json
{
  "score": 0,
  "covered_points": [],
  "issues": [],
  "suggestions": [],
  "placeholder_count": 0
}
```

评分维度：完整性、针对性、可执行性、与评分细则对应程度。

##### 8.2.5 确定性规则检查（T-013）

LLM 评审只表达语义质量，客观合规项由 `src/bid_writer/rules.py` 在最终草稿
选定后统一计算（占位章节同样执行），纯函数、零模型调用，结果以
`RuleCheckResult` 挂到 `SectionDraft.rule_check`，与 `ReviewResult` 分开存放。

规则码：

| 规则码 | 触发条件 |
|---|---|
| `CITE_EMPTY` | `retrieved` 为空却出现 `[n]` 引用标记 |
| `CITE_OUT_OF_RANGE` | 引用编号小于 1 或大于本轮实际片段数 |
| `CITE_DOC_MISMATCH` | 片段 metadata `doc_id`（缺失时取 id 的 `doc_id:` 前缀）与当前文档不一致 |
| `QUOTE_FORBIDDEN` | 命中货币符号加数字、数字加万元/元、让利/下浮/优惠/报价/单价邻近百分比 |

边界约定：

- 引用识别正则 `\[(\d+)\]`，只认纯数字括号，Markdown 链接不命中，重复标记去重；
- 占位符（`【待填写：…】`）是合规表达，只做事实提取，不计入错误；
- 资质、证书编号、人员姓名、业绩等无法可靠规则化的禁令不在本期范围，仍由
  LLM 评审与人工核对承担；报价检测取保守策略，技术误报交人工复核；
- 规则结果不参与修订循环的分数比较，不改变写作/修订行为；`build_report`
  以报告时重算为准，旧产物还原的草稿无缓存结果时按磁盘正文补算。

#### 8.3 汇总与覆盖报告（A20）

##### 8.3.1 Markdown 汇总

结构：

```markdown
# 项目名称

**投标人：公司名称**
**日期：YYYY年MM月DD日**

---

## 目录
1. 章节一
2. 章节二

## 章节一
正文

## 章节二
正文
```

- 公司名称为空 → `【待填写：公司名称】`；
- 章节按 `order` 排序；
- 章节正文去除首尾空白。

##### 8.3.2 章节统计

对每个草稿：

- 提取占位符：`【待填写[^】]*】`；
- 统计字数：去除空白后的字符数；
- 记录策略、修订前后得分、修订次数、关联评分因素、检索片段数。

##### 8.3.3 格式覆盖

对每个投标格式条目：

```text
normalize_title(item.title)
```

与每个章节的规范化标题比较：

```text
item_key in plan_key or plan_key in item_key
```

命中 → 记录 `section_id`、`section_title`、`strategy`；未命中 → `missing`。

##### 8.3.4 三层覆盖（T-012）

覆盖计算由 `src/bid_writer/coverage.py` 的纯函数完成，零模型调用。对每个评分因素
依次判定三个层级，构成严格漏斗 `evidence ⇒ body ⇒ plan`：

1. **计划覆盖 `plan_covered`**：因素名精确出现在某章节计划的 `scoring_factors`
   中（只精确匹配，不做模糊匹配），命中章节记入 `section_ids`；兼容字段 `covered`
   等同本层，未覆盖且分值非空仍生成警告。
2. **正文覆盖 `body_covered`**：在计划覆盖前提下，至少一个候选章节满足
   `strategy != placeholder`，且把正文占位符 `【待填写…】` 剔除后，去空白的因素名
   是去空白正文的子串（`normalize_title`）；命中章节记入 `body_section_ids`。
   这是保守的关键词证据，语义充分性仍由 LLM Reviewer 评分承担。
3. **证据覆盖 `evidence_covered`**：在正文覆盖前提下，至少一个正文命中章节同时
   具备同 `doc_id` 的检索片段（metadata 优先、id 前缀兜底）与合法范围内的引用
   标记 `[n]`（`1 <= n <= len(retrieved)`，复用 8.2.5 的规则工具）；命中章节记入
   `evidence_section_ids`。只检索未引用属于「有素材未使用」，不计证据覆盖。

每个因素还输出 `gaps`：未达计划为 `["plan","body","evidence"]`，计划达标但正文
未响应为 `["body","evidence"]`，正文达标但缺证据为 `["evidence"]`，全通过为 `[]`。

##### 8.3.5 报告字段

```text
BidReport:
  doc_id, project_name, bidder, generated_at,
  total_sections, total_words,
  sections[], format_coverage[], evaluation_coverage[],
  placeholders[], missing_profile_fields[], warnings[],
  rule_checks[], rule_error_count, coverage_summary
```

`evaluation_coverage[*]` 在原 `name/score/category/covered/section_ids` 上扩展
`plan_covered/body_covered/evidence_covered/body_section_ids/evidence_section_ids/gaps`；
`coverage_summary` 为三层各自的 `{total, covered, gaps}` 漏斗汇总。旧 `report.json`
缺少新字段时按 dataclass 默认值加载，`rebuild_artifact` 重算后补齐。

##### 8.3.6 摘要统计

`bid_summary` 基于报告计算：

- 格式覆盖数：`section_id` 为真；
- 计划层评分覆盖数：`plan_covered` 为真（即旧 `evaluation_factors_covered`，语义不变）；
- 正文/证据层覆盖数：`body_factors_covered`、`evidence_factors_covered`（T-012 新增）；
- 未覆盖评分因素：计划层未覆盖且 `score != null`；
- 占位符数量：去重后的占位符数；
- Word 路径：仅当文件存在时返回。

#### 8.4 占位符与缺失资料

##### 8.4.1 占位符

正则：

```text
【待填写[^】]*】
```

特点：

- 匹配任意“待填写”内容；
- 去重后排序；
- 统计的是**不同占位符**，不是出现次数。

##### 8.4.2 缺失字段

检查 11 个字段：

```text
公司名称、法定代表人、注册资本、成立日期、注册地址、
企业资质、认证证书、类似项目业绩、项目管理人员、
财务数据、联系方式
```

判定：

```text
getattr(profile, field) in (None, "", [], {})
```

`notes` 不参与缺失判定。

##### 8.4.3 提示词约束

- 资料缺失 → 正文必须写 `【待填写：字段名】`；
- 不得编造；
- 报价决策内容只生成占位。

##### 8.4.4 前端归一化

前端提交前：

- 可选文本空串 → `null`；
- 资质/证书/备注按行拆分并去空；
- 联系方式四项全空 → `null`；
- 高级 JSON 只允许四个字段；
- JSON 语法/类型错误 → 禁止提交；
- 非法 JSON 不覆盖上一次有效结构。

#### 8.5 Markdown → Word（A21）

##### 8.5.1 样式

- 正文：宋体 11pt；
- 标题：黑体；
- 表格：Table Grid。

##### 8.5.2 行级解析

按行扫描：

| 行模式 | 处理 |
|---|---|
| 空行 | 跳过 |
| ` ``` ` 代码围栏 | 围栏内内容作为普通段落 |
| `#` 开头 | 标题，层级最多 4 |
| `|` 开头 | 连续表格块 |
| `-` / `*` 开头 | 项目符号列表 |
| `数字.` / `数字、` 开头 | 编号列表 |
| `---` | 分页符 |
| 其他 | 普通段落 |

##### 8.5.3 表格解析

- 去掉首尾 `|`；
- 按 `|` 拆分单元格；
- 若第二行是分隔行（`---`、`:`、`|`、空格组成）→ 删除该行；
- 列数取所有行的最大值；
- 缺失单元格补空。

##### 8.5.4 粗体

正则：

```text
\*\*(.+?)\*\*
```

按匹配位置拆分为普通 run 和粗体 run。

##### 8.5.5 局限

- 不处理斜体、链接、图片、嵌套列表、脚注；
- 代码围栏仅按普通段落输出；
- 表格不支持合并单元格；
- `---` 一律视为分页符。

---

### 9. 任务状态与进度（A22）

#### 9.1 状态映射

| 队列状态 | API 状态 |
|---|---|
| `PENDING` / `RECEIVED` | `pending` |
| `STARTED` / `PROGRESS` / `RETRY` | `running` |
| `SUCCESS` | `succeeded` |
| `FAILURE` / `REVOKED` | `failed` |
| 其他 | `running` |

#### 9.2 进度节流

`_ProgressReporter`：

- 每次事件追加消息；
- 距上次上报不足 0.5 秒 → 不写队列；
- 上报时只带最近 50 条；
- 任务结束时把最近 50 条写入结果；
- 进度上报失败不影响主任务。

伪代码：

```python
def report(message):
    messages.append(message)
    if now - last_update < 0.5:
        return
    last_update = now
    try:
        task.update_state(
            state="PROGRESS",
            meta={"progress": messages[-50:], "started_at": started_at},
        )
    except Exception:
        pass
```

#### 9.3 排队判断

- 队列模式：队列长度；
- 本地模式：`pending + running` 数量；
- 达到 `API_MAX_PENDING_JOBS` → 429。

#### 9.4 任务保留

- 队列结果与任务标记默认 24 小时；
- 过期查询 → 404；
- 磁盘产物不受影响。

---

### 10. 参数与复杂度

#### 10.1 参数速查

| 参数 | 默认 | 所属算法 |
|---|---|---|
| 云端单文件最大页数 | 200 | 分片 |
| 云端轮询间隔 | 5 秒 | 云端解析 |
| 云端最大轮询次数 | 120 | 云端解析 |
| 云端重试次数 | 2 | 云端解析 |
| 云端退避系数 | 2.0 | 云端解析 |
| ZIP 解压上限 | `max(500MB, zip_size × 20)` | 解压安全 |
| 抽取单元长度 | 12000 字符 | 抽取 |
| 截断二分最小长度 | 1200 字符 | 抽取 |
| 截断二分深度 | 2 | 抽取 |
| 分块目标长度 | 450 | 分块 |
| 分块硬上限 | 700 | 分块 |
| 分块重叠 | 80 | 分块 |
| 无书签最短片段 | 10 字符 | 分块 |
| 目录页比例阈值 | 0.3 | 目录识别 |
| 目录页最少引导行 | 2 | 目录识别 |
| 检索条数 | 6 | 检索 |
| 默认评审阈值 | 75 | 编写 |
| 默认修订次数 | 1 | 编写 |
| 章节并发 | 3 | 编写 |
| 每章目标字数 | 1200 | 编写 |
| 默认章节上限（提示词） | 25 | 策划 |
| 接口章节上限 | 50 | 策划 |
| 进度节流 | 0.5 秒 | 任务 |
| 进度保留 | 50 条 | 任务 |

#### 10.2 复杂度

| 算法 | 时间 | 空间 | 说明 |
|---|---|---|---|
| 书签读取 | `O(L^2)` | `O(L)` | `L` 为书签数 |
| 页面归属 | `O(P × L)` | `O(P)` | `P` 为页数 |
| 章节组装 | `O(C)` | `O(C)` | `C` 为字符数 |
| 分片规划 | `O(P/M)` | `O(P/M)` | 分片数 |
| content_list 合并 | `O(C)` | `O(C)` | 逐块处理 |
| 抽取单元切分 | `O(C)` | `O(C)` | 按段落扫描 |
| 结果合并 | `O(U × I)` | `O(I)` | `U` 单元数，`I` 条目数 |
| 分块 | `O(C)` | `O(C)` | 逐字符处理 |
| 幂等入库 | `O(K)` + 嵌入成本 | `O(K)` | `K` 片段数；嵌入成本与变更数成正比 |
| 检索 | 近似 `O(log K)` | `O(top_k)` | 依赖索引实现 |
| 章节循环 | `O(S × (1 + R) × T)` | `O(S)` | `S` 章节，`R` 修订次数，`T` 单次模型时延 |
| 覆盖报告 | `O(S × F + S × B)` | `O(F + B)` | `F` 评分因素，`B` 格式条目 |
| Word 导出 | `O(L)` | `O(L)` | `L` 行数 |

---

### 11. 边界与失效模式

#### 11.1 文档结构

| 场景 | 行为 | 风险 |
|---|---|---|
| 无书签 | 章节为空，正文保留 | 抽取/检索只能按页/全文 |
| 书签页码错误 | 页面归属错误 | 章节正文错位 |
| 书签层级跳跃 | 按数值层级处理 | 子节点判断可能异常 |
| 目录页漏判 | 目录进入索引 | 检索噪声增加 |
| 目录页误判 | 正文页被跳过 | 少量正文丢失 |
| 扫描件无文字 | 无法解析正文 | 入库失败，需要 OCR |

#### 11.2 分片与合并

| 场景 | 行为 | 风险 |
|---|---|---|
| 分片边界跨章节 | 页码偏移还原 | 章节可能被分片切开，但合并后恢复 |
| content_list 缺失 | 降级 Markdown 拼接 | 章节结构丢失 |
| 上传链接数量不匹配 | 直接报错 | 任务失败 |
| 轮询超时 | 报错并保留 batch_id | 可据此续查 |
| ZIP 路径穿越 | 拒绝解压 | 安全 |
| ZIP 解压炸弹 | 超过体积上限拒绝 | 安全 |

#### 11.3 抽取

| 场景 | 行为 | 风险 |
|---|---|---|
| 模型返回坏 JSON | JSON 模式退化/重试 | 可能最终失败 |
| 输出截断 | 二分重试 | 可能重复或切断句子 |
| 单章失败 | 警告并继续 | 该章信息缺失 |
| 所有章失败 | 抛错 | 任务失败 |
| 模型改写原文 | 溯源核对报告 | 可能误报 |
| 类别不规范 | 归一化到“其他” | 评分因素分类不精确 |
| 空白模板 | 格式标题兜底 | 避免格式条目缺失 |

#### 11.4 检索

| 场景 | 行为 | 风险 |
|---|---|---|
| 索引未建立 | 返回空列表 | 写作缺少依据 |
| 嵌入服务不可用 | 初始化失败，返回空 | 同上 |
| Chroma 并发 | 锁串行化 | 查询吞吐下降 |
| 片段太短 | 无书签兜底过滤 | 可能丢失短页正文 |
| 目录页误判 | 跳过整页 | 少量正文丢失 |

#### 11.5 编写

| 场景 | 行为 | 风险 |
|---|---|---|
| 策划失败 | 确定性大纲 | 章节可能不如模型规划精细 |
| 评审失败 | 0 分并记录问题 | 可能触发无意义修订 |
| 修订降分 | 保留旧稿 | 不会变差，但可能浪费调用 |
| 检索失败 | 无引用写作 | 针对性下降 |
| 格式条目缺失 | 自动补占位章节 | 覆盖报告可能虚高 |
| 评分因素无法归属 | 报告未覆盖 | 不强行错误归因 |
| Word 导出失败 | 记录警告，继续 | 无 Word 文件 |
| 资料为空 | 大量占位符 | 需人工补全 |

#### 11.6 任务

| 场景 | 行为 | 风险 |
|---|---|---|
| 执行器未启动 | 任务保持 pending | 用户需检查 worker |
| 队列满 | 429 | 需等待 |
| 结果过期 | 404 | 无法查询，但产物仍在 |
| 进度上报失败 | 忽略 | 前端进度可能不完整 |
| 任务执行器崩溃 | `acks_late` 可重投 | 任务需幂等 |

---

### 12. 算法与代码映射

| 算法 | 代码入口 | 关键常量/配置 |
|---|---|---|
| A1 书签读取 | `src/parsing/toc.py::read_toc` | — |
| A2 页面归属 | `src/parsing/toc.py::assign_pages_to_entries` | — |
| A3 章节组装 | `src/parsing/toc.py::compose_sections` | — |
| A4 目录页识别 | `src/parsing/toc.py::looks_like_toc_page` | 3 点、比例 0.3、至少 2 行 |
| A5 PDF 分片 | `src/parsing/splitter.py::plan_splits` | `MINERU_MAX_PAGES=200` |
| A6 云端合并 | `src/parsing/builder.py::merge_mineru` | — |
| A7 本地清洗 | `src/parsing/local_parser.py` | 页脚/页码正则 |
| A8 表格扁平化 | `src/textutils.py::html_tables_to_text` | — |
| A9 抽取单元 | `src/extraction/extractor.py::build_units` | `MAX_CHARS_PER_CALL=12000` |
| A10 重点路由 | `src/extraction/prompts.py::focus_for_chapter` | 关键词优先级 |
| A11 宽容归一化 | `src/extraction/schema.py` | 真/假值集合、类别集合 |
| A12 结果合并 | `src/extraction/extractor.py::merge_partials` | 去重键顺序 |
| A13 溯源核对 | `src/extraction/extractor.py::ground_check` | 归一化规则 |
| A14 截断二分 | `src/extraction/extractor.py::_extract_unit` | 1200 字符、深度 2 |
| A15 分块 | `src/vectorstore/chunking.py::chunk_document` | 450/700/80/10 |
| A16 幂等入库 | `src/vectorstore/ingest.py::ingest_document` | 批大小 64 |
| A17 共享检索 | `src/vectorstore/retrieval.py` | `top_k=6` |
| A18 大纲 | `src/bid_writer/agents.py` | 25 章节提示上限 |
| A19 章节循环 | `src/bid_writer/agents.py::section_agent` | 75 分、1 次修订 |
| A20 覆盖报告 | `src/bid_writer/assembly.py::build_report` | 标题模糊匹配 |
| A21 Word 导出 | `src/bid_writer/docx_export.py` | 宋体/黑体 |
| A22 任务进度 | `src/tasks.py`、`api/dispatch.py` | 0.5 秒、50 条 |
| A23 文档类型 | `src/parsing/builder.py::detect_doc_type` | 2 个特征词 |
| A24 章节结构 | `src/parsing/toc.py` | 编号/祖先/子树 |

---

### 13. 验证与测试

#### 13.1 算法级测试

| 测试文件 | 覆盖算法 |
|---|---|
| `tests/test_toc.py` | A1、A2、A3、A4 |
| `tests/test_splitter.py` | A5 PDF 均衡分片 |
| `tests/test_builder.py` | A6 云端结果合并 |
| `tests/test_local_parser.py` | A7、A8 本地页面清洗与表格扁平化 |
| `tests/test_extraction.py` | A9–A14 |
| `tests/test_vectorstore.py` | A15–A17 |
| `tests/test_bid_writer.py` | A18–A20 |
| `tests/test_bid_writer_export.py` | A21 |
| `tests/test_api.py` / `test_e2e.py` | A22 与任务接口（测 dispatch 抽象，离线不依赖真实 Redis） |

#### 13.2 关键回归

1. **无书签 PDF 分块回归**  
   72 页、无书签、有正文的招标文件：
   - 修复前：`sections=[]` → 0 片段 → `IngestError`；
   - 修复后：按页兜底 → 124 片段 → 入库成功。

2. **目录页识别回归**  
   `第一章 投标邀请....2` 这类 4 点引导行现在能被识别；
   单行带点正文不会被误判。

3. **修订不降分**  
   修订稿只有得分不低于当前最佳时才被采纳。

4. **格式覆盖兜底**  
   模型漏掉空白模板时，按章节标题补格式条目。

5. **缓存失效**  
   解析器或提示词版本变化时，旧单元缓存整体作废。

#### 13.3 手工验证建议

- 有书签 PDF：检查章节数、页码归属、章节正文；
- 无书签 PDF：检查章节数为 0、分块按页、目录页被跳过；
- 超长章节：检查片段长度与重叠；
- 评分表章节：检查评分因素、分值、`source_quote`；
- 投标编写：检查策划章节、修订次数、覆盖报告、占位符；
- Word：检查标题、列表、表格、粗体、分页符。

---

### 14. 设计取舍

#### 14.1 为什么以书签为章节骨架

- 目录页会污染正文标题识别；
- 字体元数据可能损坏；
- 书签是 PDF 中最稳定的层级来源；
- 无书签时才退化为按页/全文处理。

#### 14.2 为什么按章抽取而不是整篇

- 上下文与输出预算有限；
- 单章失败不影响整篇；
- 单元缓存支持中断续跑；
- 合并阶段可以按字段去重与补全。

#### 14.3 为什么分块以章节为基本单位

- 法条、评分细则常以条款为语义单元；
- 固定窗口会拦腰截断条款；
- 只有超长章节才硬切，尽量保持语义完整。

#### 14.4 为什么只保留不降分的修订

- 模型修订可能引入新问题；
- 评审分数存在波动；
- 保留最佳版本比盲目采纳最后一次更稳。

#### 14.5 为什么覆盖匹配使用标题包含

- 模型生成的章节标题与招标格式条目通常只有编号/措辞差异；
- 规范化后互相包含是较稳的匹配；
- 代价是可能误匹配，因此报告保留 `section_id` 与策略供人工核对。

---

### 15. 已知局限

1. 无书签 PDF 无法恢复章节层级，只能按页检索；
2. 纯图片/扫描件没有可识别正文时仍无法入库；
3. 本地解析对表格、图纸的版面还原能力有限；
4. 云端解析的表格可能列优先或丢结构；
5. 溯源核对是精确子串匹配，无法判断语义等价；
6. 评分因素覆盖只按名称精确匹配，可能漏掉同义表述；
7. 章节标题匹配使用包含关系，可能误判；
8. 修订循环不保证分数单调上升，只保证不采纳降分稿；
9. 检索没有重排与距离阈值，低相关片段也可能进入提示词；
10. 队列模式下任务进度只保留最近 50 条，长任务早期日志会丢失（本地后端最多保留 200 条）；
11. `force` 参数当前不改变入库行为；
12. Word 导出只支持有限的 Markdown 子集。

---

### 16. 附录：关键常量汇总

```text
# 分片
MINERU_MAX_PAGES = 200

# 云端
POLL_INTERVAL = 5
MAX_POLLS = 120
MAX_RETRIES = 2
RETRY_BACKOFF = 2.0
ZIP_SIZE_LIMIT = max(500MB, zip_size * 20)

# 抽取
MAX_CHARS_PER_CALL = 12000
BISECT_MIN_CHARS = 1200
BISECT_MAX_DEPTH = 2

# 分块
CHUNK_TARGET = 450
CHUNK_HARD_MAX = 700
CHUNK_OVERLAP = 80
MIN_FALLBACK_CHARS = 10

# 目录识别
TOC_LEADER_MIN = 3
TOC_MIN_LINES = 2
TOC_RATIO = 0.3

# 编写
DEFAULT_MIN_SCORE = 75
DEFAULT_MAX_REVISIONS = 1
DEFAULT_TOP_K = 6
DEFAULT_CONCURRENCY = 3
DEFAULT_TARGET_WORDS = 1200
PLANNER_MAX_SECTIONS = 25
API_MAX_SECTIONS = 50

# 任务
PROGRESS_INTERVAL = 0.5
PROGRESS_LIMIT = 50
JOB_TTL = 86400
```

---

*文档结束。*

---

## 第二部分　投标编写代码精读（原《代码精读-投标编写》）

> 范围：[`src/bid_writer/`](../src/bid_writer/)、[`src/pipeline.py`](../src/pipeline.py) 的 `run_bid_writer` 与 [`src/vectorstore/retrieval.py`](../src/vectorstore/retrieval.py)
> 配套：[`code-guide.md`](code-guide.md) 第三层、本文件第一部分 A17–A21、[`api-contract.md`](api-contract.md) §7–8。
> 本笔记只做「逐阶段导读 + 设计要点」，不复制大段源码。行号为当前 main 快照（2026-11-06），代码演进后可能漂移，定位以符号名为准。

### 一、端到端链路速览

一次投标编写 = 6 个阶段，全部由 [`run_bid_writer`](../src/pipeline.py)（`src/pipeline.py`，约 L305 起）串起来：

```
① 规划大纲   plan_sections()          agents.py
② 准备检索   ChromaRetriever()        vectorstore/retrieval.py
③ 并行写作   run_write_pipeline()     graph.py
④ 单章循环   section_agent()          agents.py   （写→评→改，可能多轮）
⑤ 汇总报告   assemble_markdown()       assembly.py
              build_report()
⑥ 导出 Word  markdown_to_docx()       docx_export.py
```

数据形态随阶段演进：`TenderDocument`（抽取）→ `list[SectionPlan]`（大纲）→ `list[SectionDraft]`（草稿）→ `BidReport`（报告）。

### 二、入口：`run_bid_writer`（总装）

位置：[`src/pipeline.py`](../src/pipeline.py) 的 `run_bid_writer`（约 L305–L421）。

按阶段读：

| 阶段 | 关键调用 | 要点 |
|---|---|---|
| 组装 | `BidderProfile.from_dict`、`WriteOptions.from_dict`、`load_extraction` | 资料/参数容忍缺省；读取 `extraction.json` |
| ① 规划大纲 | `plan_sections` | 传入 `target_words`、`max_sections` |
| ②③ 检索与扇出 | `ChromaRetriever`、`run_write_pipeline` | `collection_name(tender.doc_type)` 决定集合名；`doc_id` 过滤 |
| ⑤⑥ 汇总落盘 | `assemble_markdown`、`build_report`、`markdown_to_docx` | Word 导出失败只进 `warnings`，不阻断 Markdown 与报告 |

**记住**：入口只负责「组装 + 编排 + 落盘」，具体智能逻辑都在 [`agents.py`](../src/bid_writer/agents.py) / [`graph.py`](../src/bid_writer/graph.py)。产物目录是 `upload/bids/<doc_id>-<时间戳>/`。

### 三、阶段① 大纲规划：`plan_sections`

位置：[`agents.py`](../src/bid_writer/agents.py) 的 `plan_sections`（L44）。

三层结构：

```
plan_sections (L44)
  ├─ _planner_llm()       L66   LLM 策划（失败返回空）
  ├─ _fallback_outline()  L96   确定性兜底（无模型）
  └─ _normalize_outline() L191  归一化（兜底合流）
```

#### 3.1 三个子函数各做什么

| 函数 | 位置 | 职责 | 失败策略 |
|---|---|---|---|
| `_planner_llm` | L66 | 用 `build_planner_prompt` 求 JSON，逐条转 `SectionPlan` | 单条非法 `continue`，整体异常返回 `[]` |
| `_fallback_outline` | L96 | 从 `bid_format_items` + `evaluation_factors` 确定性拼大纲 | 纯规则，不调模型 |
| `_normalize_outline` | L191 | 去重 + 补格式 + 映射评分 + 排序截断 | 兜底与 LLM 结果共用同一出口 |

#### 3.2 归一化四步（对照第一部分 8.1.4）

| 步骤 | 位置 | 要点 |
|---|---|---|
| 去重 | `_dedup_by_title` L213 | 标题去空白后查重，保留第一条 |
| 补格式 | `_normalize_outline` L191 | 缺失的格式条目追加 `placeholder` 章节，`order=10**6` 排到最后 |
| 映射评分 | `_map_scoring_factors` L235 | 三套匹配：已在 factors → 标题包含 → 分类关键词 |
| 排序截断 | `_order_and_limit` L303 | **优先保留 `generate`**，再按 order 重排，`_renumber`（L315）重编号 |

> `_title_covered`（L227）用「标题互相包含」判断覆盖，与 `_map_scoring_factors`（L235）内的 `normalize_title(name) in normalize_title(plan.title)` 是两处不同的模糊匹配：前者判断「格式条目不重复」，后者判断「评分因素归属哪个章节」。

### 四、阶段② 检索准备：`ChromaRetriever`

位置：[`retrieval.py`](../src/vectorstore/retrieval.py) 的 `ChromaRetriever`（L16）。

| 符号 | 位置 | 要点 |
|---|---|---|
| `__init__` | L19 | 共享查询器，`get_collection` 失败时 `self.collection=None` 且 `degraded=True` 降级；`doc_id` 作为 `where` 过滤 |
| `search` | L37 | 用互斥锁 `self._lock` 串行化 `query`；查询异常时置 `degraded=True` 并返回空列表，不向上抛 |
| `_to_chunks` | L50 | 检索结果归一化为片段字典 |

**核心约束**（A17）：多角色并行写作时若各自 `new PersistentClient` 会触发 Chroma 租户错误，所以一轮编写只建**一个**检索器，查询用锁串行。这是「并行写作、串行检索」的关键，对应 [`graph.py`](../src/bid_writer/graph.py) 中检索在扇出前完成。

**降级显式标记**（T-017，AGENTS 5.3）：检索器持有 `degraded` 布尔标志，初始化失败或任一查询异常都会置位；`run_bid_writer` / 补写 / 重写链路把标志透传进 `BidReport.retrieval_degraded` 与 `summary`，前端结果页据此显示红色提示，避免把无原文依据的内容误判为可信生成。人工编辑重建报告时该标志从旧 `report.json` 粘性保留。

### 五、阶段③ 并行写作：`run_write_pipeline`

位置：[`graph.py`](../src/bid_writer/graph.py) 的 `run_write_pipeline`（L23，全文 65 行）。

- 检索在扇出前串行完成：占位章节直接给空片段，其余章节统一调 `retrieve(query, top_k)`；
- `ThreadPoolExecutor(max_workers=settings.bid_writer_max_concurrency)` 按章节扇出 `section_agent`；
- `as_completed` 收集后按 `plan.order` 排序返回；
- 没有状态图与运行时自主决策，Planner/Writer/Reviewer/Reviser 是职责固定的函数节点。

### 六、阶段④ 单章循环：`section_agent`

位置：[`agents.py`](../src/bid_writer/agents.py) 的 `section_agent`（L326–L366）。

```
占位章节 → placeholder_section() L367，直接返回【待填写：标题】，不调模型
普通章节 → _writer() L372 → _reviewer() L377
           while 分数 < min_score 且 revisions < max_revisions:
               _reviser() L390 → 再评审
               仅当新分 >= 旧分才采纳
```

| 关键函数 | 位置 | 失败策略 |
|---|---|---|
| `_writer` | L372 | 经 `_generate_text`（L404），空内容抛 `BidWriteError`（整章失败） |
| `_reviewer` | L377 | `_generate_json`（L412）异常/解析失败降级为 `score=0`（不抛） |
| `_reviser` | L390 | 同 `_writer`，空内容抛 `BidWriteError` |

**设计要点**：

- 「修订不降分才采纳」避免越改越差；
- 评审给 0 分不阻断，占位/低质量草稿仍会被汇总进报告并标 `warning`。

### 七、提示词：`build_*_prompt`

位置：[`prompts.py`](../src/bid_writer/prompts.py)。

| 构造器 | 位置 | 输出格式 | 硬约束 |
|---|---|---|---|
| `build_planner_prompt` | L187 | JSON（sections） | 覆盖评分维度 + 逐条格式 |
| `build_writer_prompt` | L230 | Markdown 正文 | 不编造、缺失写占位符、不报价 |
| `build_reviewer_prompt` | L253 | JSON（score/points/issues/suggestions） | 0-100，四维度 |
| `build_reviser_prompt` | L266 | Markdown 正文 | 与 writer 一致 |

**贯穿全链路的硬约束**集中在 `_build_system_rules`（L17）：**不编造、缺失写 `【待填写：xxx】`、报价只给占位**。这是产品「草稿 + 人工复核」定位的代码落点。

辅助：`_profile_text`（L145）把 `BidderProfile` 转成提示词可读文本，`_tender_summary`（L216）抽取项目摘要。

### 八、阶段⑤ 汇总与覆盖报告：`assembly.py`

位置：[`assembly.py`](../src/bid_writer/assembly.py)。

| 函数 | 位置 | 职责 |
|---|---|---|
| `assemble_markdown` | L34 | 合并标题 + 目录 + 各章正文成 `bid.md` |
| `build_report` | L57 | 产 `BidReport`，输出 `report.json` |

覆盖报告四类计算（对照第一部分 8.3.2–8.4.2）：

| 覆盖类型 | 实现位置 | 规则 |
|---|---|---|
| 章节统计 | `_char_count`（L19） | 字数=去空白字符数 |
| 格式覆盖 | `build_report` | 标题互相包含即命中 |
| 评分覆盖 | `build_report` | **只精确名称匹配**（比大纲映射更严格，遗漏写 warning） |
| 占位符 | `extract_placeholders`（`schema.py` L85） | 全篇去重 + 排序 |

> 一个容易混淆点：大纲阶段 `_map_scoring_factors`（粗匹配）与报告阶段 `BidReport.summary`（`schema.py` L282，精确匹配）标准不同，可能出现「大纲里归了该因素但报告未标记 covered」。

### 九、阶段⑥ Word 导出：`docx_export.py`

位置：[`docx_export.py`](../src/bid_writer/docx_export.py)，入口 `markdown_to_docx`（L27）。

| 符号 | 位置 | 要点 |
|---|---|---|
| `_render_body` | L78 | 行级解析：代码围栏、表格、标题、列表、分页符、段落 |
| `_set_default_style` | L253 | 正文宋体 11pt |
| `_add_line` / `_add_heading` | L261 / L281 | 行级分派；标题黑体、`**` 拆 run |
| `_add_table` | L318 | Markdown 表格 → `Table Grid` |
| `_add_cover` / `_add_toc` | L119 / L151 | 封面与目录域 |

**能力边界**（文件头 docstring 明确）：不处理斜体、链接、图片、嵌套列表、脚注。`bid.docx` 是「够用但不全」的还原，真正的结构化产物仍是 `bid.md` + `report.json`。

### 十、失败兜底与降级策略总表

| 环节 | 失败表现 | 去向 |
|---|---|---|
| LLM 策划 | 异常/空 | 回退 `_fallback_outline` 确定性大纲 |
| 检索建集合 | `get_collection` 异常 | `collection=None`，查询返回空，不阻断写作 |
| 检索查询 | 异常 | 返回空列表 |
| 写作/修订 | 空内容 | 抛 `BidWriteError`，任务失败 |
| 评审 | 异常/解析失败 | 降级 `score=0`，不抛 |
| Word 导出 | 异常 | `report.warnings` 记一条，Markdown/报告仍产出 |

设计哲学：**「确定性产物优先，模型是增强」**——大纲有兜底、检索可空、评审可降级；但写作/修订空内容视为硬失败（因为没正文就无从汇总）。

### 十一、改动指南（按你想动的位置）

| 想改什么 | 看哪里 |
|---|---|
| 章节策略/占位符/缺失字段逻辑 | `schema.py`：`choose_strategy` L71、`extract_placeholders` L85、`WriteOptions` L208 |
| 大纲生成规则 | `agents.py`：`plan_sections` 及其子函数 L44–L315 |
| 单个章节写/评/改循环 | `agents.py`：`section_agent` L326–L366 |
| 并发行/检索串行 | `graph.py` L23 + `retrieval.py` |
| 提示词话术/硬约束 | `prompts.py` 全文件 |
| 覆盖报告字段/判定 | `assembly.py` L57 + `schema.py` 的 `BidReport`（L249） |
| Word 还原能力 | `docx_export.py` 全文件 |
| 对应测试 | `tests/test_bid_writer.py`、`tests/test_bid_writer_export.py` |

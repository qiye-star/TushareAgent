# 设计：RAG 财报知识库（A股年报）

> 本文档是 `demomcp/rag/` 针对 **A股上市公司年报**（PDF，如比亚迪 / 宁德时代 2024/2025 年报）的**专门化设计蓝图**。
>
> 图编排（`rewrite_query → parallel{call_tools, rag_retrieve} → integrate → …`）与本文的解析/切块/检索/引用件**大体已按本文落地**（细节以 `RAG_INTEGRATION.md`（当前实现）与代码为准）；本文仍保留「设计为何如此」的动因与评估标准，**不重复实现细节**。文中「现有/未来」措辞为设计视角，不代表实现状态；凡与代码冲突处，**以代码与 `RAG_INTEGRATION.md` / `ARCHITECTURE.md` 为准**。

---

## 实现状态（截至 2026-08-27）

| 主题 | 状态 | 与本文的差异 | 代码位置 |
|---|---|---|---|
| `demomcp/rag/` 模块 | ✅ 已实现 | 19+ 模块（含本文未列的 `bm25.py`/`store.py`(MilvusVectorStore)/`fakes.py`），`retriever.py` 为遗留 NullRetriever 占位 | `demomcp/rag/` |
| LangGraph 图编排 | ✅ 已实现 | **五节点**：router → rewrite_query → tool_rag → synthesizer/fallback；`integrate/verify_reasonableness/structure_output` 分别融入 synthesizer（后者未实现回环） | `demomcp/graph/` |
| 混合检索 | 🔶 部分 | 已实现但为**三路纯 RRF**（块稠密恒开 + 节稠密 + 节 BM25，`strategy!=factual` 时）——非本文 §5.2 的「加权 dense/BM25」；`RAG_HYBRID_DENSE_WEIGHT` 预留、无读取者 | `rag/hybrid_retriever.py` |
| 两阶段检索（recall→聚合→rerank） | ✅ 已实现 | 数值默认 50/5/30（本文 §5.7 曾写 30/3/24）；rerank 失败回退 RRF 序且**跳过阈值** | `rag/hybrid_retriever.py` |
| 元数据过滤（company/year） | ✅ 已实现 | 在线路径 `infer_filters` 可识别比亚迪/宁德时代 → `RagFilters` 生效（旧文中「恒空」已过时） | `rag/query_build.py` + `graph/nodes.py` |
| 确定性引用 CiteRef | ✅ 已实现 | 格式 `[公司·年份年报 - 第X页 章节]` + 表格题注后缀 | `rag/citing.py` |
| 持久化 | ✅ 已实现（蓝图未提） | **Milvus-Lite（milvus.db）+ SQLite RelStore（rag_rel.db）**；has_index→load_index 快路径；单写者，多进程走 HTTP | `rag/persist.py` + `rag/runtime.py` |
| HTTP 检索服务 | ✅ 已实现（蓝图未提） | `/api/rag/retrieve` + `/api/rag/health` 挂 web:8010；`HttpRetriever`（Bearer/20s） | `rag/http_retriever.py` + `rag/server.py` + `entry/web.py` |
| hyDE / 查询扩展 | 🔶 部分 | 术语键词扩展已实现；hyDE 开关 off（未启用分支） | `rag/query_build.py` |
| 真模型依赖 | 🔶 部分 | 嵌入/重排走**服务端 API**（bge-m3 / bge-reranker-v2-m3），本地无 torch/faiss/sentence-transformers——与本文一致；向量库为 **pymilvus + milvus-lite**（本文未提） | `pyproject.toml` |
| 评估 runner | ✅ 已实现 | `scripts/eval_rag.py`（离线门禁）+ `scripts/validate_rag.py`（真引擎）；金标 `tests/rag_golden/` | `scripts/` |
| 质量自检回环（§7.4/§10.2 verify_reasonableness） | ❌ 未实现 | 图上无回环边；合成失败直接转 fallback | `graph/routes.py` |

---

## 0. 摘要

为「LLM + MCP 数据对话助手」补上一路**非结构化财报知识**佐证：把 A股年报 PDF 离线解析成**带章节层级与表格结构**的向量/词法索引，在线对**业务概况、研发投入、主营业务构成、风险因素**等长文本做**高精度检索**，并让每条回答**逐条标注文档出处与依据**（如 `[比亚迪2024年报 - 第42页 3.2 研发投入]`）。

本文围绕三大功能展开：**结构化解析与切片**（§4）、**高精度检索**（§5）、**可信溯源**（§6）。

---

## 1. 目标与范围

### 1.1 要解决的问题

| 问题 | 表现 | 后果 |
|---|---|---|
| 长文本小节 | MD&A（管理层讨论与分析）、研发投入、主营业务构成、风险因素都是**数千字**的小节 | 纯稠密 top-k 只能捞到零散碎片，凑不成完整结论 |
| 术语与结构查询 | 「2024 研发费用率」「主营业务中哪个占比最高」「毛利率同比变化」这类查询依赖**术语识别与节层级语义** | 与事实型查询的检索信号完全不同，单一检索器难以兼顾 |
| 表格 | 年报大量表格（主营业务构成、研发投入、产销量、现金流） | 泛用切块会**拆散表格、丢失表头（列名）与所属章节**，命中的块无法自述出处 |
| 可信溯源 | 每条结论必须给出「文档 × 页码 × 节」 | 泛用 `RagChunk` 只记 `doc_id + chunk_index`，**无页码、无节路径**，无法生成 `[比亚迪2024年报 - 第42页 3.2 研发投入]` 式引用 |

### 1.2 非目标

- **不做 OCR 扫描件**：默认语料为**数字化文本层**的 PDF（可选中复制文字的电子年报）；扫描件（图片型 PDF）列为远期（见 §9.2）。
- **不做语义推断式「算数」**：RAG 只负责检索+引用，跨指标计算（如「研发费用率 = 研发投入 / 营业收入」若年报未直接给出）应由 LLM 在引用事实之上推导，不得让 RAG 自行编造算式结果。
- **不替代工具取数**：RAG 佐证的是**非结构化**的财报正文/表格；结构化行情、财务指标仍走现有 MCP 工具链路（`call_tools`）。两者并行、经 `integrate` 汇聚（沿用 ARCH §9）。

### 1.3 与 `docs/ARCHITECTURE.md` 的关系

- **继承**：图编排（§4/§6）、`GraphState`（§5）、`Evidence`/`Claim`（§9）、`VerificationResult`/回环上限（§10）、「无来源不进 `claims`」（§11.2）、依赖方向（`graph`/`rag` 只被注入、不侵入 `interfaces`/`providers`）。
- **专门化**：只深化 `rag_retrieve` **内部**（向量 top-k → hybrid + section 聚合 + rerank），其对外契约仍是「输入 `rewritten_query` + `retrieval_plan`，输出 `list[RagChunk]`」，故图节点无需改动。
- **差异点（本文 = 财报 delta）**：
  1. ARCH §8.2 纯向量 top-k → 本文 **hybrid + section 聚合 + rerank + 元数据过滤**；
  2. ARCH §8.3「按语义/标题切块」→ 本文**层级感知 + 表格专用块 + 冗余溯源元数据**；
  3. ARCH §12 `RAG_CHUNK_SIZE=800/150` → 财报用 **`RAG_FIN_DENSE_CHUNK=400/80`**（因 bge 窗口）；
  4. ARCH §11.1 `Citation` → 增加溯源字段；
  5. ARCH §13 新增依赖/配置 → 见本文 §7。

---

## 2. 语料与来源

### 2.1 语料形态

- **A股上市公司年报**（`报告类型` = 年度报告），PDF，**数字化文本层**、非扫描件。
- 每份报告按「**公司 × 财年**」唯一化：`company`（公司名）、`company_code`（如 `002594.SZ`）、`year`（财年，如 `2024`）。

### 2.2 公司 + 年份归一化

年报首页、封面、目录处可提取公司名/股票代码/财年；公司名需归一化（去掉「股份有限公司」「A 股简称」后缀），并维护「公司名 ↔ 股票代码」映射，供查询改写阶段把用户口语（「比亚迪」）归一为 `company`/`company_code`。这是 §5.4 元数据过滤、§6.1 引用命名的前提。

### 2.3 来源目录与入库

- PDF 放在约定的语料目录（如 `<root>/data/corpus/{company}/{year}/`），`DocMeta` 记录 `source_pdf`（路径/URL）与 `source_url`（若有）。
- **离线摄取**：PDF 经 §4 管线解析后入库；`doc_id` 用稳定编码（如 `fy{year}_{code}`，`doc_id="fy2024_002594"`）。更新某份报告 = 删旧 `doc_id` 索引后重跑，幂等。

```python
class DocMeta(BaseModel):            # 扩展 ARCH §8.1 的 DocMeta
    doc_id: str                      # fy2024_002594
    title: str                       # 比亚迪股份有限公司 2024 年年度报告
    company: str                     # 比亚迪
    company_code: str                # 002594.SZ
    year: int                        # 2024
    report_type: str                 # annual_report
    source_pdf: str                  # 路径/URL
    source_url: str | None = None
    total_pages: int | None = None
    parse_tool: str = "pdfplumber"   # pdfplumber | camelot
    ingested_at: str | None = None
```

---

## 3. 整体流程与分层

### 3.1 分层图（新增子模块）

在 ARCH §3 分层的 `rag` 层之下，再细分出财报专用子模块（其余层复用，不改依赖方向）：

```
graph        LangGraph 编排（已落地为五节点；rag_retrieve 即 tool_rag._rag_retrieve）
rag           检索层（以下模块均已实现；★ 为本文未预见的后加件）
  +─ pdf_parser.py        PyMuPDF 原生 block（text/image）抽取 + find_tables 表格
  +─ section_tree.py      字号聚类 + 编号正则 → SectionNode 语义树
  +─ table_split.py       表格题注/表头识别、跨页去重合并、行分块、上下文随行
  +─ segments.py          Block 结构 + 块类型判定 + 元数据注入
  +─ chunking.py          块感知 dense 切分（恒有重叠）/ section 整节 / 块边界锚点
  +─ captioner.py         多模态图块描述（Noop 默认 / deepseek-v4-flash-vision-exp 可选）
  +─ embedder.py          HashingEmbedder（默认） / ApiEmbedder（bge-m3 /embeddings）
  +─ bm25.py ★            自包含 OKAPI BM25（jieba 词元；iter_state/from_state 可持久化）
  +─ store.py ★           InMemoryVectorStore / FaissVectorStore / MilvusVectorStore（向量库选择）
  +─ hybrid_retriever.py  BM25(jieba)+dense 混合、元数据过滤、RRF 融合、section 聚合
  +─ reranker.py          服务端重排（SiliconFlow /rerank，可关，回退 RRF 序）
  +─ query_build.py       从 retrieval_plan 生成 dense/BM25/hyDE 查询 + 术语键词扩展
  +─ citing.py            RagChunk → CiteRef → Citation 映射、reflist 格式化
  +─ schemas.py           扩展：RagChunk / DocMeta + SectionNode / Block / CiteRef
  +─ ingest.py            9-stage 摄取管线编排（build_index / ingest / RagIndex.add_doc）
  +─ runtime.py ★         build_runtime_retriever（进程缓存；has_index 快载 / 按 CORPUS_DIR 摄取）
  +─ persist.py ★         Milvus-Lite + SQLite RelStore：保存/加载/检测索引（含 BM25 状态）
  +─ http_retriever.py ★  HTTP 检索客户端（另一进程取数，不开 Milvus）
  +─ server.py ★          retrieve_from / health_from 纯函数（供 web /api/rag/* 用）
  +─ fakes.py ★           合成语料与假检索器（测试/离线评估）
  +─ retriever.py         遗留 NullRetriever 占位（无引用）
interfaces/providers/config/db/entry   全部不变（复用）
```

### 3.2 离线摄取管线（9 个 stage）

```
 1 acquire         定位 PDF + 归一化 company/year → DocMeta
 2 extract_layout  PyMuPDF 逐页取原生 block(text/image)+find_tables 表格 + 图块字节
 3 build_tree      字号聚类 + 编号正则 → SectionNode 语义树（含每节页码跨度）
 4 segment_blocks  沿树把每叶节的 text/image 原生块与表格归为 Block（block_type）
 5 assign_meta     注入 company/year/section_path/page/table_* 到每个 Block
 6 chunk           段落 → 块感知 dense 小块（恒有重叠）；表格 → 表格块；标题 → 块边界锚点
 7 embed           元数据前缀 + 文本（{company}{year} {section_path} {heading} {text}）→ bge-m3 嵌块；section 用 BM25 + 代表向量
 8 index           写 2 个向量库集合 + 存 BM25 倒排 + 存 parse-tree 旁车
 9 verify          统计(节/块数, 覆盖率) + 抽样回开 PDF 校验页码/题注 —— 幂等可重跑
```

- **幂等**：以 `doc_id` 为粒度，重跑先删该 doc 旧索引再写。
- **两套索引**：`chunk`（dense，小块）与 `section`（BM25，整节）——这是 §5 检索设计的地基。

### 3.3 在线检索管线（`rag_retrieve` 内部语义）

```
rewritten_query + retrieval_plan
   │  query_build：拆出 company/year/指标 → 扩展键词 → 生成 dense 与 BM25 查询
   ▼
 Stage 1 recall    元数据过滤(company+year) → chunk/section 两索引并行
                   dense(top-k) + BM25(top-k) → RRF 融合 → 候选({chunk}×{section})
   │
   ▼
 Stage 2 聚合     候选按 section_path 聚合出候选节 → RAG_TOP_K_SECTIONS
   节内取最优 chunk → reranker(bge-reranker) 重排 → RAG_SCORE/TOP_K 过滤
   ▼
 list[RagChunk]（自带 cite_ref，进入 integrate 节点）
```

---

## 4. 结构化解析与切片

目标是：**任何一块文本或表格，都能自述「我来自哪家公司哪一年、哪个章节、第几页」**——这既是检索的过滤键，也是溯源的地基。

### 4.1 PDF 层级标题提取（编 / 章 / 节 / 条款）

**策略 = 字号聚类（主）+ 编号正则（校验）双融合。**

- 用 **PyMuPDF(`fitz`)** 逐页取 `get_text("dict")` 的**原生 block**（type=text/image，含 lines→spans，带 `size`/`bbox`/`block_no`）。一个 text block ≈ PDF 内部排版的**天然段落**；`size` 取块内 span 字号。据此做字号聚类：字号显著大于正文（正文约 10.5pt，节标题 14–18pt，章/编更大）的块归为候选标题；字号越大层级越高（level 越小）。
- **编号正则**（用于确认/纠错，尤其字号不显著的标题）：

| 层级 | 正则模式 | 示例 |
|---|---|---|
| 编 / 章 | `第[一二三四五六七八九十百]+[章编]` | 第七章 财务会计报告 / 第三编 |
| 节（年报必用） | `第[一二三四五六七八九十]+节` | 第三节 管理层讨论与分析 |
| 数字层级 | `^\d+(\.\d+)*\s` | `3.2 研发投入`、`1.1`、`1.2.1` |
| 中文序号 | `^[一二三四五六七八九十]+、` | 一、经营情况讨论与分析 |
| 括号序号 | `^（[一二三四五六七八九十]+）` | （1）分行业 |

- **融合规则**：正则命中的行若字号也落在「标题簇」→ 定级；若仅正则命中而字号接近正文（如某些「1.1 概述」用正文字号）→ 按正则定级并**降一等**处理（避免把正文小节误提为一级标题）。

### 4.2 章节语义树 `SectionNode`

产出一棵嵌套树，`path` 是祖先标题链，`page_start/page_end` 是 PDF **物理页码**（1-based）。

```python
class SectionNode:
    id: str                # 稳定 id：sha1(doc_id + path)
    heading: str           # "3.2 研发投入"
    level: int             # 0=根
    path: list[str]        # 祖先标题链：[..., "1、主营业务分析", "(1) 分行业"]
    page_start: int
    page_end: int
    children: list["SectionNode"]
    blocks: list["Block"]  # 该节内文本/表格块
    text: str              # 整节正文字符串（供 BM25，见 §5.3）
```

### 4.3 表格提取

**默认 `pymupdf`（`page.find_tables`）；`camelot-py` 可选；`table-transformers` 列为远期。**

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| `pymupdf.find_tables` | 与原生 block 同源，`uv sync --extra rag-full` 干净安装 | 复杂表格（跨页、嵌套、无框线）偶有错切 | **默认** |
| `camelot-py`（lattice） | 有框线表格精度高 | 需**系统级 Ghostscript**，容器/Windows 安装费事、重 | **可选**（`RAG_TABLE_ENGINE=camelot`） |
| `table-transformers` | 处理无框线/复杂格 | torch 大、慢、精度需调 | **远期** |

- **题注识别**：表格上方紧邻、字号小于标题但不小于正文、且以 `表[一二三四五六七八九十百\d]+` 或「单位：…」/「数据来源：…」开头的行 → `table_caption`。
- **表头识别**：`find_tables().extract()` 结果的前 1–2 行 → `table_headers`。
- **跨页合并与表头去重**：财经报表常出现「续表」，每页顶部重复打印表头。用**表头行指纹**（同一逻辑表相邻页表头文本一致）把跨页同表头分段合并为一个逻辑表单元；重复表头行只保留一次。

### 4.4 表格切片算法（题注 + 表头 + N 行 + 所属节 + 上下文）

> 目标：**一块表格 chunk 自带「列名、表头、题注、所属节、页码」**，检索命中即能出 `[第42页 · 3.2 研发投入 · 表 12 研发投入情况]` 式引用。

- **切片单位**：`题注行` + `表头行` + `RAG_TABLE_ROWS_PER_CHUNK`（默认 8）条数据行 + 包围它的**节上下文**（节标题链 + 与表格同属一节的**前/后一两句正文**，作为语境，占 ≤15% 字符）。
- **单表整存 vs 拆行**：数据行 ≤8 且总字符 < `RAG_FIN_DENSE_CHUNK` → 整表一块；否则按 `RAG_TABLE_ROWS_PER_CHUNK` 行分块，**每块仍携带同一表头 + 题注**。
- **上下文如何随行**：把 `(section_path, table_caption, table_headers, 该表所属节的首句/本节标题)` 拼进 `text` 与 `metadata`；`table_caption` 用原文，保证表头释义不丢失。
- **文本化**：`text` 用 ` | ` 连接表头与各列；`table_headers` 单独入 `metadata`，便于前端渲染成 Markdown/HTML 表格。

### 4.5 块类型与切片粒度

**切片以 PyMuPDF 原生 block 为段落单元**：一个 text block ≈ 一个自然段落，块边界**优先**作为 chunk 切割点，**且相邻 chunk 恒有重叠**（overlap 默认 80，把上一块尾部文本带入下块开头）。图/表块按需单独成块。

| 块类型 | 切片粒度 | 用途 |
|---|---|---|
| `paragraph` | **块感知 dense 小块**（`RAG_FIN_DENSE_CHUNK=400` 字 / 重叠 80，按块边界切 + 恒有 overlap） | 事实/数字查询（精度） |
| `section` | **整节**文本（供 BM25，无长度限制） | 结构查询（召回），指向该节所有子块 |
| `table` | 整表或按行分块（见 4.4） | 数字/构成类查询 + 表格溯源 |
| `header` | 标题块作为块边界锚点 | 切块定位、节上下文 |
| `figure` / `note` | 图 / 注释单独成块 | 语义完整性 |

**图/表块（`figure`）多模态兜底**：`kind==image` 且无内嵌文本的图/表块——能提取成立则走表格/文本；提取不了（纯图表）用**多模态模型 `deepseek-v4-flash-vision-exp`** 描述成文本入库（经现有 DeepSeek/OpenAI 兼容 client 发 base64）。未配置/失败 → **跳过并记 note，不编造描述**。

### 4.6 每块元数据 schema（扩展 `RagChunk.metadata`）

`RagChunk` 基类字段（`doc_id / doc_title / chunk_index / text / score / metadata`）**不动**；`metadata` 扩展为财报专用字段。**每个 chunk 都冗余携带来源与层级信息**（而非仅顶层 doc），使任何被命中的块都自带完整溯源——这是 §6「引用保证」的根基，检索层毋需回查 parse tree。

```python
metadata = {
    # —— 来源 / 范围（核心）——
    "company": "比亚迪",                # 公司名（归一化）
    "company_code": "002594.SZ",       # 股票代码
    "year": 2024,                      # 财年
    "doc_id": "fy2024_002594",         # 与外层 doc_id 一致
    # —— 层级定位（用于溯源与结构检索）——
    "section_path": ["第三节 管理层讨论与分析", "二、报告期内主要经营情况", "1、主营业务分析", "(1) 分行业"],
    "section_level": 4,
    "heading": "研发投入",              # 本块所属最近标题
    "page_start": 42, "page_end": 42,  # PDF 物理页码（1-based，可区间）
    # —— 块类型与表格 ——
    "block_type": "paragraph",         # paragraph | table | header | figure | note
    "table_headers": ["项目", "本报告期", "上年同期", "同比"] | None,
    "table_caption": "表 12 研发投入情况" | None,
    # —— 辅助 ——
    "position": {"top": 320.5, "left": 80.0},   # 页内坐标（可选，前端高亮/跳转）
    "source_url": "https://...", "ingested_at": "2026-08-25T00:00:00Z",
    "raw_heading_depth": 3,            # 原始字号聚类深度（调试用）
}
```

---

## 5. 高精度检索

### 5.1 为什么纯 dense 在财报上不行

- **embedding 输入含元数据 + 整节过长无法一次嵌入**：向量化时把 `{company}{year} {section_path} {heading}` 前缀拼进文本，强化跨公司/年份/节区分度；同时 MD&A 整节常数千字，嵌入窗口受限 → **整节无法一次嵌入**。
- **纯小 chunk dense** 对事实/数字型查询（`2024 归母净利润`）召回好，但对**结构型查询**（`主营业务中哪个占比最高`、`研发费用率变化`）召回差——答案分散在多块、依赖节层级语义，chunk 向量无法感知「这个小节讲的是主营构成」。

解法是 **两套索引 + 两阶段 + 意图路由**（本文最关键的决策）：

### 5.2 混合检索：BM25（jieba 键词）+ dense（bge-m3）

- **chunk 索引（dense，精度）**：小块逐块嵌入（内容含元数据前缀），对事实/数字类查询最利。
- **section 索引（BM25 为主，结构复用）**：**整节**为一个可检索单元，文本用 BM25（`rank-bm25` + `jieba` 分词，**无长度限制**），辅以「节代表向量 = heading 首 300 字池化 + 平均」仅作补充信号。对结构类查询最利——把「正确的节」整节捞回，其 `section_path` 指向该节所有子块。
- **融合**：dense 与 BM25 各取 top-k，用 **RRF（Reciprocal Rank Fusion）** 融合，避免不同分数域对齐问题。
  > 实现注（2026-08-27）：实际为**三路纯 RRF**（块稠密 + 节稠密 + 节 BM25，`strategy!="factual"` 时后两路参与），分数 `Σ 1/(rank + RAG_RRF_K)`；本文「加权 `RAG_HYBRID_WEIGHTS`」未实现，对应配置是 `RAG_HYBRID_DENSE_WEIGHT`（预留在 settings，无读取者）。

### 5.3 分节索引（整节为可检索单元）

- 每个 `SectionNode`（叶子/非叶子节）存一条 `section` 记录：`text`（全节正文）、`heading`、`section_path`、`page_start/end`、`doc_id/company/year`。
- 检索命中 `section` 后，按 `section_path` 展开到该节所有子 `chunk`，取子块用于引用。这样「结构命中的是节，引用给的是节内最相关的最优块」。

### 5.4 元数据过滤（company + year 范围感知）

- `retrieval_plan.filters` 记录改写阶段识别出的 `{company, year}`；当**已知**时，`RAG_STRICT_SCOPE=true` 做**精确过滤**（`company` 匹配 + 可选 `year` 匹配），从根上避免「比亚迪 2024 研发」命中宁德时代。（不识别出 company/year 时，关闭过滤靠相关度兜底。）
- `section_type` 过滤（可选）：识别出「业务概况/研发投入/主营构成/风险因素」时，可先在该类节候选内检索，提升精度。

### 5.5 查询扩展 / hyDE（可选）

- **术语键词扩展（默认开，成本最低、收益最稳）**：对 query 用 `jieba` 分词 + 财报术语表（净利率、研发费用率、扣非、归母净利润、毛利率、分行业/分产品/分地区……）**抽取/补充键词**，喂给 BM25。
- **hyDE（默认关，`RAG_HYDE=false`）**：用 DeepSeek 生成一条「假设回答文档」，嵌入后与 query 向量融合；收益是增强召回，成本是一次 LLM 调用。

### 5.6 两阶段检索设计（recall → section 聚合 → rerank）

- **Stage 1 recall**：元数据过滤（§5.4）→ chunk 与 section **两索引并行**混合检索 → RRF 融合 → 候选（`RAG_CANDIDATE_K=50`）。
- **Stage 2 聚合 + rerank**：把候选 chunk 按 `(doc_id, section_path)` 聚合**（跨公司同名节不合并）**，得**候选节**（`RAG_TOP_K_SECTIONS=5`）；每候选节取 **best ** (`max(RAG_TOP_K, ceil(RAG_RERANK_CANDIDATES / n_sections))` 个、去重，池硬上限 `RAG_RERANK_CANDIDATES=30`；无 chunk 的节回退节级候选），用 **SiliconFlow `/v1/rerank`（`BAAI/bge-reranker-v2-m3`）** 重排（query × chunk，服务端、复用嵌入 base/key）。阈值 `RAG_RERANK_THRESHOLD=0.2` + `RAG_TOP_K=5` → 输出自带 cite_ref 的 `list[RagChunk]`。
  - 未配置 key / 调用失败 → 回退 RRF 序并计 `rerank_degraded`，**跳过阈值截断**（RRF 分数量级 ~1/60，0.2 阈值会全灭）。

**意图路由**：`retrieval_plan.strategy ∈ {structural, factual, auto}`：

| strategy | 行为 | 适用 |
|---|---|---|
| `structural` | section 索引优先 | 「主营业务构成」「占比最高」「研发投入结构」 |
| `factual` | chunk 索引优先 | 「2024 归母净利润」「研发费用是多少」 |
| `auto`（默认） | 双路都跑 + RRF 融合 | 未显式分类 |

### 5.7 默认参数表（财报专用；标注与 ARCH §12 差异）

| 参数 | 财报默认 | 说明 | 与 ARCH §12 |
|---|---|---|---|
| `VECTOR_STORE_PATH` | `<root>/data/vectorstore` | 本地向量库地址（**已实现：milvus.db + rag_rel.db**） | 同 |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | OpenAI 兼容 `/embeddings` 服务端模型（本地不加载）；`hashing`=测试兜底 | **覆盖**（原 bge-small-zh-v1.5） |
| `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY` | 空 | 嵌入 API 地址/密钥；二者非空且 `RAG_USE_REAL=true` 才走 API，否则回退 hashing | **新增** |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | SiliconFlow `/v1/rerank` 服务端重排（复用嵌入 base/key）；无 key 回退 Noop | **新增** |
| `RAG_TOP_K` | `5` | 最终返回 chunk 数 | 同 |
| `RAG_CANDIDATE_K` | `50` | 每路候选数（recall-first） | **新增** |
| `RAG_TOP_K_SECTIONS` | `5` | 聚合后候选节数 | **新增** |
| `RAG_SCORE_THRESHOLD` | `0.3` | dense 粗筛阈值 | 同 🔶 声明但代码未读取 |
| `RAG_RERANK_THRESHOLD` | `0.2` | rerank 阈值（bge-reranker-v2-m3 对真实 MD&A 分偏低） | **新增** |
| `RAG_RERANK_CANDIDATES` | `30` | 重排候选池硬上限 | **新增** |
| `RAG_FIN_DENSE_CHUNK` / `RAG_FIN_DENSE_OVERLAP` | `400` / `80` | dense 小块粒度（bge 512 token） | **覆盖**（ARCH 800/150 偏大易被截断） |
| `RAG_SECTION_MAX_CHARS` | `8000` | 节文本截断（BM25 用） | **新增** |
| `RAG_TABLE_ROWS_PER_CHUNK` | `8` | 表格分块行数 | **新增** |
| `RAG_HYBRID_DENSE_WEIGHT` | `0.6` | 🔶 预留：实际三路纯 RRF（`Σ1/(rank+RAG_RRF_K)`），无读取者 | **新增** |
| `RAG_RRF_K` / `RAG_BM25_K1` / `RAG_BM25_B` | `60` / `1.5` / `0.75` | RRF 的 k 与 BM25 参数 | **新增** |
| `RAG_STRICT_SCOPE` | `true` | company+year 已知即硬过滤 | **新增** |
| `RAG_HYDE` | `false` | 假设文档增强（未启用分支） | **新增** |
| `RAG_STRATEGY` | `auto` | structural / factual / auto（意图映射：report→factual、compare→structural） | **新增** |
| `RAG_TABLE_ENGINE` | `pymupdf` | 表格引擎（pymupdf/camelot） | **新增** |
| `RAG_CAPTIONER` | `deepseek-v4-flash-vision-exp` | 多模态图块描述模型（需 `DS_API_KEY`，无 key 回退 Noop 跳图块） | **新增** |
| `RAG_CORPUS_DIR` / `RAG_HTTP_URL`(+) | `""` | 🔶 后加：显式设置才摄取；RAG_HTTP_URL 非空 → 进程走 HttpRetriever | **后加** |

> 数值以 `demomcp/config/settings.py` 与 `.env.example` 为唯一事实源（本表已按 2026-08-27 代码对齐）。`RAG_FIN_DENSE_CHUNK=400` 是**结合 real 向量模型窗口与 400 字留余量**的理性选择；`RAG_EMBEDDING_MODEL=hashing` 仅作离线/测试兜底。`RAG_RETRY_MAX`（原§5.7）不存在于 settings，已删除。

---

## 6. 可信溯源（Attribution）

### 6.1 `CiteRef`：块 → 引用映射

从命中 chunk 的元数据**确定性**生成（不靠 LLM 编页码）：

```python
class CiteRef(BaseModel):
    title: str          # doc_title；标签更短可缩为"比亚迪2024年报"
    page: str           # metadata.page_start（物理页码，含区间时 "42" 或 "42-43"）
    section: str        # " ".join(metadata.section_path) 或末尾 heading
    heading: str        # metadata.heading
    company: str        # metadata.company
    year: int           # metadata.year
    block_type: str     # paragraph | table | ...
    inline: str         # 内联标记："[比亚迪2024年报 - 第42页 3.2 研发投入]"
    ref_index: int      # 参考文献列表序号
```

### 6.2 引用如何被保证

每个 chunk **在 ingest 时已冗余写入** `doc_id × doc_title × page_start/end × section_path`（见 §4.6）。故 `structure_output` 只需把命中 chunk → `CiteRef`，**无需回查 parse tree、无需 LLM 生成**——这就是「不会出现假页码 / 假节」的机制。

### 6.3 引用格式（内联标记 + 参考文献列表）

- **内联标记**：正文插入 `[比亚迪2024年报 - 第42页 3.2 研发投入]`（表格块额外含题注：`[比亚迪2024年报 - 第42页 3.2 研发投入 · 表 12 研发投入情况]`）。
- **参考文献列表**（reflist）：`[1] 比亚迪股份有限公司 2024 年年度报告，第 42 页，第三节 管理层讨论与分析 → 3.2 研发投入`。`ref_index` 与内联标记一一对应。

```python
class Citation(BaseModel):        # 扩展 ARCH §11.1，全部为可选新增字段
    claim_id: str
    source_type: Literal["tool", "rag"]
    source_id: str                # RAG：doc_id#chunk_index（沿用）
    title: str                    # 文档标题 / 工具名（沿用）
    excerpt: str                  # 摘要片段（沿用；RAG 取 chunk 前 N 字）
    page: str | None = None       # 沿用；页码（可区间）
    link: str | None = None       # 沿用；来源 URL / 数据代理端点
    # —— 新增（仅 RAG，工具引用不填）——
    section_path: str | None = None   # "第三节 管理层讨论与分析 → 3.2 研发投入"
    page_end: str | None = None
    block_type: str | None = None
    company: str | None = None
    year: int | None = None
    ref_index: int | None = None
    inline_marker: str | None = None
```

### 6.4 页码 vs 节路径：如何派生

两者都展示：`page` 供**跳转**，`section` 供**语义定位**。`section` 由命中块的 `metadata.section_path` 直接给出（ingest 时由 `SectionNode.path` 注入每个块），检索/引用层零成本取用。

### 6.5 「无来源」处理（不编造）

与 ARCH §11.2 一致：

- **任何 `claim` 必须 ≥1 条 `Citation`**；「无来源」的结论**禁止进入 `claims`**。
- 检索不到或低于阈值的结论改记 `unresolved`；`StructuredAnswer.is_reliable=False`；正文明示「需人工核实 / 未检索到依据」，**绝不编造页码或节**。

---

## 7. 落地增量（相对 ARCHITECTURE.md §8）

### 7.1 新增子模块（`demomcp/rag/` 之下，§8 五件套之外）

| 文件 | 职责 |
|---|---|
| `pdf_parser.py` | PyMuPDF 原生 block（text/image）+ `find_tables` 表格 + 图块字节 |
| `section_tree.py` | 字号聚类+编号正则 → `SectionNode`；派生 `section_path`/页码跨度 |
| `table_split.py` | 表格题注/表头识别、跨页合并去重、行分块、上下文随行 |
| `segments.py` | `Block` 结构 + 块类型判定 + 元数据注入 |
| `chunking.py` | 块感知 dense 切分（恒有重叠）、section 整节、块边界锚点 |
| `captioner.py` | 多模态图块描述：`NoopCaptioner`（默认）/ `VisionCaptioner`(deepseek-v4-flash-vision-exp) |
| `hybrid_retriever.py` | BM25(jieba)+dense 混合、元数据过滤、RRF 融合、section 聚合 （`build_retriever`） |
| `reranker.py` | SiliconFlow `/v1/rerank` 服务端重排（可关，回退 Noop/RRF 分） |
| `query_build.py` | 从 `retrieval_plan` 生成 dense/BM25/hyDE 查询 + 术语键词扩展 |
| `citing.py` | `RagChunk → CiteRef → Citation` 映射、reflist 格式化 |
| `schemas.py`（扩展） | `RagChunk`/`DocMeta` + `SectionNode`/`Block`/`CiteRef` + `LayoutBlock`/`LayoutResult` |
| `ingest.py`（扩展） | 9-stage 摄取管线编排（`build_index`/`ingest`） |
| `scripts/eval_rag.py` | 离线评估 runner（标注集 → RAGAS 式指标） |
| `tests/rag_golden/*.yaml` | 小规模标注 QA 集 + gold citation |

### 7.2 新增依赖（`[project.optional-dependencies] rag-full`，**不进入默认 `dependencies`**，容器不装；`uv sync --extra rag-full` 时安装）

| 依赖 | 用途 | 备注 |
|---|---|---|
| `pymilvus` + `milvus-lite` | **向量库（实际实现）** | 真实后端；单进程独占锁，多进程需走 HTTP |
| `pymupdf` | PDF 原生 block / 表格 / 图块提取 | 真实后端；纯核心管线不依赖 |
| `rank-bm25` | 混合检索 BM25 | 纯 Python，轻量（实际实现含自包含 BM25：`rag/bm25.py` + `persist.py` RelStore 持久化状态） |
| `jieba` | 中文分词（BM25 键词 + 术语表） | 纯 Python |
| `camelot-py` | （可选）高保真表格 | **需系统 Ghostscript**，默认不装；`RAG_TABLE_ENGINE=camelot` 时启用（未入 pyproject） |

> **嵌入不做本地加载**：`BAAI/bge-m3` 以 OpenAI 兼容 `/embeddings` API 调用（`RAG_EMBEDDING_API_BASE` + `RAG_EMBEDDING_API_KEY`，服务端跑模型）；因此**无需** `sentence-transformers`/`faiss-cpu`/`torch`，本地只保留轻量依赖。真实后端接入零模型下载。**注意**：向量存储实际是 **Milvus-Lite**（本文稿早期曾提 Chroma/FAISS 候选 —— 未采用）。

> 核心管线默认纯 Python（HashingEmbedder + InMemoryVectorStore + 自包含 BM25 + NoopReranker + NoopCaptioner），**无需任何新依赖即可离线跑通**。真实嵌入走 OpenAI 兼容 `/embeddings` API（服务端 bge-m3），本地不加载 torch/模型。与 `mcp>=1.28,<2`、`pydantic>=2`、`openai>=1` 无冲突。`camelot-py` 因系统依赖**不建议进容器默认镜像**。

### 7.3 新增配置（`demomcp/config/settings.py` + `.env.example`）

- 沿用 ARCH §13.3：`VECTOR_STORE_PATH / EMBEDDING_MODEL / RAG_TOP_K / RAG_SCORE_THRESHOLD / RAG_RETRY_MAX`。
- 新增/覆盖（见 §5.7 参数表）：`RERANK_MODEL`、`RAG_CANDIDATE_K`、`RAG_TOP_K_SECTIONS`、`RAG_RERANK_THRESHOLD`、`RAG_RERANK_CANDIDATES`（重排候选池）、`RAG_FIN_DENSE_CHUNK`、`RAG_FIN_DENSE_OVERLAP`、`RAG_SECTION_MAX_CHARS`、`RAG_TABLE_ROWS_PER_CHUNK`、`RAG_HYBRID_DENSE_WEIGHT`（预留）、`RAG_STRICT_SCOPE`、`RAG_HYDE`、`RAG_STRATEGY`、`RAG_TABLE_ENGINE`、`RAG_CAPTIONER`、`RAG_USE_REAL`、`RAG_EMBEDDING_DIM`、`RAG_RRF_K`、`RAG_BM25_K1`、`RAG_BM25_B`、`RAG_CORPUS_DIR`、`RAG_HTTP_URL`/`RAG_HTTP_TIMEOUT`/`RAG_HTTP_TOKEN`（后加）。

### 7.4 需扩展的既有契约

| 契约 | 位置 | 改动 |
|---|---|---|
| `RagChunk.metadata` | ARCH §8.2 | `metadata: dict` 扩展 §4.6 财报字段（无破坏性） |
| `retrieval_plan` | ARCH §5/§6 | 增 `concepts: list[str]`、`filters: {company, year}`、`strategy`（工具/检索字段不变） |
| `Citation` | ARCH §11.1 | 增可选 `section_path/page_end/block_type/company/year/ref_index/inline_marker`（工具引用不受影响） |
| `DocMeta` | ARCH §8.1 | 增 `company/company_code/year/source_pdf/total_pages/parse_tool/report_type`（见 §2.3） |

---

## 8. 评估与验证

### 8.1 小规模标注 QA 集

约 30–50 题，覆盖 5 类，每题含 **gold answer + gold citation**（`doc_id`、`section`、`page`、表格题注）：

| 类别 | 示例 |
|---|---|
| 数字事实 | 「比亚迪 2024 年归母净利润是多少？」 |
| 结构对比 | 「近期主营构成中哪个产品占比最高？」 |
| 跨公司消歧 | 「比亚迪研发投入 vs 宁德时代研发投入」 |
| 表格溯源 | 「2024 收入构成表 XX 产品的收入金额」 |
| 术语 | 「2024 年研发费用率 / 扣非净利润」 |

### 8.2 RAGAS 式指标

- `context_recall`：gold 证据是否在 top-k；
- `faithfulness`（LLM-as-judge，可用 `MockLLM` 离线当 judge）：答案是否被上下文支撑；
- `answer_relevance`：是否切题；
- `precision@k / recall@k`（chunk 级）、`map@k`（节级）。

### 8.3 表格溯源专项测试

命中 chunk 必须是「**正确表格 + 正确节 + 正确页码**」三重断言（例：要 `表 12 研发投入情况`，命中的必须是含正确 `table_caption` 的 `table` 块，且 `page` 与 `section_path` 与 gold 一致）。

### 8.4 跨公司隔离负例

query 含「比亚迪」时，top-k 不得出现宁德时代块（或已被 `RAG_STRICT_SCOPE` 过滤）。这是元数据过滤正确性的回归用例。

### 8.5 离线 runner 与 CI gate

- **默认可离线**：纯 Python(hashing) 嵌入 + InMemoryVectorStore + 自包含 BM25 + `MockLLM`（作 judge）→ 无需真实 DeepSeek/网络即可跑 `scripts/eval_rag.py`；真实接入改用 OpenAI 兼容 `/embeddings` API。
- 可作 CI gate（阈值：如 `context_recall@5 ≥ 0.8`、`faithfulness ≥ 0.7`、表格溯源三重断言通过率 ≥ 90%）。

---

## 9. 风险与取舍

| 风险 | 影响 | 取舍 / 缓解 |
|---|---|---|
| 依赖体积 / 容器 / Windows 兼容 | PDF 解析 + 向量依赖 | 核心纯 Python；真实后端进 `rag-full` 可选组（`pymupdf`），嵌入走 API 不装 torch/模型；`camelot-py`（Ghostscript）不进默认镜像；`RERANK_MODEL` 留空则跳 rerank |
| 扫描件 OCR | 图片型 PDF 无法直接解析 | 列为远期（PaddleOCR）；本文默认数字化文本层 |
| bge 512-token 窗口 | 整节无法一次嵌入 | 用官方推荐 512 + `RAG_FIN_DENSE_CHUNK=400` 小块规避；section 走 BM25 |
| 财报措辞 / 节名命名不统一 | 正则识别不准、检索漏召 | 增强正则 + 术语表；必要时人工维护「节名 → 标准节类型」映射 |
| 表格跨页 / 并表复杂结构 | 表头重复、合并表错切 | 表头指纹合并重复表头；复杂并表标注「需人工核实」 |
| reranker 成本 | 显存/CPU 开销 | top-k=30 可接受；`RERANK_MODEL` 留空可关，回退 RRF |

---

## 10. 附录

### 10.1 参数表汇总

见 §5.7（合并了 ARCH §13.3 与本文新增项）。

### 10.2 示例数据流（「比亚迪 2024 研发费用率」）

1. **`rewrite_query`**：`rewritten_query = "比亚迪 2024 年研发费用率"`；`retrieval_plan = {tools: [...], concepts: ["研发费用率", "研发投入", "营业收入"], filters: {company: "比亚迪", year: 2024}, strategy: "auto"}`。
2. **`rag_retrieve`**：`query_build` 用 jieba + 术语表补充键词 → Stage 1：company+year 过滤后，chunk 索引（dense）与 section 索引（BM25）并行 top-50，RRF 融合 → Stage 2：候选按 `(doc_id, section_path)` 聚合出候选节（RAG_TOP_K_SECTIONS=5）→ SiliconFlow `/v1/rerank`（`bge-reranker-v2-m3`）重排（失败回退 RRF 序并跳过阈值）→ 返回 `RagChunk[]`（如 `[比亚迪2024年报 - 第46页 3.2 研发投入 · 表 12 研发投入情况]`、`... 第42页 3.1 营业收入` 等），每块自带 cite_ref。
3. **`integrate`**：与 `call_tools` 的结构化数据（若有）归一化为 Evidence，形成 claims（「2024 年研发费用率 = 研发投入 / 营业收入 ≈ X%」），挂 citations。（已实现：`tool_rag` 并行 + 证据合并，见 `RAG_INTEGRATION.md` §3。）
4. **`verify_reasonableness`**：核对单位/年份一致 → 通过。**（❌ 未实现：当前 synthesizer 一次性流式生成，无自检回环；合成失败转 fallback。）**
5. **`structure_output`**：输出 `StructuredAnswer`——`answer`（中文总结）+ `claims`（带证据）+ `citations`（内联标记 + reflist）+ `is_reliable=true`。（已实现为 `structured: dict`：answer/intent/strategy/sources/citations/claims/metadata。）

### 10.3 引用样例

**内联**：`...2024 年研发费用率约为 5.6%[比亚迪2024年报 - 第46页 3.2 研发投入]。`

**参考文献列表**：

```
[1] 比亚迪股份有限公司 2024 年年度报告，第 46 页，第三节 管理层讨论与分析 → 二、报告期内主要经营情况 → 3.2 研发投入 · 表 12 研发投入情况。
[2] 同上，第 42 页，第三节 管理层讨论与分析 → 二、报告期内主要经营情况 → 3.1 营业收入。
```

---

*本文档基于仓库现状与 [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md) 撰写；所有「现有」构件均已核对存在。新增组件（`demomcp/rag/` 报表子模块 + `demomcp/graph/`）与可选依赖（`pymupdf`/`bge-m3` 等）为前瞻设计，落地时再行实施。核心管线默认纯 Python，可离线运行。*

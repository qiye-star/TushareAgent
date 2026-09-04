# RAG 向量数据库结构与多公司数据划分方案

> 本文档是对 `demomcp/rag/` 现有向量/检索索引的一次系统性摸底 + 评审。
> 定位：**现状诊断 + 生产就绪度评估 + 设计建议**（分析/参考文档）。
> **不包含**分阶段落地路线图，**不改任何代码**；所有「现状」描述以源码为准，标注出处；「建议」一律明确为非现状。

---

## 1. 概述与定位

- **目标**：回答三个问题 —— ① 当前向量数据库结构是什么；② 该结构对生产环境是否完整、需要补什么；③ 若纳入多公司研报/财报/公告，数据应如何划分。
- **范围**：`demomcp/rag/`（财经知识库：解析/检索/引用/持久化）。
- **非目标**：不给出改造步骤清单，不改代码，不进主链路（`entry→agents→interfaces`）。

### 与既有文档的关系

| 文档 | 定位 |
|---|---|
| `docs/RAG_FINANCE.md` | 设计蓝图（目标态） |
| `docs/RAG_INTEGRATION.md` | 现状权威（系统架构 §、配置表） |
| `docs/ARCHITECTURE.md` | 全量系统架构 |
| **`docs/RAG_VECTORDB.md`（本文）** | 向量库**结构**现状 + 生产评估 + 多公司划分方案 |

凡与蓝图冲突处，本文档**以代码现状为准**（代码是唯一事实来源）。

---

## 2. 当前向量库结构

### 2.1 顶层架构

一个**混合 3 路索引**，文本与向量分离落盘：

| 路 | 索引类型 | 存储介质 | 键 |
|---|---|---|---|
| ① 稠密 chunk 向量 | dense（每块一个向量） | Milvus-Lite | `{doc_id}#{chunk_index}` |
| ② 节代表向量（语义） | dense（每节首 300 字符） | Milvus-Lite `rag_sections` | `{doc_id}#{idx}` |
| ③ 节级 BM25（词法） | BM25 / SQLite 实时打分 | SQLite `bm25_docs` | `section_id` |

- **向量**：Milvus-Lite（`IP`/内积，归一化后即点积），文本在 SQLite。
- **搜索时 join**：Milvus 命中只回 `doc_id`+`chunk_index`，文本/元数据从 SQLite `RelStore` 回拉（`store.py:290-297`）。
- 检索入口编排在 `HybridRetriever.retrieve`（`hybrid_retriever.py:48`），内部 `asyncio.to_thread` 跑同步检索（`hybrid_retriever.py:49`）。

### 2.2 物理落盘

根目录：`RAG_VECTOR_STORE_PATH`，默认 `PROJECT_ROOT/data/vectorstore`（`settings.py:64-66`）。

```
data/vectorstore/
├─ milvus.db/                # Milvus-Lite 目录（非单文件）
│  ├─ collections/rag_chunks/   # 含 schema.json / manifest.json / wal/ / partitions/.../*.parquet + *.vector.autoindex.idx
│  ├─ collections/rag_sections/
│  └─ LOCK                    # Milvus-Lite 单进程独占锁
└─ rag_rel.db                # SQLite（WAL：rag_rel.db-wal / -shm）
```

- `RelStore`：`sqlite3`，`PRAGMA journal_mode=WAL`（`persist.py:31`）。表名常量 `_REL_DB`/`_MILVUS_DB`（`persist.py:19-20`）。
- **应用自身不写 chunk parquet/json** —— 那些是 Milvus-Lite 内部产物。

**SQLite 四表结构**（`persist.py:34-49`）：

| 表 | 列 | 主键 |
|---|---|---|
| `chunks` | `doc_id, chunk_index, text, metadata_json` | `(doc_id, chunk_index)` |
| `sections` | `doc_id, idx, text, metadata_json` | `(doc_id, idx)` |
| `bm25_docs` | `section_id, doc_id, text, metadata_json, tf_json, doc_len` | `section_id` |
| `meta` | `key, value` | `key` |

### 2.3 索引 schema（Milvus）

两个 collection（`rag_chunks` / `rag_sections`）共用一套 schema，由 `_ensure_schema` 创建（`store.py:233-245`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(256) | 主键，=`{doc_id}#{chunk_index}`（`store.py:249`） |
| `vector` | FLOAT_VECTOR | dim = embedder（真实 1024 / hashing 256） |
| `doc_id` | VARCHAR(256) | 标量过滤用 |
| `chunk_index` | INT64 | section 用时存节序号 `idx` |
| `company` | VARCHAR(64) | 标量过滤用 |
| `year` | INT64 | 标量过滤用 |
| （动态字段） | — | `enable_dynamic_field=True` |

- 度量：`AUTOINDEX` + `IP`（`store.py:243-244`）。
- **全局单 collection，无按公司/文档类型/年份的分区**；隔离只靠标量字段 + 过滤表达式（`store.py:313-321`）。

### 2.4 数据契约与元数据清单

**文档级 `DocMeta`**（`schemas.py:19-32`）：

```
doc_id, title, company, company_code, year,
report_type:str = "annual_report",        # schemas.py:27
source_pdf, source_url, total_pages, parse_tool, ingested_at
```

**chunk 元数据** —— `_chunk_metadata`（`ingest.py:168-179`）：

```
doc_title, company, company_code, year, doc_id,
section_path(list), section_level(=len(section_path)), heading,
page_start, page_end, block_type, table_headers, table_caption,
position, ingested_at
```

**section 元数据** —— `ingest.py:86-92`：

```
doc_title, company, company_code, year, doc_id,
section_path, heading, page_start, page_end, block_type="section", section_id
```

**逐字段对比 → 关键缺口**：

| 字段 | chunk | section | 备注 |
|---|---|---|---|
| `section_id` | ✗ | ✓ | 仅 section 有（`SectionNode.id`） |
| `source_pdf`（来源 PDF 路径） | ✗ | ✗ | chunk/节不携带原始文件源 |
| `report_type` / `doc_type` | ✗ | ✗ | **DocMeta 有 `report_type` 但从未下渗** |
| `company_code` | ✓ | ✓ | 已带 |
| `ingested_at` | ✓ | ✗ | chunk 带，section 不带 |

**结论**：一个 chunk 能自证「公司/财年/章节/页码/块类型」，但**不能**自证「来自哪份 PDF、是何种文档类型」。

### 2.5 检索过滤面（现状）

`RagFilters`（`schemas.py:88-92`）**只有两个维度**：

```python
class RagFilters(BaseModel):
    company: str | None = None
    year: int | None = None
```

全链路同构：

| 位置 | 实现 |
|---|---|
| 存储层 match | `store.py:25-31`（内存 `_match`）、`store.py:313-321`（Milvus `_milvus_filter`）、`persist.py:182-187`（`_match_meta`） |
| 检索层 | `hybrid_retriever.py:182-183` `_filters_dict`、`:186-189` `_strict_filters`、`:192-195` `_query_prefix` |

- `_strict_filters`（`hybrid_retriever.py:186-189`）：**仅当** `rag_strict_scope=true` **且** company/year 至少一个已知时才应用过滤；否则返回 `None`（纯相关度兜底，跨公司隔离**静默关闭**）。
- `_query_prefix`（`hybrid_retriever.py:192-195`）：把 `{company}{year} ` 前缀拼进 dense query，与「向量化包含元数据」（`ingest.py:162-165`）对齐。

**strategy 收窄三路**（`hybrid_retriever.py:66-73`）：

| strategy | 参与路 |
|---|---|
| `factual` | 仅 chunk 稠密 ① |
| `structural` | 节级两路 ②③ + chunk ①（保表格覆盖） |
| `auto` | 三路全走 |

配合 `_filters_dict`，三路检索都吃同一份 `{company, year}` 过滤。

**公司名推断** —— `_COMPANY_ALIASES` 硬编码（`query_build.py:11-14`），目前只有：

```python
(("比亚迪","002594","byd","002594.sz"), "比亚迪"),
(("宁德时代","宁德","300750","catl","300750.sz"), "宁德时代"),
```

`infer_filters`（`query_build.py:17-29`）从查询子串匹配公司/年份。**没有 `company_code` 过滤**（`company_code` 只进元数据，不进 `RagFilters`）。

### 2.6 doc_id 生成与身份键

- 设计蓝图期望稳定键 `doc_id="fy{year}_{code}"`（`RAG_FINANCE.md:80`）—— **当前未实现**。
- 实际 `_doc_meta_from_path`（`runtime.py:24-49`）：`doc_id = pdf.stem`（文件名 stem，`runtime.py:38`），`title = pdf.stem`，`company_code = ""`（`runtime.py:41`），`report_type="annual_report"`（`runtime.py:43`）。
- 即 **doc_id 是不透明的文件名**，不编码公司/年份/类型；`company`/`year` 靠启发式另推（`runtime.py:29-36`：优先父目录 `data/corpus/{company}/{year}/`，否则文件名 `：/年` 前缀）。

---

## 3. 生产就绪度评估

### 3.1 已满足的点

1. **多公司可共存**：一个索引可容纳多公司多财年；`company`/`year` 以标量列 + 元数据落库。
2. **已知公司时跨公司隔离**：`RAG_STRICT_SCOPE=true` + 别名表命中时，能硬过滤避免「比亚迪研发」命中宁德时代。
3. **chunk 自证出处**：`RagChunk.metadata` 携带 `doc_id/company/year/section_path/page/heading/block_type`。
4. **幂等重跑**：`ingest()` 先 `delete_doc(doc_id)` 再 `add_doc`（`ingest.py:133-136`），同一 doc 重跑等于全量重建。
5. **进程级旁路**：`RAG_HTTP_URL` 指向运行中 web `/api/rag/retrieve` 时走 `HttpRetriever`，不打开 Milvus（规避单写锁）。

### 3.2 缺口清单（分级）

**P0 —— 阻断多公司/多类型生产：**

| 缺口 | 证据 | 影响 |
|---|---|---|
| 无摄取注册表 | 只有 `chunks/sections/bm25_docs/meta` 四表，无 per-doc 记录；`meta` 仅存 `bm25_k1/bm25_df/bm25_total_len/dim/model`（`persist.py:103-117, 222-228`） | 无法追踪哪些 doc 已索引、无法增量更新、无法孤儿清理/版本管理 |
| `has_index` 校验不足 | `persist.py:211-219` 只判 `milvus.db`+`rag_rel.db` 存在且 `chunks>0` | 无法识别「BM25 组件未持久化」的残缺索引 → 节级词法召回**静默归零**（`persist.py:148-149` 空则直接 `[]`）；调研时刻已入库数据即观察到 `bm25_docs` 为空而向量数据仍在 |
| `doc_id` 非稳定键 | `runtime.py:38` 用文件名 stem | 多公司同名/同名年份文件冲突、无法用 `doc_id` 表达「公司×类型×区间」；也无法做删除对齐 |
| 公司别名**硬编码 2 家** | `query_build.py:11-14` | 第 3+ 家公司不生成过滤 → `_strict_filters` 返回 `None`，跨公司隔离**静默失效**（第三方公司内容可混入） |

**P1 —— 维度缺失，限制生产级查询：**

| 缺口 | 证据 |
|---|---|
| `report_type` 惰性、无 `doc_type` 概念 | `DocMeta.report_type`（`schemas.py:27`，`runtime.py:43`）从未写入 chunk/section 元数据；`_chunk_metadata`/`sec_meta` 均无该字段 |
| 仅年报、无研报/公告管线 | `runtime.py:52-57 _is_report_name` 只收 `年度报告/年报`/「4位年份+报告」；解析/章节逻辑面向年报（编/节编号、MD&A） |
| 无日期区间过滤 | `infer_filters` 只抓单一 4 位年份（`query_build.py:26-28`）；`year` 是 `int` 精确匹配，无区间语义 |
| 无 `company_code` 过滤 | `RagFilters` 只含 `company`；ticker（如 `002594.SZ`）不构成结构化过滤 |
| 无节类型过滤 | `RAG_FINANCE.md §5.4` 提到的 `section_type` 过滤**未实现**；section 仅作内部 `(doc_id, section_path)` 聚合（`hybrid_retriever.py:79-89`），不能作为查询作用域 |
| schema 漂移无 ALTER | `create_all` 不 ALTER 既有表；加字段（如真 `doc_type`/区间/节类型）需重建库 |
| Milvus-Lite 单写者锁 | `milvus.db/LOCK`；多 worker 写/重建需错开 |

**P2 —— 运维/扩展优化：**

| 缺口 | 说明 |
|---|---|
| 无摄取监控/自动重建 | 增删 PDF 需手动重跑 `dba_rag.py`；无增量 watch |
| 无 schema 版本元数据 | `meta` 表无库 schema 版本；无法审计迁移 |
| 索引无 partition 键 | 全局 collection，扩展靠元数据过滤；规模/冷热分离受限 |

### 3.3 结论

- 对**单公司（或已知可控几家）年报** demo：结构干净、链路完整、可检索、可引用。
- 对**多公司 × 多文档类型（研报/财报/公告）** 生产：**不完整** —— 缺文档类型维度、缺稳定身份键、缺摄取注册表、缺通用公司名解析、缺日期区间；且 `has_index` 校验不足会让「看起来可用」的索引在词法路上静默失效。需按第 4 节补数据模型与治理。

---

## 4. 多公司 / 多文档类型数据划分方案（单索引 + 元数据过滤增强）

> 路线按约定：**单索引 + 元数据过滤增强**（契合 `RAG_FINANCE.md` 既有设计意图）。以下全是**建议**，非现状。

### 4.1 原则

- **索引不分区、目录分区**：磁盘侧按 `data/corpus/{company}/{doc_type}/{year}/` 组织；索引保持**单一混合库**，隔离与筛选全部靠元数据过滤。
- **文档唯一化**：`文档 = (公司 × 文档类型 × 区间)`——一份研报、一份年报、一份公告都是独立文档，各自独立 `doc_id`。

```
data/corpus/
├─ 比亚迪/                        # company
│  ├─ annual_report/2024/…        # doc_type / year
│  ├─ research_report/2024-06/…
│  └─ announcement/2024-05-01/…
└─ 宁德时代/
   └─ …
```

### 4.2 数据模型补全（字段级建议）

**`DocMeta` 增补**：

| 字段 | 建议 | 现状 |
|---|---|---|
| `doc_type` | `annual_report` / `research_report` / `announcement` | 有 `report_type` 但惰性 |
| `pub_date` / `interval` | 发布时间或区间（研报/公告用） | 无 |
| `doc_id` 生成 | 稳定键 `fy{year}_{code}_{typename}`（对齐 `RAG_FINANCE.md:80`，并加类型） | 文件名 stem |

**chunk / section 元数据统一注入**（当前 `_chunk_metadata` `ingest.py:168-179`、`sec_meta` `ingest.py:86-92` 均缺）：

```
doc_type, company_code, source_pdf, published_at
```

> 这样 chunk/节即能自证「公司 × 类型 × 区间 × 来源 PDF」，完成 §2.4 的溯源断链。

### 4.3 过滤维度扩面

`RagFilters` 从 `{company, year}` 扩展（`schemas.py:88-92`）：

```python
class RagFilters(BaseModel):
    company: str | None
    company_code: str | None        # 新增：ticker 级精确过滤
    doc_type: str | None            # 新增：研报/年报/公告
    points: tuple[str, str] | None  # 新增：发布/财年区间（或 year_start/year_end）
    section_type: str | None        # 新增（可选，对应 §5.4 蓝图）
```

对应改动：`_filters_dict` / `_milvus_filter` / `_match` / `_match_meta` 同步支持新维度；`_strict_filters` 的「已知维度」判定逻辑改为对任一非空维度生效。

**公司名解析**（解决「第三方公司隔离被关闭」）：

- 现状 `_COMPANY_ALIASES` 硬编码（`query_build.py:11-14`）→ 改为**配置驱动词表**（YAML/DB）。
- 兜底：命中不了词表时，用 Tushare `stock_basic` 接口把口语名 ↔ `ts_code` ↔ `company` 归一；归一失败时保持「不硬过滤但返回 `degraded` 标注」，而非静默混入。

### 4.4 摄取治理

新增**摄取注册表**（建议独立表或复用 `meta` 加命名空间），字段：

```sql
ingest_registry(doc_id PK, company, company_code, doc_type, year,
                version, source_checksum, ingested_at, state, validator)
```

支撑语义：

- **增量**：只更新 `source_checksum` 变化的文档。
- **更新**：同 `doc_id` 覆盖（现为 delete-then-add，`ingest.py:133-136`）。
- **孤儿删除**：`doc_id` 不在注册表/语料目录 → 清理（现 `delete_doc` 需显式传，无追踪）。
- **幂等/可审计**：每 doc 摄取/状态可查询。

### 4.5 远期扩展触发条件（仅条件，不作为当前落地）

当出现以下任一情形，再评估引入 **Milvus partition key**（如按 `doc_type` 或 `company`）或 **按公司分 collection**：

- 单 collection 规模超十万级向量且检索延迟上升；
- 高频并发写（Milvus-Lite 单写者锁成为瓶颈）；
- 冷热分离 / 按公司治理（某公司数据回滚不影响其它）。

触发前，**元数据过滤增强**是低风险前提，可平移到 partition 之上。

### 4.6 与既有蓝图的对齐

- 承接 `RAG_FINANCE.md §2.1`「公司 × 财年唯一化」、`§2.2`「公司名 ↔ 股票代码归一化」、`§5.4`「元数据过滤 + 可选 section_type」、`§8`「跨公司隔离负例」。
- 本方案是这些蓝图的**补全**（补 `doc_type`/`company_code`/区间/注册表，解「硬编码别名表」与「bm25 持久化空」两类现状硬伤），不改变 RAG 分层与「单索引 + 元数据隔离」的总体走向。

---

## 5. 建议改动清单（仅建议，不实施）

> 只作设计输入，标注优先级；**不在此文档内改代码**。涉及文件以当前实现为准。

| 现状 | 建议 | 优先级 | 涉及文件/字段 |
|---|---|---|---|
| 无摄取注册表 | 新增 `ingest_registry` 表 + 增量/删除/校验 | P0 | `persist.py`、`runtime.py` |
| `has_index` 只判存在+chunks>0 | 校验 `bm25_docs>0` + schema 版本 | P0 | `persist.py:211-219` |
| `doc_id=文件名 stem` | 稳定键 `fy{year}_{code}_{typename}` | P0 | `runtime.py:24-49`、`schemas.py` |
| 公司别名硬编码 2 家 | 配置驱动词表 + `stock_basic` 兜底 | P0 | `query_build.py:11-29` |
| `report_type` 惰性 | 下渗 `doc_type` 到 chunk/section 元数据 | P1 | `ingest.py:86-92,168-179` |
| `RagFilters` 仅 company+year | 增 `doc_type`/`company_code`/区间/`section_type` | P1 | `schemas.py`、`store.py`、`persist.py`、`hybrid_retriever.py` |
| 无日期区间 | `year` 支持区间或 `pub_date` | P1 | `query_build.py`、`schemas.py` |
| 无节类型过滤 | 实现 `section_type` 过滤（蓝图 §5.4） | P1 | `schemas.py`、`hybrid_retriever.py` |
| schema 漂移无 ALTER | 库 schema 版本 + 迁移策略 | P1 | `persist.py` |
| 无摄取监控/自动重建 | 增量 watch/定时重建 | P2 | `runtime.py`、`scripts/dba_rag.py` |
| 索引无 partition 键 | 量级/并发触发后按类型/公司分 partition | P2 | `store.py` |

---

## 6. 附录 A：证据引用表

| 结论 | 出处 |
|---|---|
| 三路混合索引：chunk dense → Milvus / section 代表向量 → Milvus / section BM25 → SQLite | `hybrid_retriever.py:1-6`、`ingest.py:22` |
| Milvus 字段 `id/vector/doc_id/chunk_index/company/year` + dynamic、`IP` 度量 | `store.py:233-245` |
| 主键 `{doc_id}#{chunk_index}` | `store.py:249` |
| SQLite 四表 `chunks/sections/bm25_docs/meta` | `persist.py:34-49` |
| `DocMeta` 含 `report_type` 默认 `annual_report` | `schemas.py:19-32`（`:27`） |
| `RagFilters` 仅 `company+year` | `schemas.py:88-92` |
| chunk 元数据字段（缺 `report_type`/`source_pdf`） | `ingest.py:168-179` |
| section 元数据字段（缺 `report_type`/`source_pdf`，有 `section_id`） | `ingest.py:86-92` |
| embed 前缀 `{company}{year} {path} {heading}` | `ingest.py:162-165` |
| `_strict_filters` 无已知维度 → 无过滤 | `hybrid_retriever.py:186-189` |
| `_COMPANY_ALIASES` 仅比亚迪/宁德时代 | `query_build.py:11-14` |
| `infer_filters` 只抓单一 4 位年份 | `query_build.py:17-29` |
| strategy 收窄三路 | `hybrid_retriever.py:66-73` |
| 按 `(doc_id, section_path)` 聚合候选节 | `hybrid_retriever.py:79-89` |
| `doc_id=文件名 stem`、`company_code=""`、`report_type="annual_report"` | `runtime.py:24-49` |
| 只收年报文件名 | `runtime.py:52-57` |
| `has_index` 只判存在+chunks>0 | `persist.py:211-219` |
| `search_bm25` 空表直接返回 `[]`（节词法静默失效） | `persist.py:148-149` |
| `save_bm25` 生命周期 / `meta` 存 `bm25_k1/bm25_df/dim/model` | `persist.py:103-117, 222-228` |
| 持久化路径常量 `_REL_DB`/`_MILVUS_DB` | `persist.py:19-20` |
| RAG 配置面（路径/维度/开关） | `settings.py:62-94` |

---

*复核注：本文所有「现状」均以上述源码行为为准；`bm25_docs` 空表为调研时点对已入库数据的一次观察，其本质反映第 3.2 P0「`has_index` 校验不足」的结构性风险。*

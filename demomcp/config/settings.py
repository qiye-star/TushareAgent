"""配置层：从 demo-mcp/.env 读 DeepSeek / MCP / agent / 数据库配置（pydantic-settings）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from demomcp.config.env import PROJECT_ROOT

DEFAULT_SYSTEM_PROMPT = (
    "你是一名资深投研分析师，服务于 A 股市场：具备扎实的财务与估值分析功底，覆盖个股/行业/产业链的"
    "高频跟踪、深度剖析、对比分析与财务/估值解读，凡事讲证据、给结论、不臆测；"
    "也能按需生成结构化投研报告（如产业链高频跟踪快报）。"
    "可查询任意上交所/深交所上市公司、指数及其它金融数据。"
    "先用 list_apis 按关键词浏览可用接口，再用 get_api_info 查看某接口的必填/可选参数与返回列，"
    "然后调用 query(api_name, params, fields) 或对应接口工具取数。"
    "标的用合法 ts_code（如 600519.SH、000001.SZ）；不确定代码时可借助 stock_basic 等接口查询。"
    "日期支持多种格式与相对词（如 2026-08-01、20260801、近一年、今天）；区间行情默认前复权(qfq)。"
    "工具返回 {code, msg, row_count, data}，code 非 0（如权限/积分不足、接口下线）是业务结果而非崩溃，"
    "请如实转述 msg 给用户并说明，而不是编造数字；取到数据后整理成结构清晰、可溯源的投研结论，给出关键数字。"
)

DEFAULT_DISCLAIMER = "以上内容基于公开数据整理，仅供研究参考，不构成投资建议。"

# 万得（Wind）使用约定 —— 装配万得时追加进 system_prompt，教 LLM 用对懒发现三件套 + Wind 代码/自然语言/限流等约定。
WIND_USAGE_GUIDE = (
    "已配置万得(Wind)数据源，可与 Tushare 互补；其工具通过三个发现工具懒加载使用（不会一次性列出全部接口）：\n"
    "1) wind_list_apis(keyword?) —— 按关键词（如 行情/财务/公告/新闻/宏观/基金/债券）浏览万得可用接口名与简介；\n"
    "2) wind_get_api_info(api_name) —— 查看某接口（wind_list_apis 返回的 name，如 wind_get_stock_quote）的完整参数与返回说明；\n"
    "3) wind_query(api_name, params) —— 按上一步确认好的参数调用该接口取数。\n"
    "拿不准该用哪个万得接口时先 wind_list_apis 再 wind_get_api_info，不要凭猜测直接 wind_query。\n"
    "以下是通用背景知识，具体参数名以 wind_get_api_info 返回为准：\n"
    "- 代码用 Wind 格式：A股 600519.SH / 000001.SZ，港股 .HK，美股 .O，基金/ETF .OF（末尾带交易所后缀）；"
    "不确定标的先用 wind_list_apis(\"search\") 找到对应的搜索接口查询，不要自行猜交易所后缀或把名称转成代码。\n"
    "- 财务/公告/新闻/宏观类接口通常要求**自然语言** query/question 参数，而非结构化字段。\n"
    "- 行情/价格指标类接口支持用逗号合并多个 Wind 代码（单次 ≤50）。\n"
    "- 跨标的聚合/排名/复合指标才用 analytics_data 域接口；**不要**用它拉批量行情/筛选标的——"
    "那更耗积分；批量场景改用对应领域的 search 类或逐标的专用行情接口。\n"
    "- 标的识别失败或不确定 Wind 代码时向用户询问准确全称/代码，不要自行猜测。\n"
    "- Wind 返回以服务端为准；若返回 OUT_OF_SCOPE / 限流 / 后端错误，如实转述给用户，不要编造数字。"
)

# 同花顺 iFind 使用约定 —— 装配 iFind 时追加进 system_prompt（判定见 agents/agent.py::base_system_for）。
# 核心要讲清的是：iFind 的参数是**一句自然语言**，不是结构化字段——不说的话 LLM 会按 Tushare 的
# 习惯传 ts_code/start_date，然后每一次都取不到数。
IFIND_USAGE_GUIDE = (
    "已配置同花顺(iFind)数据源，可与 Tushare/万得互补；同样通过三个发现工具懒加载使用：\n"
    "1) ifind_list_apis(keyword?) —— 按关键词（如 选股/财务/股东/风险/ESG/基金/宏观/新闻/公告/债券/港美股/指数/板块）浏览接口；\n"
    "2) ifind_get_api_info(api_name) —— 查看某接口（如 ifind_get_stock_financials）的完整参数与返回说明；\n"
    "3) ifind_query(api_name, params) —— 按上一步确认好的参数调用该接口取数。\n"
    "拿不准用哪个接口时先 ifind_list_apis 再 ifind_get_api_info，不要凭猜测直接 ifind_query。\n"
    "以下是通用背景知识，具体参数名以 ifind_get_api_info 返回为准：\n"
    "- **iFind 绝大多数接口只接受一个 `query` 参数，值是一句自然语言**，把标的、指标、时间写在同一句话里，"
    "例如 query=\"科大讯飞2025年三季度的ROE\"、query=\"沪深300过去10个交易日的涨跌幅和收盘点数\"。"
    "**不要**拆成 ts_code/start_date/end_date 这类结构化字段传给它——那是 Tushare 的风格，iFind 收不到。\n"
    "- 标的可直接写中文简称（如 茅台、格力电器），不必先转成代码；多主体多指标时单次不超过 5 个。\n"
    "- 新闻/公告类接口（ifind_search_news / ifind_search_notice / ifind_search_trending_news）另有"
    "time_start/time_end（YYYY-MM-DD）与 size 参数，返回的是语义检索到的相关段落而非全文。\n"
    "- iFind 按套餐限并发，返回「并发超限」属正常限流：如实转述并稍后重试，不要编造数字。\n"
    "- 鉴权失败/接口无权限时同样如实转述，并可改用 Tushare 或万得的等价接口。"
)

# 免费兜底源（AkShare / 财经新闻）使用约定 —— 这两个源的工具名**没有前缀**，且代码格式与
# Tushare 不同（6 位裸代码，不带 .SH/.SZ 后缀），不讲清楚 LLM 会传 600519.SH 然后收到 not found。
FREE_SOURCE_USAGE_GUIDE = (
    "已配置免费兜底数据源（AkShare 行情/财报 + 财经新闻，数据来自东方财富）。它们的工具名没有前缀，"
    "直接按名调用即可，用途是在 Tushare/万得取不到数（无权限、接口下线）时交叉验证或兜底：\n"
    "- **代码格式是 6 位裸代码，不带交易所后缀**：用 600519，**不要**用 600519.SH——传后缀会查不到。\n"
    "- 日期参数用 YYYYMMDD（如 20260101）。\n"
    "- 行情/资料：search_stock(keyword) 搜标的、get_quote(ticker) 实时快照、"
    "get_historical_data(ticker, start_date, end_date, frequency) 历史K线、get_stock_info(ticker) 公司资料、"
    "get_market_overview() 涨跌幅与成交额榜、get_industry_stocks(industry) 行业成分股、"
    "get_index_data(index_code) 指数行情、get_fund_data(fund_code) 场内基金。\n"
    "- 财报：get_financials(ticker, statement_type, period)，statement_type ∈ income/balance/cashflow。"
    "注意它的口径来自网页接口，**不要**把它的数与 Tushare 财务接口的数放进同一张表对比。\n"
    "- 新闻：get_stock_news(ticker) 个股新闻、get_market_headlines(top_n) 市场头条——"
    "Tushare 官方的 news 接口通常无权限，需要新闻时优先用这两个。\n"
    "- 这些源来自网页抓取，接口可能突然失效并返回 {\"error\": ...}：如实转述，不要编造数字。"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # 允许用字段名（且保留别名）构造，便于测试传 max_iterations 等
    )

    # DeepSeek（OpenAI 兼容端点）
    ds_api_key: str = ""                    # DS_API_KEY
    ds_base_url: str = "https://api.deepseek.com"   # DS_BASE_URL
    ds_model: str = "deepseek-chat"         # DS_MODEL
    ds_max_tokens: int = 8192               # DS_MAX_TOKENS
    ds_streaming: bool = True               # DS_STREAMING

    # Agent
    system_prompt: str = Field(
        default=DEFAULT_SYSTEM_PROMPT, alias="DEMO_SYSTEM_PROMPT"
    )
    max_iterations: int = Field(default=10, alias="DEMO_MAX_ITERATIONS")
    disclaimer: str = Field(default=DEFAULT_DISCLAIMER, alias="DISCLAIMER")

    # 会话历史库（留空 → 回落 sqlite+aiosqlite:///<PROJECT_ROOT>/demo.db）
    demo_database_url: str = ""             # DEMO_DATABASE_URL

    # 日志：对话以文本文件记录（UTF-8 + 按大小轮转）
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")            # DEBUG / INFO / ...
    log_file: str = Field(default=str(PROJECT_ROOT / "logs" / "chat.log"), alias="CHAT_LOG_PATH")
    log_max_bytes: int = Field(default=5 * 1024 * 1024, alias="LOG_MAX_BYTES")  # 单文件上限，触发轮转
    log_backup_count: int = Field(default=3, alias="LOG_BACKUP_COUNT")   # 轮转保留份数

    # MCP 网关（独立部署，见仓库根 mcp_gateway/）：demomcp **唯一**的取数入口——它只是网关的一个
    # 纯 MCP 客户端，进程内不再直连任何数据源。上游源（Tushare 官方 MCP / 万得 Wind / 未来更多）的
    # 地址与 key 只配在 mcp_gateway/.env 一处，并在网关里按源开关，这里既读不到也不需要。
    # 下面这几个 mcp_* 描述的是「demomcp → 网关」这一条连接。
    mcp_gateway_url: str = Field(default="http://127.0.0.1:8766/mcp", alias="MCP_GATEWAY_URL")
    mcp_gateway_admin_url: str = Field(default="", alias="MCP_GATEWAY_ADMIN_URL")  # 管理 REST base；空则从上面推导（同 host:port，去掉 /mcp 路径）
    mcp_timeout: float = Field(default=30.0, alias="DEMO_MCP_TIMEOUT")   # 单次工具调用读超时（秒）
    mcp_retries: int = Field(default=2, alias="DEMO_MCP_RETRIES")        # 工具调用重试次数
    mcp_keepalive_interval: float = Field(default=45.0, alias="DEMO_MCP_KEEPALIVE")  # 保活 ping 间隔（秒）；0=关闭保活
    tool_pool_hot_start: bool = Field(default=True, alias="TOOL_POOL_HOT_START")  # web 启动即连网关（热启动；网关没起来就退避重试），false=首个 /chat 冷连

    # 动态工具目录：per-query 只把相关子集喂给 LLM（meta 发现工具恒在），可用性探测默认关。
    tool_max_revealed: int = Field(default=12, alias="TOOL_MAX_REVEALED")       # 每轮揭示给 LLM 的非 meta 工具上限
    tool_meta_always: bool = Field(default=True, alias="TOOL_META_ALWAYS")       # 恒保留 list_apis/get_api_info/query/stock_basic
    tool_probe_enabled: bool = Field(default=False, alias="TOOL_PROBE_ENABLED")  # 启动时实时探测可用性（默认关，避免烧积分）
    tool_probe_cache_path: str = Field(
        default=str(PROJECT_ROOT / "data" / "tool_catalog.json"), alias="TOOL_PROBE_CACHE_PATH"
    )  # 可用性缓存：有则加载并剔除 blocked/down
    tool_probe_concurrency: int = Field(default=4, alias="TOOL_PROBE_CONCURRENCY")  # 探测并发

    # 注：所有上游源（Tushare / 万得 / 同花顺 iFind / AkShare / 财经新闻）的地址与 key 都在
    # mcp_gateway/.env（网关是唯一持有上游源连接的进程）。demomcp 是否给 LLM 讲某个源的用法，
    # 按「本轮工具清单里有没有它的工具」自动判断，见 agents/agent.py::base_system_for
    # （跟着网关的按源开关自动变化，无需 demomcp 知道任何源的配置）。

    # RAG 财报知识库（离线优先：默认纯 Python；RAG_USE_REAL=true 且安装 rag-full 才加载真实后端）
    rag_use_real: bool = Field(default=False, alias="RAG_USE_REAL")                 # 是否走真实后端（bge-m3/faiss/pymupdf）
    rag_vector_store_path: str = Field(
        default=str(PROJECT_ROOT / "data" / "vectorstore"), alias="RAG_VECTOR_STORE_PATH"
    )  # RAG 持久化目录：Milvus‑Lite 向量库 + SQLite 关系库（rag_rel.db）落盘于此
    rag_embedding_model: str = Field(default="BAAI/bge-m3", alias="RAG_EMBEDDING_MODEL")  # 默认真实向量模型；"hashing"=测试兜底
    rag_embedding_api_base: str = Field(default="https://api.siliconflow.cn/v1", alias="RAG_EMBEDDING_API_BASE")  # OpenAI 兼容嵌入服务 base_url（SiliconFlow 默认）
    rag_embedding_api_key: str = Field(default="", alias="RAG_EMBEDDING_API_KEY")    # 嵌入服务 api_key；为空则回退 hashing
    rag_embedding_dim: int = Field(default=256, alias="RAG_EMBEDDING_DIM")          # bge-m3 API 维度由响应自动发现；hashing 用此值
    rag_captioner: str = Field(default="deepseek-v4-flash-vision-exp", alias="RAG_CAPTIONER")  # 多模态模型名；无 DS_API_KEY 回退 Noop
    rag_rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RAG_RERANK_MODEL")  # 重排模型（SiliconFlow 服务端）；无 key 回退 Noop
    rag_top_k: int = Field(default=5, alias="RAG_TOP_K")
    rag_candidate_k: int = Field(default=50, alias="RAG_CANDIDATE_K")  # 三路检索召回（recall-first）
    rag_top_k_sections: int = Field(default=5, alias="RAG_TOP_K_SECTIONS")  # 候选节数（recall-first，避免答案落在第 4+ 节被截）
    rag_score_threshold: float = Field(default=0.3, alias="RAG_SCORE_THRESHOLD")
    rag_rerank_threshold: float = Field(default=0.2, alias="RAG_RERANK_THRESHOLD")  # bge-reranker-v2-m3 对真实 MD&A 文本分偏低，0.2 提召回
    rag_rerank_candidates: int = Field(default=30, alias="RAG_RERANK_CANDIDATES")  # 重排候选池上限（recall-first 放宽；实际由 per_section 驱动）
    rag_fin_dense_chunk: int = Field(default=400, alias="RAG_FIN_DENSE_CHUNK")
    rag_fin_dense_overlap: int = Field(default=80, alias="RAG_FIN_DENSE_OVERLAP")
    rag_section_max_chars: int = Field(default=8000, alias="RAG_SECTION_MAX_CHARS")
    rag_table_rows_per_chunk: int = Field(default=8, alias="RAG_TABLE_ROWS_PER_CHUNK")
    rag_hybrid_dense_weight: float = Field(default=0.6, alias="RAG_HYBRID_DENSE_WEIGHT")  # bm25 = 1 - dense；当前走 RRF 作预留
    rag_strict_scope: bool = Field(default=True, alias="RAG_STRICT_SCOPE")
    rag_hyde: bool = Field(default=False, alias="RAG_HYDE")
    rag_strategy: Literal["structural", "factual", "auto"] = Field(default="auto", alias="RAG_STRATEGY")
    rag_table_engine: Literal["pymupdf", "camelot"] = Field(default="pymupdf", alias="RAG_TABLE_ENGINE")
    rag_rrf_k: int = Field(default=60, alias="RAG_RRF_K")
    rag_bm25_k1: float = Field(default=1.5, alias="RAG_BM25_K1")
    rag_bm25_b: float = Field(default=0.75, alias="RAG_BM25_B")
    rag_corpus_dir: str = Field(default="", alias="RAG_CORPUS_DIR")  # 可选年报 PDF 目录；启动时 ingest 建索引；空则检索为空
    rag_http_url: str = Field(default="", alias="RAG_HTTP_URL")     # 非空 → agent 经 HTTP 检索（web /api/rag/retrieve），不再本地打开 Milvus
    rag_http_timeout: float = Field(default=20.0, alias="RAG_HTTP_TIMEOUT")  # HTTP 检索超时（秒）
    rag_http_token: str = Field(default="", alias="RAG_HTTP_TOKEN")  # 可选 Bearer token（内部服务鉴权）

    # 快报（quickreport）：独立于 Agent 主链路的确定性取数+计算管线（六段式 AI 算力产业链高频跟踪快报）
    quickreport_enabled: bool = Field(default=True, alias="QUICKREPORT_ENABLED")      # 总开关
    quickreport_auto: bool = Field(default=True, alias="QUICKREPORT_AUTO")           # lifespan 每日 08:30 定时任务（测试设 false，替身 TOOL_POOL_HOT_START 模式）
    quickreport_dir: str = Field(
        default=str(PROJECT_ROOT / "data" / "quickreport"), alias="QUICKREPORT_DIR"
    )  # watchlist.json / latest.json / last_error.json 所在目录
    quickreport_stage_timeout: float = Field(default=60.0, alias="QUICKREPORT_STAGE_TIMEOUT")  # 单段取数上限（秒）
    quickreport_concurrency: int = Field(default=4, alias="QUICKREPORT_CONCURRENCY")  # MCP 并行调用上限

    # 报告 skill 技能库（claude-for vendored 语料 → graph/skills.py 的注册表）
    skill_library_enabled: bool = Field(default=True, alias="SKILL_LIBRARY_ENABLED")   # 逃生开关：false 只剩内置 skill
    skill_library_dir: str = Field(default="", alias="SKILL_LIBRARY_DIR")              # 空 = 包内 demomcp/skill_library/claude-for

    @property
    def is_configured(self) -> bool:
        return bool(self.ds_api_key)

    @property
    def effective_database_url(self) -> str:
        """落到 demo-mcp 的默认 SQLite 路径（CWD 无关），或用户显式配置的 DEMO_DATABASE_URL。"""
        return self.demo_database_url or f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"

    @property
    def effective_mcp_gateway_admin_url(self) -> str:
        """网关管理 REST 的 base url：MCP_GATEWAY_ADMIN_URL 显式配置优先，否则从 mcp_gateway_url
        去掉路径部分推出（网关的 /mcp 与 /admin 就设计成同端口，见 mcp_gateway/app.py）；
        mcp_gateway_url 为空（未启用网关模式）时返回空字符串。"""
        if self.mcp_gateway_admin_url:
            return self.mcp_gateway_admin_url.rstrip("/")
        if not self.mcp_gateway_url:
            return ""
        from urllib.parse import urlsplit

        parts = urlsplit(self.mcp_gateway_url)
        return f"{parts.scheme}://{parts.netloc}"

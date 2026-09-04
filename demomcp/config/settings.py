"""配置层：从 demo-mcp/.env 读 DeepSeek / MCP / agent / 数据库配置（pydantic-settings）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from demomcp.config.env import PROJECT_ROOT

DEFAULT_SYSTEM_PROMPT = (
    "你是面向 A 股市场的投研助手，覆盖个股/行业/产业链的高频跟踪、深度剖析、对比分析与财务/估值解读；"
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

# 万得（Wind）使用约定 —— 装配万得时追加进 system_prompt，教 LLM 用对 Wind 代码/自然语言工具/限流等约定。
WIND_USAGE_GUIDE = (
    "已配置万得(Wind)数据源，其工具带 wind_ 前缀（如 wind_get_stock_quote、wind_get_stock_kline、"
    "wind_get_company_announcements），可与 Tushare 互补。使用约定：\n"
    "- 代码用 Wind 格式：A股 600519.SH / 000001.SZ，港股 .HK，美股 .O，基金/ETF .OF（末尾带交易所后缀）；"
    "不确定标的先用 wind_search_stocks 查询。\n"
    "- 财务/公告/新闻/宏观类工具（wind_get_stock_fundamentals、wind_get_company_announcements、"
    "wind_get_financial_news、wind_query_economic_indicator_data 等）用**自然语言** query/question，而非结构化参数。\n"
    "- 行情/价格指标工具支持用逗号合并多个 Wind 代码（单次 ≤50）。\n"
    "- 跨标的聚合/排名/复合指标才用 wind_get_financial_data(analytics_data)；**不要**用它拉批量行情/筛选标的——"
    "那更耗积分；批量用对应领域的 search_* 或逐标的专用行情工具。\n"
    "- 标的识别失败或不确定 Wind 代码时向用户询问准确全称/代码，不要自行猜交易所后缀或把名称转成代码。\n"
    "- Wind 返回以服务端为准；若返回 OUT_OF_SCOPE / 限流 / 后端错误，如实转述给用户，不要编造数字。"
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

    # MCP：经 TUSHARE_MCP_URL 连接 Tushare 官方 MCP（token 放 URL query，由 .env 提供完整地址）；
    # 内置 mcp_server/（本地代理→每接口工具）默认停用，仅作后备。
    tushare_mcp_url: str = "https://api.tushare.pro/mcp/"  # TUSHARE_MCP_URL（.env 提供 https://api.tushare.pro/mcp/?token=...）
    mcp_timeout: float = Field(default=30.0, alias="DEMO_MCP_TIMEOUT")   # 单次工具调用读超时（秒）
    mcp_retries: int = Field(default=2, alias="DEMO_MCP_RETRIES")        # 工具调用重试次数

    # 动态工具目录：per-query 只把相关子集喂给 LLM（meta 发现工具恒在），可用性探测默认关。
    tool_max_revealed: int = Field(default=12, alias="TOOL_MAX_REVEALED")       # 每轮揭示给 LLM 的非 meta 工具上限
    tool_meta_always: bool = Field(default=True, alias="TOOL_META_ALWAYS")       # 恒保留 list_apis/get_api_info/query/stock_basic
    tool_probe_enabled: bool = Field(default=False, alias="TOOL_PROBE_ENABLED")  # 启动时实时探测可用性（默认关，避免烧积分）
    tool_probe_cache_path: str = Field(
        default=str(PROJECT_ROOT / "data" / "tool_catalog.json"), alias="TOOL_PROBE_CACHE_PATH"
    )  # 可用性缓存：有则加载并剔除 blocked/down
    tool_probe_concurrency: int = Field(default=4, alias="TOOL_PROBE_CONCURRENCY")  # 探测并发

    # 万得（Wind）：可选的第二数据源。WIND_API_KEY 为空或 WIND_ENABLED=false ⇒ 仅 Tushare（行为不变）。
    wind_api_key: str = Field(default="", alias="WIND_API_KEY")   # WIND_API_KEY（aifinmarket.wind.com.cn 获取）
    wind_enabled: bool = Field(default=True, alias="WIND_ENABLED")  # WIND_ENABLED（有 key 时是否并入万得）

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

    @property
    def is_configured(self) -> bool:
        return bool(self.ds_api_key)

    @property
    def wind_configured(self) -> bool:
        """万得是否装配：WIND_API_KEY 有效且 WIND_ENABLED=true。"""
        return self.wind_enabled and bool(self.wind_api_key)

    @property
    def effective_system_prompt(self) -> str:
        """基础 system_prompt 加万得使用约定：装配万得时追加 WIND_USAGE_GUIDE，教 LLM 用对 Wind 代码/自然语言工具。"""
        base = self.system_prompt
        if self.wind_configured:
            base += "\n\n" + WIND_USAGE_GUIDE
        return base

    @property
    def effective_database_url(self) -> str:
        """落到 demo-mcp 的默认 SQLite 路径（CWD 无关），或用户显式配置的 DEMO_DATABASE_URL。"""
        return self.demo_database_url or f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"

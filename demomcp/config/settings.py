"""配置层：从 demo-mcp/.env 读 DeepSeek / MCP / agent / 数据库配置（pydantic-settings）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from demomcp.config.env import PROJECT_ROOT

DEFAULT_SYSTEM_PROMPT = (
    "你是面向比亚迪(002594.SZ)、宁德时代(300750.SZ)两家上市公司的金融数据助手。"
    "只能使用提供给你的语义工具：stock_available / stock_realtime_quote / stock_price_range / stock_financials。"
    "标的用公司名/代码/别名皆可；日期支持多种格式与相对词（如 2026-08-01、20260801、近一年、今天）；"
    "区间涨跌幅默认前复权(qfq)。不确定可选公司时可先调 stock_available 确认。"
    "取到数据后总结成简洁中文，必要时给出关键数字；若工具返回无数据、区间交易日不足或权限/积分不足，"
    "如实转述给用户并说明，而不是编造数字。"
)

DEFAULT_DISCLAIMER = "以上内容基于公开数据整理，仅供研究参考，不构成投资建议。"


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

    # 语义工具层（双票限定）
    stock_allowlist: str = Field(default="002594.SZ,300750.SZ", alias="DEMO_STOCKS")  # 硬 allowlist（逗号分隔 ts_code）
    stock_default_adj: str = Field(default="qfq", alias="STOCK_DEFAULT_ADJ")           # 区间涨跌幅默认复权口径
    stock_financial_periods: int = Field(default=8, alias="STOCK_FINANCIAL_PERIODS")   # 财务默认期数（近 2 年 = 8 期）

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
    def effective_database_url(self) -> str:
        """落到 demo-mcp 的默认 SQLite 路径（CWD 无关），或用户显式配置的 DEMO_DATABASE_URL。"""
        return self.demo_database_url or f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"

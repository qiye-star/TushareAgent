"""配置层：从 demo-mcp/.env 读 DeepSeek / MCP / agent / 数据库配置（pydantic-settings）。"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from demomcp.config.env import PROJECT_ROOT

DEFAULT_SYSTEM_PROMPT = (
    "你是一位 Tushare 金融数据助手。通过 MCP 提供的工具查数据："
    "先用 list_apis 浏览 / 搜索可用接口，再用 get_api_info 确认必填参数与返回列，"
    "最后用 query 取数。取到数据后用简洁中文总结，必要时给出关键数字。"
    "若 query 返回 code!=0（如接口无权限需提升积分），向用户说明并给出建议，"
    "而不是假装取到了数据。"
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

    # 会话历史库（留空 → 回落 sqlite+aiosqlite:///<PROJECT_ROOT>/demo.db）
    demo_database_url: str = ""             # DEMO_DATABASE_URL

    # MCP 服务器启动（配置驱动：MCP_PYTHON 留空用当前解释器；MCP_SERVER_PATH 必填）
    tushare_proxy_url: str = "http://127.0.0.1:8000"    # TUSHARE_PROXY_URL
    tushare_api_key: str = ""                           # TUSHARE_API_KEY
    tushare_proxy_timeout: float = 20.0                 # TUSHARE_PROXY_TIMEOUT
    mcp_python: str = ""                                # MCP_PYTHON（留空=当前解释器）
    mcp_server_path: str = ""                           # MCP_SERVER_PATH（留空=内置 mcp_server/server.py）
    mcp_args: list[str] = Field(default_factory=list)   # MCP_ARGS (JSON 数组)
    mcp_timeout: float = Field(default=30.0, alias="DEMO_MCP_TIMEOUT")   # 单次工具调用读超时（秒）
    mcp_retries: int = Field(default=2, alias="DEMO_MCP_RETRIES")        # 工具调用重试次数

    @property
    def is_configured(self) -> bool:
        return bool(self.ds_api_key)

    @property
    def effective_database_url(self) -> str:
        """落到 demo-mcp 的默认 SQLite 路径（CWD 无关），或用户显式配置的 DEMO_DATABASE_URL。"""
        return self.demo_database_url or f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"

"""网关自己的配置：独立 pydantic-settings，**只**读 mcp_gateway/.env（不回退读 demomcp 根目录的
.env——「独立成一个项目」连配置边界也独立；从根 .env 搬 TUSHARE_MCP_URL / WIND_API_KEY 这两行过来即可）。

上游源的地址与 key 只存在于这一处：demomcp 那边已经删掉了这些字段，它只知道网关地址。
`config_problems()` 把「其实没配好」的状态显式化——启动日志与 /admin/health 都会照实说出来，
而不是拿一个没有 token 的默认 URL 去假装可用（那会表现成一堆 40203 权限错，很难查）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# mcp_gateway/config.py -> parents[0]=mcp_gateway
GATEWAY_DIR = Path(__file__).resolve().parents[0]


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(GATEWAY_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = Field(default="0.0.0.0", alias="GATEWAY_HOST")
    port: int = Field(default=8766, alias="GATEWAY_PORT")
    data_dir: str = Field(default=str(GATEWAY_DIR / "data"), alias="GATEWAY_DATA_DIR")

    # 上游源（唯一配置处）：留空 = 该源不注册，不会出现在 /admin/sources 里
    tushare_mcp_url: str = Field(default="", alias="TUSHARE_MCP_URL")
    wind_api_key: str = Field(default="", alias="WIND_API_KEY")
    wind_enabled: bool = Field(default=True, alias="WIND_ENABLED")

    # 同花顺 iFind：远程多域 MCP，鉴权是裸 token（非 Bearer）；并发受套餐限制（免费 2/个人 5/企业 10）
    ifind_auth_token: str = Field(default="", alias="IFIND_AUTH_TOKEN")
    ifind_enabled: bool = Field(default=True, alias="IFIND_ENABLED")
    ifind_concurrency: int = Field(default=2, alias="IFIND_CONCURRENCY")

    # 免费源：各自是**独立进程**里的 MCP server，网关只当普通 MCP 客户端连它们的 URL
    # （形态同 TUSHARE_MCP_URL：留空即不注册，不占一个永远连不上的开关项）。
    akshare_mcp_url: str = Field(default="", alias="AKSHARE_MCP_URL")
    china_news_mcp_url: str = Field(default="", alias="CHINA_NEWS_MCP_URL")

    mcp_timeout: float = Field(default=30.0, alias="MCP_TIMEOUT")
    mcp_retries: int = Field(default=2, alias="MCP_RETRIES")
    mcp_keepalive_interval: float = Field(default=45.0, alias="MCP_KEEPALIVE")

    @property
    def tushare_configured(self) -> bool:
        return bool(self.tushare_mcp_url)

    @property
    def wind_configured(self) -> bool:
        return self.wind_enabled and bool(self.wind_api_key)

    @property
    def ifind_configured(self) -> bool:
        return self.ifind_enabled and bool(self.ifind_auth_token)

    @property
    def akshare_configured(self) -> bool:
        return bool(self.akshare_mcp_url)

    @property
    def china_news_configured(self) -> bool:
        return bool(self.china_news_mcp_url)

    @property
    def any_source_configured(self) -> bool:
        """有没有**任何**一个源可注册；供 config_problems() 判「网关会不会是空壳」。

        新增源时记得加进来——漏了会导致「只配了新源」时被误报成没有可用源。
        """
        return any(
            (
                self.tushare_configured,
                self.wind_configured,
                self.ifind_configured,
                self.akshare_configured,
                self.china_news_configured,
            )
        )

    def config_problems(self) -> list[str]:
        """把「没配好」讲成人话（启动 ERROR 日志 + /admin/health 都用它）；空列表 = 配置齐备。

        故意不抛异常/不退出进程：容器带 `restart: unless-stopped`，配置问题崩进程会变成重启风暴，
        而且 admin API 必须活着，人才能从页面上看到到底缺什么。
        """
        problems: list[str] = []
        if not env_file_exists():
            problems.append(
                f"缺少配置文件 {GATEWAY_DIR / '.env'}：请复制 mcp_gateway/.env.example 为 "
                "mcp_gateway/.env，并从项目根 .env 把 TUSHARE_MCP_URL 与 WIND_API_KEY 搬过来。"
            )
        if not self.tushare_mcp_url:
            problems.append("未配置 TUSHARE_MCP_URL：Tushare 源不会注册，网关将没有任何 Tushare 工具。")
        elif "token=" not in self.tushare_mcp_url:
            problems.append(
                "TUSHARE_MCP_URL 里没有 token= 参数：官方 MCP 需要把 token 放在 URL query 上，"
                "否则每个接口都会返回权限错误。"
            )
        if self.wind_enabled and not self.wind_api_key:
            problems.append("WIND_ENABLED=true 但 WIND_API_KEY 为空：万得源不会注册（如不需要万得可设 WIND_ENABLED=false）。")
        if self.ifind_enabled and not self.ifind_auth_token:
            problems.append(
                "IFIND_ENABLED=true 但 IFIND_AUTH_TOKEN 为空：同花顺 iFind 源不会注册"
                "（如不需要 iFind 可设 IFIND_ENABLED=false）。"
            )
        if self.ifind_configured and self.ifind_concurrency < 1:
            problems.append("IFIND_CONCURRENCY 必须 ≥1（iFind 按套餐限并发：免费版 2、个人版 5、企业版 10）。")
        if not self.any_source_configured:
            problems.append("当前没有任何可用上游源，网关会连一个空的工具清单——agent 将取不到任何数据。")
        return problems


def env_file_exists() -> bool:
    return (GATEWAY_DIR / ".env").is_file()

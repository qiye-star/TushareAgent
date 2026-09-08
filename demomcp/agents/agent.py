"""核心编排层：LangGraph 四节点状态机（Router → Tool/RAG → Synthesizer / Fallback）的薄壳。

`Agent` 只依赖 interfaces 的 ToolProvider 与 LLMClient（经 `demomcp.graph.builder` 构图闭包注入）；
`run` 保持原有外观（history + on_text/on_thinking/on_tool），内部经 LangGraph `ainvoke` 驱动，
把图结果归一化为 `AgentResult`（与旧手动循环契约一致，cli/web 零改动）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from demomcp.config.logging import get_logger
from demomcp.config.settings import (
    FREE_SOURCE_USAGE_GUIDE,
    IFIND_USAGE_GUIDE,
    WIND_USAGE_GUIDE,
    Settings,
)
from demomcp.graph.builder import build_research_graph
from demomcp.graph.skills import SKILLS
from demomcp.graph.state import GraphState
from demomcp.graph.tool_select import select_tools
from demomcp.interfaces.llm_client import LLMClient
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import META_TOOL_NAMES, AgentResult

_log = get_logger("agents")

# 快速问答模式追加进 base_system 的后缀：只允许一轮取数，且要求模型在回答里自证身份、引导切换智能体模式。
# 不放进 graph/prompts.py——那是 select_system/synth_system 两种模式共享的公共文件，这段只属于快速模式。
QUICK_MODE_SUFFIX = (
    "\n\n【快速问答模式】本轮你只能查一次数据（最多一组并行工具调用），看到返回后必须直接总结作答，"
    "不能再发起新一轮取数。回答开头先声明「（快速问答模式）」，并在结尾提示：如需更全面/更深入的多轮分析，"
    "可切换到「智能体模式」重新提问。"
)


WIND_TOOL_PREFIX = "wind_"
IFIND_TOOL_PREFIX = "ifind_"

# 免费源（AkShare / 财经新闻）的工具名**没有前缀**，只能按哨兵名判断在不在本轮清单里。
# 挑的是这两个源独有、且不与 Tushare 官方接口名撞车的名字（Tushare 那边是 daily/stock_basic 这类）。
FREE_SOURCE_SENTINELS = frozenset({"get_market_overview", "get_market_headlines", "search_stock"})


def base_system_for(cfg: Settings, tool_defs: list[Any]) -> str:
    """基础 system_prompt + 各数据源的用法约定，**按本轮工具清单里真的有什么**决定追加哪几段。

    从前这是 `Settings.effective_system_prompt`，按 demomcp 自己的 WIND_API_KEY 判断；现在上游源全部
    由 MCP 网关持有并按源开关，demomcp 读不到（也不该读）源配置——直接看网关这一轮实际暴露了什么：
    网关把某个源关掉，下一次建图时它的工具消失，提示词也就自动不再讲它的用法，两边永不打架。

    每段用法约定都不是可有可无的文字：三家的参数风格互不兼容（Tushare 结构化字段 + 带后缀代码；
    万得自然语言 + Wind 后缀代码；iFind 单个自然语言 query；免费源 6 位裸代码），
    不注入对应那段，LLM 就会拿另一家的习惯传参而稳定取不到数。
    """
    names = [getattr(spec, "name", "") for spec in tool_defs]
    base = cfg.system_prompt
    if any(n.startswith(WIND_TOOL_PREFIX) for n in names):
        base += "\n\n" + WIND_USAGE_GUIDE
    if any(n.startswith(IFIND_TOOL_PREFIX) for n in names):
        base += "\n\n" + IFIND_USAGE_GUIDE
    if FREE_SOURCE_SENTINELS.intersection(names):
        base += "\n\n" + FREE_SOURCE_USAGE_GUIDE
    return base


def _load_catalog(path: str) -> dict[str, Any] | None:
    """读工具可用性缓存（TOOL_PROBE_CACHE_PATH）；不存在/损坏 → None（不剔除，行为同今日）。"""
    try:
        import json

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - 缓存缺失/损坏只当无剔除
        return None


def _flatten_exceptions(err: BaseException) -> list[BaseException]:
    """把（可能嵌套的）ExceptionGroup/BaseExceptionGroup 拍平成叶子异常，便于取真实原因/判断取消。"""
    if isinstance(err, BaseExceptionGroup):
        out: list[BaseException] = []
        for e in err.exceptions:
            out.extend(_flatten_exceptions(e))
        return out
    return [err]


class Agent:
    def __init__(self, *, llm: LLMClient, tools: ToolProvider, config: Settings) -> None:
        self._llm = llm
        self._tools = tools
        self._config = config
        self._tool_defs: list[Any] | None = None
        self._graphs: dict[str, Any] = {}  # 按 mode（agent/quick）分片缓存编译好的图
        self._retriever: Any | None = None
        self._tool_catalog: dict[str, Any] | None = None

    async def _get_tool_defs(self) -> list[Any]:
        if self._tool_defs is None:
            self._tool_defs = await self._tools.list_tools()
            self._tool_catalog = await self._resolve_tool_catalog()
        return self._tool_defs

    async def _resolve_tool_catalog(self) -> dict[str, Any] | None:
        """工具可用性目录：TOOL_PROBE_ENABLED 时实时探测并写缓存，否则读缓存；无缓存/失败 → None（不剔除）。

        默认关（避免每启动真实调用各接口烧积分、规避官方 MCP 契约未知风险）；开启时全 try/except，失败只回退不剔除。
        """
        cfg = self._config
        path = cfg.tool_probe_cache_path
        if cfg.tool_probe_enabled:
            try:
                from demomcp.providers.tools.curate import (
                    probe_availability,
                    save_catalog,
                )

                catalog = await probe_availability(
                    self._tools, self._tool_defs, concurrency=cfg.tool_probe_concurrency
                )
                save_catalog(catalog, path)
                return catalog
            except Exception as exc:  # noqa: BLE001 - 探测失败仅回退读缓存/不剔除
                print(f"[agent] 工具可用性探测失败，忽略：{exc}")
        return _load_catalog(path)

    async def _get_retriever(self) -> Any | None:
        """懒加载 + 兜底：RAG_HTTP_URL 时用 HTTP 远端检索（该进程不再打开 Milvus）；
        否则构建运行时 retriever，失败回退 None（图上不接 RAG，不崩）。"""
        if self._retriever is None:
            cfg = self._config
            if cfg.rag_http_url:
                from demomcp.rag.http_retriever import HttpRetriever

                self._retriever = HttpRetriever(
                    cfg.rag_http_url, timeout=cfg.rag_http_timeout, token=cfg.rag_http_token
                )
                return self._retriever
            try:
                from demomcp.rag.runtime import build_runtime_retriever

                self._retriever = await build_runtime_retriever(cfg)
            except Exception as exc:  # noqa: BLE001 - RAG 构建失败仅回退无检索
                print(f"[agent] RAG retriever 构建失败，回退无检索：{exc}")
                self._retriever = None
        return self._retriever

    async def _get_graph(self, mode: str = "agent") -> Any:
        """按 mode 分片缓存编译好的图：agent/quick 共享同一个 build_research_graph，仅传参不同
        （base_system 后缀 / max_iterations / skills），nodes.py/routes.py/builder.py 本体不感知 mode。"""
        if mode not in self._graphs:
            tool_defs = await self._get_tool_defs()
            retriever = await self._get_retriever()
            cfg = self._config
            meta = META_TOOL_NAMES if cfg.tool_meta_always else frozenset()

            def curate(
                specs: list[Any], query: str, *, skill_tools: frozenset[str] = frozenset()
            ) -> list[Any]:
                return select_tools(
                    specs, query, max_revealed=cfg.tool_max_revealed, meta=meta,
                    catalog=self._tool_catalog, skill_tools=skill_tools,
                )

            system = base_system_for(cfg, tool_defs)  # 各源用法约定按本轮实际暴露的工具决定
            if mode == "quick":
                base_system = system + QUICK_MODE_SUFFIX
                max_iterations = 1
                # 快速模式不生成结构化投研报告：传空注册表而非 None——
                # _resolve_skill 对 None 会回退全局 SKILLS（2026-09-07 审查：原先 skills=None 实际未禁用）
                skills = []
            else:
                base_system = system
                max_iterations = cfg.max_iterations
                skills = SKILLS

            self._graphs[mode] = build_research_graph(
                self._llm,
                self._tools,
                tool_defs,
                max_tokens=cfg.ds_max_tokens,
                disclaimer=cfg.disclaimer,
                base_system=base_system,
                retriever=retriever,
                skills=skills,
                curate=curate,
                max_iterations=max_iterations,
            )
        return self._graphs[mode]

    def _error_result(self, messages: list[dict[str, Any]], cause: BaseException, mode: str = "agent") -> AgentResult:
        self._log_error(cause)
        return AgentResult(
            final_text=f"处理失败：{cause}。请稍后重试。\n\n{self._config.disclaimer}",
            stopped_reason="error",
            messages=messages,
            tool_results=[],
            usage=None,
            mode=mode,
        )

    @staticmethod
    def _log_error(cause: BaseException) -> None:
        _log.error(
            "agent.run failed: %s",
            type(cause).__name__,
            exc_info=(type(cause), cause, cause.__traceback__),
        )

    async def run(
        self,
        user_input: str,
        *,
        history: list[dict[str, Any]] | None = None,
        mode: str = "agent",
        forced_skill: str | None = None,
        on_text=None,
        on_thinking=None,
        on_tool=None,
        on_process=None,
    ) -> AgentResult:
        messages = list(history or []) + [{"role": "user", "content": user_input}]
        state: GraphState = {
            "messages": messages,
            "original_query": user_input,
            "out_of_scope": False,
            "tool_results": [],
            "evidence": [],
            "rag_chunks": [],
            "citations": [],
            "usage": None,
            "stopped_reason": "end_turn",
            # agentic tool loop：循环状态初值（RAG 只首次检索、loop 计数 0、尚未决定继续取数、无进展计数 0）
            "loop_index": 0,
            "want_more": False,
            "rag_retrieved": False,
            "no_progress_count": 0,
            # 前端技能页「快速使用」指定的报告 skill（router 据此跳过 skill 判定）
            "forced_skill": forced_skill or None,
        }
        # 强制 skill 与 quick 模式冲突时按 agent 跑（决策 O1 的后端保护）：quick 模式 skills=[]，
        # 会让 _resolve_skill 找不到指定技能而静默降级成普通问答——那不是用户点「快速使用」的预期。
        if forced_skill and mode == "quick":
            _log.info("forced_skill=%s 与 quick 模式冲突，按 agent 模式执行", forced_skill)
            mode = "agent"
        loop_cap = 1 if mode == "quick" else self._config.max_iterations
        try:
            # _get_graph（经 _get_tool_defs → tools.list_tools()）挪进 try 块：其抛出的异常（如
            # MCP 断线竞态）必须走本方法统一的 _error_result/_log_error（带 exc_info 的结构化日志），
            # 而不是逃逸到调用方（web.py）更外层、日志没有 traceback 的通用兜底（2026-09-07 事故）。
            graph = await self._get_graph(mode)
            result = await graph.ainvoke(
                state,
                config={
                    # 保证我们设定的循环上限先于 LangGraph 默认 recursion_limit（10007）触发
                    "recursion_limit": loop_cap + 20,
                    "configurable": {"on_text": on_text, "on_thinking": on_thinking, "on_tool": on_tool, "on_process": on_process},
                },
            )
        except asyncio.CancelledError:
            raise  # 客户端断连/取消：透传（BaseException），不误报“处理失败”
        except BaseExceptionGroup as eg:  # 取消组透传，真异常组归一为优雅结果
            leaves = _flatten_exceptions(eg)
            cancel = [e for e in leaves if isinstance(e, asyncio.CancelledError)]
            if cancel:
                raise cancel[0]
            return self._error_result(messages, leaves[-1] if leaves else eg, mode)
        except Exception as exc:  # noqa: BLE001 - 图普通异常归一为优雅结果
            return self._error_result(messages, exc, mode)
        return AgentResult(
            final_text=result.get("final_answer") or "",
            stopped_reason=result.get("stopped_reason") or "end_turn",
            messages=result.get("messages") or messages,
            tool_results=result.get("tool_results") or [],
            usage=result.get("usage"),
            citations=result.get("citations") or [],
            structured=result.get("structured"),
            mode=mode,
        )

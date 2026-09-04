"""入口层 · CLI REPL：用自然语言驱动 agent，流式打印回复与思考，并把会话历史落到 demo-mcp 自己的库。

运行（demo-mcp 自带 .venv，先 `uv sync`）：
    python -m demomcp.entry.cli
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from demomcp.agents.agent import Agent
from demomcp.config.logging import configure_logging, get_logger, log_chat_turn
from demomcp.config.settings import Settings
from demomcp.db.store import build_store
from demomcp.providers.llm.deepseek import DeepSeekLLMClient
from demomcp.providers.tools.mcp import mcp_tool_provider

logger = get_logger("entry.cli")


async def main() -> int:
    settings = Settings()
    if not settings.ds_api_key:
        sys.stderr.write(
            "DS_API_KEY 未配置：请在 demo-mcp/.env 设置 DeepSeek 的 API key，再用真 key 跑通。\n"
        )
        return 2

    llm = DeepSeekLLMClient(
        api_key=settings.ds_api_key,
        base_url=settings.ds_base_url or None,
        model=settings.ds_model or None,
    )
    store = build_store(settings.effective_database_url)

    try:
        await store.init()
        configure_logging(
            settings.log_level, settings.log_file, settings.log_max_bytes, settings.log_backup_count
        )
        async with mcp_tool_provider(
            settings.tushare_mcp_url, timeout=settings.mcp_timeout, retries=settings.mcp_retries
        ) as tools:
            # 直接使用原始 MCP provider：LLM 看到服务端暴露的全部工具（list_apis/get_api_info/query + 各接口工具），可查任意标的任意接口
            agent = Agent(llm=llm, tools=tools, config=settings)
            session_id = uuid.uuid4().hex
            history: list[dict] = []

            async def on_text(text: str) -> None:
                sys.stdout.write(text)
                sys.stdout.flush()

            async def on_thinking(text: str) -> None:
                sys.stdout.write(f"\n[思考] {text}\n")
                sys.stdout.flush()

            print("Tushare 数据助手（DeepSeek + MCP，会话历史已落库）。输入 exit / quit 退出。")
            try:
                while True:
                    prompt = await asyncio.to_thread(input, "You> ")
                    prompt = prompt.strip()
                    if not prompt:
                        continue
                    if prompt in {"exit", "quit", "q"}:
                        break
                    print("\nAssistant> ", end="", flush=True)
                    result = await agent.run(
                        prompt, history=history, on_text=on_text, on_thinking=on_thinking
                    )
                    if not settings.ds_streaming:
                        sys.stdout.write(result.final_text)
                    print(f"\n[stop: {result.stopped_reason}]")
                    # 持久化：用户 / 助手 / 工具调用，按会话
                    await store.append(session_id, "user", prompt)
                    await store.append(session_id, "assistant", result.final_text)
                    for tr in result.tool_results:
                        await store.append(session_id, "tool", tr.content)
                    log_chat_turn(
                        logger,
                        session_id,
                        {
                            "query": prompt,
                            "thinking": "",
                            "steps": [],
                            "answer": result.final_text,
                            "sources": [],
                            "citations": [],
                            "claims": [],
                            "metadata": None,
                            "intent": None,
                            "strategy": None,
                            "stopped_reason": result.stopped_reason,
                            "usage": None,
                            "error": None,
                        },
                    )
                    history = result.messages
            except (EOFError, KeyboardInterrupt):
                print()
    finally:
        await store.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

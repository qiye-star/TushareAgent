"""config/logging 单元测试：configure_logging 写 UTF-8 文件 + log_chat_turn 内容正确。"""

from demomcp.config.logging import configure_logging, get_logger, log_chat_turn


def test_configure_logging_writes_chat_turn_to_file(tmp_path) -> None:
    log_file = tmp_path / "chat.log"
    configure_logging("INFO", log_file, 1_000, 2)
    logger = get_logger("entry.web")

    blob = {
        "query": "比亚迪近一年区间涨跌幅",
        "thinking": "先查行情",
        "steps": [
            {"kind": "intent", "data": {"intent": "market", "strategy": "auto"}},
            {"kind": "tool_call", "data": {"name": "stock_realtime", "input": {"stock": "比亚迪"}}},
        ],
        "answer": "区间涨跌幅约为 10%。",
        "sources": [{"type": "tool", "title": "stock_realtime"}],
        "intent": "market",
        "strategy": "auto",
        "stopped_reason": "end_turn",
        "usage": None,
        "error": None,
    }
    log_chat_turn(logger, "a" * 24, blob)

    for h in logger.handlers:
        h.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "比亚迪近一年区间涨跌幅" in text
    assert "steps=['intent', 'tool_call']" in text
    assert "intent=market strategy=auto stopped=end_turn" in text
    assert "A: 区间涨跌幅约为 10%。" in text

    # DEBUG 级别再记一次 → 文件包含完整 JSON 转储
    configure_logging("DEBUG", log_file, 1_000, 2)
    logger = get_logger("entry.web")
    log_chat_turn(logger, "b" * 24, blob)
    for h in logger.handlers:
        h.flush()
    text2 = log_file.read_text(encoding="utf-8")
    assert "turndump=" in text2

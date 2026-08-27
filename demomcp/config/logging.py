"""日志配置：把对话以 UTF-8 文本落到文件，并按大小轮转（logging + RotatingFileHandler）。

用法（在 web lifespan / CLI 启动各配置一次）:
    configure_logging("INFO", PROJECT_ROOT / "logs" / "chat.log", 5 * 1024 * 1024, 3)
之后 `get_logger("entry.web").info(...)` 会写入该文件（`demomcp.*` 子 logger 向上传播到已挂 handler 的 `demomcp`）。
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "demomcp"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def configure_logging(
    level: str = "INFO",
    log_file: Path | str | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """配置 `demomcp` logger 写一个按大小轮转的 UTF-8 文本日志。

    重复调用会先清掉旧 handler，因此幂等（测试/多次启动都安全）。
    `log_file=None` 时退回 StreamHandler（写 stdout）。
    """
    logger = logging.getLogger(LOGGER_NAME)
    level = str(level).upper()
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    if log_file is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")

    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """取 `demomcp[.name]` 子 logger（向上传播到已挂 handler 的 `demomcp`）。"""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def log_chat_turn(logger: logging.Logger, session_id: str, blob: dict) -> None:
    """日志化一轮对话：INFO 记可读摘要，DEBUG 记完整 JSON（便于人工/工具查看）。"""
    sid = session_id[:12]
    logger.info("[session=%s] Q: %s", sid, (blob.get("query") or "").strip())
    logger.info(
        "[session=%s] intent=%s strategy=%s stopped=%s",
        sid,
        blob.get("intent"),
        blob.get("strategy"),
        blob.get("stopped_reason"),
    )
    steps = blob.get("steps") or []
    if steps:
        kinds = [s.get("kind") for s in steps if isinstance(s, dict)]
        logger.info("[session=%s] steps=%s", sid, kinds)
    sources = blob.get("sources") or []
    if sources:
        logger.info("[session=%s] sources=%d", sid, len(sources))
    answer = (blob.get("answer") or "").strip()
    if answer:
        logger.info("[session=%s] A: %s", sid, answer.replace("\n", " "))
    if blob.get("error"):
        logger.error("[session=%s] error=%s", sid, blob.get("error"))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("[session=%s] turndump=%s", session_id, json.dumps(blob, ensure_ascii=False))

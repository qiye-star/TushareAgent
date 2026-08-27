"""SQLAlchemy 2.0 ORM 模型：会话聊天日志（可读）与精确恢复数据。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ChatMessage(Base):
    """按条的可读日志：user / assistant / tool / error，供侧栏展示与出错定位。"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))  # user / assistant / tool / error
    content: Mapped[str] = mapped_column(Text)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatTurn(Base):
    """每轮完整消息（OpenAI 格式 JSON，累计式），用于精确恢复对话上下文。"""

    __tablename__ = "chat_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    messages: Mapped[str] = mapped_column(Text)  # json.dumps(list[dict])
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatTurnData(Base):
    """每轮面向 UI 的完整载荷（JSON），供前端重载时原样还原思考/工具调用/ReAct 链/引用。

    与 ChatTurn（供 LLM 上下文恢复的 OpenAI 消息）解耦；create_all 会新建本表，不触发 ALTER。
    """

    __tablename__ = "chat_turn_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[str] = mapped_column(Text)  # json.dumps(dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

"""会话历史访问层：异步 SQLAlchemy engine/session。

- ChatMessage：按条可读日志（role/content/is_error/created_at），供侧栏显示与出错定位。
- ChatTurn：每轮完整 OpenAI 消息（累计式 JSON），供精确恢复（含 tool_calls / tool_call_id）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from demomcp.db.models import Base, ChatMessage, ChatTurn


class ChatHistoryStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    async def init(self) -> None:
        """本地建表（demo-mcp 专属表）。"""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def append(self, session_id: str, role: str, content: str, *, is_error: bool = False) -> None:
        async with self._session_factory() as session:
            session.add(
                ChatMessage(session_id=session_id, role=role, content=content, is_error=is_error)
            )
            await session.commit()

    async def append_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        async with self._session_factory() as session:
            session.add(ChatTurn(session_id=session_id, messages=json.dumps(messages, ensure_ascii=False)))
            await session.commit()

    async def last_turn_messages(self, session_id: str) -> list[dict[str, Any]] | None:
        """取该会话最新一轮的完整消息（累计式），用于恢复；无记录则 None。"""
        async with self._session_factory() as session:
            stmt = (
                select(ChatTurn)
                .where(ChatTurn.session_id == session_id)
                .order_by(ChatTurn.id.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalars().first()
        if row is None:
            return None
        return json.loads(row.messages)

    async def load(self, session_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """该会话逐条可读消息（含 is_error / created_at）。"""
        async with self._session_factory() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "role": r.role,
                "content": r.content,
                "is_error": r.is_error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        return await self.load(session_id)

    async def list_sessions(self) -> list[str]:
        async with self._session_factory() as session:
            stmt = select(ChatMessage.session_id).distinct()
            return list((await session.execute(stmt)).scalars().all())

    async def list_sessions_meta(self) -> list[dict[str, Any]]:
        """侧栏用：会话 + 消息条数 + 最近时间（倒序）。"""
        async with self._session_factory() as session:
            stmt = (
                select(
                    ChatMessage.session_id,
                    func.count(ChatMessage.id),
                    func.max(ChatMessage.created_at),
                )
                .group_by(ChatMessage.session_id)
                .order_by(func.max(ChatMessage.created_at).desc())
            )
            rows = (await session.execute(stmt)).all()
        return [
            {
                "session_id": sid,
                "count": count,
                "created_at": created_at.isoformat() if created_at else None,
            }
            for sid, count, created_at in rows
        ]

    async def delete(self, session_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
            await session.execute(delete(ChatTurn).where(ChatTurn.session_id == session_id))
            await session.commit()

    async def dispose(self) -> None:
        await self._engine.dispose()


def build_store(database_url: str) -> ChatHistoryStore:
    """按 DEMO_DATABASE_URL 建 store（默认 SQLite / DSN 自动驱动）。"""
    return ChatHistoryStore(create_async_engine(database_url))

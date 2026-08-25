"""SQLAlchemy 数据层离线测试：用内存 SQLite（StaticPool 共享单连接）验证会话历史往返。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from demomcp.db.store import ChatHistoryStore


@pytest.fixture
async def store():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    s = ChatHistoryStore(engine)
    await s.init()
    yield s
    await s.dispose()


async def test_append_load_roundtrip(store) -> None:
    await store.append("s1", "user", "你好")
    await store.append("s1", "assistant", "你好！")
    await store.append("s1", "tool", "[1,2]")
    msgs = await store.load("s1")
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[0]["content"] == "你好"


async def test_load_scoped_by_session(store) -> None:
    await store.append("s1", "user", "a")
    await store.append("s2", "user", "b")
    assert len(await store.load("s1")) == 1
    assert len(await store.load("s2")) == 1
    assert sorted(await store.list_sessions()) == ["s1", "s2"]


async def test_load_empty(store) -> None:
    assert await store.load("none") == []


async def test_append_with_is_error(store) -> None:
    await store.append("s1", "tool", "boom", is_error=True)
    msgs = await store.get_messages("s1")
    assert msgs[0]["is_error"] is True


async def test_append_turn_and_recovery(store) -> None:
    await store.append_turn("s1", [{"role": "user", "content": "hi"}])
    await store.append_turn(
        "s1",
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi there"}],
    )
    latest = await store.last_turn_messages("s1")
    assert latest == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi there"},
    ]


async def test_last_turn_messages_empty(store) -> None:
    assert await store.last_turn_messages("none") is None


async def test_list_sessions_meta(store) -> None:
    await store.append("s1", "user", "a")
    await store.append("s1", "assistant", "b")
    await store.append("s2", "user", "c")
    meta = await store.list_sessions_meta()
    counts = {m["session_id"]: m["count"] for m in meta}
    assert counts == {"s1": 2, "s2": 1}


async def test_delete_session(store) -> None:
    await store.append("s1", "user", "a")
    await store.append_turn("s1", [{"role": "user", "content": "a"}])
    await store.delete("s1")
    assert await store.get_messages("s1") == []
    assert await store.last_turn_messages("s1") is None

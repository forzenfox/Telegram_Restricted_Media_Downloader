# coding=UTF-8
"""TelegramRestrictedMediaDownloaderSession.invoke 重试策略单元测试。

覆盖场景（方案二）：
- 成功调用立即返回
- 网络/服务临时故障受总循环上限（max_cycles）约束，达到上限后抛异常，避免无限重试
- 500（InternalServerError）快速失败，不做重试
- 大额 FloodWait（超过阈值）仍直接抛出
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrogram.errors import FloodWait, InternalServerError, ServiceUnavailable

from module.core.download.client import TelegramRestrictedMediaDownloaderSession


class _FakeQuery:
    QUALNAME = "pyrogram.raw.functions.upload.GetFile"


@pytest.fixture
def session_factory(monkeypatch):
    # 屏蔽重试过程中的控制台输出
    monkeypatch.setattr(
        "module.core.download.client.console.log",
        lambda *args, **kwargs: None,
    )

    def _make(send_side_effect):
        session = object.__new__(TelegramRestrictedMediaDownloaderSession)
        session.is_started = asyncio.Event()
        session.is_started.set()
        session.client = SimpleNamespace(name="test")
        session.send = AsyncMock(side_effect=send_side_effect)
        return session

    return _make


@pytest.mark.asyncio
async def test_invoke_success_returns_immediately(session_factory):
    """成功调用应立即返回，不做额外重试。"""
    session = session_factory(AsyncMock(return_value="ok"))

    result = await session.invoke(_FakeQuery())

    assert result == "ok"
    session.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_raises_after_max_cycles(session_factory):
    """持续服务故障达到总循环上限后应抛出异常，而非无限重试。"""
    error = ServiceUnavailable("Telegram says: [503 ...]")
    session = session_factory(AsyncMock(side_effect=error))
    retries = 3
    max_cycles = 2

    with pytest.raises(ServiceUnavailable):
        await session.invoke(_FakeQuery(), retries=retries, max_cycles=max_cycles)

    # 每轮重试 retries 次，共 max_cycles 轮
    assert session.send.await_count == retries * max_cycles


@pytest.mark.asyncio
async def test_invoke_internal_server_error_fails_fast(session_factory):
    """500 服务器内部错误应快速失败，不做任何重试。"""
    error = InternalServerError(
        "Telegram says: [500 PERSISTENT_TIMESTAMP_OUTDATED]"
    )
    session = session_factory(AsyncMock(side_effect=error))

    with pytest.raises(InternalServerError):
        await session.invoke(_FakeQuery(), retries=3, max_cycles=3)

    session.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_large_flood_wait_raises(session_factory):
    """超过阈值的 FloodWait 应直接抛出，不重试。"""
    error = FloodWait(value=60)
    session = session_factory(AsyncMock(side_effect=error))

    with pytest.raises(FloodWait):
        await session.invoke(_FakeQuery())

    session.send.assert_awaited_once()
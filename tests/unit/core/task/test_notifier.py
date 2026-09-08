# coding=UTF-8
"""TaskNotifier 单元测试

覆盖场景：
- 通知开关矩阵（notice 总开关 × 完成/错误子开关）
- 常驻任务（监听/定时清理）终态不通知
- 消息内容（任务类型 / 任务 ID / 目标 / 原因 / 耗时）
- 发送异常被吞，不影响主流程
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from module.core.task.manager import Task, TaskType


class FakeConfig:
    """模拟 ConfigManager.get() 的嵌套读取（按点号拆分层级）。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key: str, default=None):
        node = self._data
        for k in key.split("."):
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node


def _make_task(
    task_type: TaskType = TaskType.UPLOAD,
    task_id: str = "task_test01",
    chat_id: int = -1001234567890,
    chat_username: str = "target_chat",
    error_message: str | None = None,
    started: datetime | None = None,
    completed: datetime | None = None,
) -> Task:
    """构造一个测试任务。"""
    created = started or datetime.now(UTC) - timedelta(minutes=5)
    return Task(
        task_id=task_id,
        task_type=task_type,
        chat_id=chat_id,
        chat_username=chat_username,
        created_at=created,
        started_at=started,
        completed_at=completed,
        error_message=error_message,
    )


def _make_notifier(**preference):
    """构造 TaskNotifier：注入 mock client 与 FakeConfig。"""
    from module.core.task.notifier import TaskNotifier

    client = AsyncMock()
    config = FakeConfig({"preference": preference or {}})
    notifier = TaskNotifier(client=client, root_ids=[10001], config_manager=config)
    return notifier, client


@pytest.fixture
def flags_on():
    """全开关开启的 preference。"""
    return {
        "notice": True,
        "notification_enabled": True,
        "error_notification_enabled": True,
    }


class TestCompletedNotification:
    """完成通知契约。"""

    @pytest.mark.asyncio
    async def test_sends_when_all_flags_enabled(self, flags_on):
        """全开关开启时，任务完成应发送通知。"""
        notifier, client = _make_notifier(**flags_on)
        task = _make_task(completed=datetime.now(UTC))
        await notifier.notify_completed(task)
        client.send_message.assert_awaited_once()
        text = client.send_message.await_args.kwargs["text"]
        assert "任务完成" in text

    @pytest.mark.asyncio
    async def test_text_contains_task_details(self, flags_on):
        """消息应包含任务类型 / 任务 ID / 目标 chat。"""
        notifier, client = _make_notifier(**flags_on)
        task = _make_task(
            task_type=TaskType.UPLOAD,
            task_id="task_abc123",
            chat_username="my_channel",
            completed=datetime.now(UTC),
        )
        await notifier.notify_completed(task)
        text = client.send_message.await_args.kwargs["text"]
        assert "上传" in text
        assert "task_abc123" in text
        assert "my_channel" in text

    @pytest.mark.asyncio
    async def test_not_sent_when_notice_off(self):
        """总开关 notice 关闭时不发送。"""
        notifier, client = _make_notifier(
            notice=False,
            notification_enabled=True,
            error_notification_enabled=True,
        )
        await notifier.notify_completed(_make_task())
        client.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_sent_when_completion_flag_off(self):
        """完成通知子开关关闭时不发送。"""
        notifier, client = _make_notifier(
            notice=True,
            notification_enabled=False,
            error_notification_enabled=True,
        )
        await notifier.notify_completed(_make_task())
        client.send_message.assert_not_awaited()


class TestFailedNotification:
    """错误通知契约。"""

    @pytest.mark.asyncio
    async def test_sends_when_all_flags_enabled(self, flags_on):
        """全开关开启时，任务失败应发送通知。"""
        notifier, client = _make_notifier(**flags_on)
        task = _make_task(error_message="下载超时")
        await notifier.notify_failed(task)
        client.send_message.assert_awaited_once()
        text = client.send_message.await_args.kwargs["text"]
        assert "任务失败" in text
        assert "下载超时" in text

    @pytest.mark.asyncio
    async def test_not_sent_when_error_flag_off(self):
        """错误通知子开关关闭时不发送。"""
        notifier, client = _make_notifier(
            notice=True,
            notification_enabled=True,
            error_notification_enabled=False,
        )
        await notifier.notify_failed(_make_task(error_message="x"))
        client.send_message.assert_not_awaited()


class TestResidentTasksExcluded:
    """常驻任务终态不通知。"""

    @pytest.mark.asyncio
    async def test_cleanup_failed_not_notified(self, flags_on):
        """定时清理任务失败不发送错误通知。"""
        notifier, client = _make_notifier(**flags_on)
        task = _make_task(
            task_type=TaskType.CLEANUP_FILES,
            task_id="task_clean",
            error_message="clean fail",
        )
        await notifier.notify_failed(task)
        client.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listen_completed_not_notified(self, flags_on):
        """监听任务完成不发送完成通知。"""
        notifier, client = _make_notifier(**flags_on)
        task = _make_task(task_type=TaskType.LISTEN_DOWNLOAD)
        await notifier.notify_completed(task)
        client.send_message.assert_not_awaited()


class TestNotificationErrorHandling:
    """发送异常容错。"""

    @pytest.mark.asyncio
    async def test_send_error_is_swallowed(self, flags_on):
        """send_message 抛错时 notify_* 不向上抛异常。"""
        from module.core.task.notifier import TaskNotifier

        client = AsyncMock()
        client.send_message = AsyncMock(side_effect=RuntimeError("send boom"))
        notifier = TaskNotifier(
            client=client,
            root_ids=[10001],
            config_manager=FakeConfig({"preference": flags_on}),
        )
        await notifier.notify_completed(_make_task())
        await notifier.notify_failed(_make_task(error_message="x"))

    @pytest.mark.asyncio
    async def test_no_config_manager_does_not_send(self):
        """config_manager 为 None 时不发送（无开关可查，默认不打扰）。"""
        from module.core.task.notifier import TaskNotifier

        client = AsyncMock()
        notifier = TaskNotifier(client=client, root_ids=[10001], config_manager=None)
        await notifier.notify_completed(_make_task())
        await notifier.notify_failed(_make_task(error_message="x"))
        client.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_preference_does_not_send(self):
        """preference 区块缺失时默认不发送（保守不打扰）。"""
        notifier, client = _make_notifier(notice=True)
        await notifier.notify_completed(_make_task())
        client.send_message.assert_not_awaited()

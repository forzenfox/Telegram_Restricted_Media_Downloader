# coding=UTF-8
"""CleanupScheduler 单元测试。

覆盖：next_run_at 周期计算、pause/resume 语义、run_now 投递。
"""
import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from module.core.task.manager import TaskManager, TaskType
from module.core.task.scheduler import CleanupScheduler


@pytest.fixture
def db_path():
    """创建临时数据库路径。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    if os.path.exists(f.name):
        os.unlink(f.name)


@pytest_asyncio.fixture
async def task_manager(db_path):
    """创建 TaskManager 实例。"""
    from module.core import db

    await db.init_db(db_path)
    tm = TaskManager(max_concurrent_tasks=2)
    yield tm
    await db.close_db()


@pytest.fixture
def fake_executor():
    """模拟 TaskExecutor（仅用于验证投递）。"""
    ex = MagicMock()
    ex.submit_task = MagicMock(return_value=object())
    ex._event_loop = object()
    return ex


class TestNextRunAt:
    """周期计算测试。"""

    @pytest.mark.asyncio
    async def test_daily_future_today(self, task_manager, fake_executor):
        """daily 模式：目标时刻尚未到 → 今天该时刻。"""
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)
        now = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
        next_run = scheduler._next_run_at(
            {"mode": "daily", "time": "03:00"}, now=now
        )
        assert next_run == datetime(2026, 9, 7, 3, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_daily_past_today(self, task_manager, fake_executor):
        """daily 模式：目标时刻已过 → 明天该时刻。"""
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)
        now = datetime(2026, 9, 7, 5, 0, tzinfo=UTC)
        next_run = scheduler._next_run_at(
            {"mode": "daily", "time": "03:00"}, now=now
        )
        assert next_run == datetime(2026, 9, 8, 3, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_interval(self, task_manager, fake_executor):
        """interval 模式：now + interval_hours。"""
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)
        now = datetime(2026, 9, 7, 1, 30, tzinfo=UTC)
        next_run = scheduler._next_run_at(
            {"mode": "interval", "interval_hours": 6}, now=now
        )
        assert next_run == now + timedelta(hours=6)


class TestPauseResume:
    """暂停/恢复语义测试。"""

    @pytest.mark.asyncio
    async def test_pause_persists_and_clears_next_run(
        self, task_manager, fake_executor
    ):
        """pause 置 paused=True 并清空 next_run_at。"""
        task = await task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={
                "keep_days": 7,
                "schedule": {"mode": "daily", "time": "03:00"},
                "last_run": {"next_run_at": "2026-09-08T03:00:00+00:00"},
            },
        )
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)
        scheduler.register(task)

        await scheduler.pause(task.task_id)

        updated = await task_manager.get_task(task.task_id)
        assert updated.params["paused"] is True
        assert updated.params["last_run"]["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_resume_recomputes_next_run(self, task_manager, fake_executor):
        """resume 置 paused=False 并按周期重算 next_run_at。"""
        task = await task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={
                "keep_days": 7,
                "schedule": {"mode": "interval", "interval_hours": 4},
                "paused": True,
                "last_run": {"next_run_at": None},
            },
        )
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)
        scheduler.register(task)

        await scheduler.resume(task.task_id, now=datetime(2026, 9, 7, 6, 0, tzinfo=UTC))

        updated = await task_manager.get_task(task.task_id)
        assert updated.params["paused"] is False
        assert updated.params["last_run"]["next_run_at"] == (
            datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
        ).isoformat()


class TestRunNow:
    """手动立即执行测试。"""

    @pytest.mark.asyncio
    async def test_run_now_submits_task(self, task_manager, fake_executor):
        """run_now 通过 submit_task 投递一轮执行。"""
        task = await task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={"keep_days": 7, "schedule": {"mode": "daily", "time": "03:00"}},
        )
        scheduler = CleanupScheduler(task_manager=task_manager, executor=fake_executor)

        await scheduler.run_now(task.task_id)

        fake_executor.submit_task.assert_called_once()
        called_task = fake_executor.submit_task.call_args[0][0]
        assert called_task.task_id == task.task_id
        assert called_task.task_type == TaskType.CLEANUP_FILES
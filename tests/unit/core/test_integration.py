# coding=UTF-8
"""集成测试：AppContext.init_task_executor 重启恢复流程。

覆盖场景（重启恢复两个盲区）：
- 崩溃遗留的 running 非监听任务在启动恢复时被标记为 failed；
- running 监听任务通过 recover_listeners() 恢复；
- 排队任务在注入 executor 后通过 resume_queued_tasks() 立即调度执行。
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from module.core.task.manager import TaskStatus, TaskType


@pytest.fixture
def db_path():
    """创建临时数据库路径。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    # Windows 下 SQLite 异步引擎关闭后可能仍有残留连接，
    # 导致 PermissionError，因此重试删除。
    import time

    for _ in range(5):
        try:
            if os.path.exists(f.name):
                os.unlink(f.name)
            break
        except PermissionError:
            time.sleep(0.1)


class FakeCleanupScheduler:
    """模拟 CleanupScheduler，仅验证启动钩子被调用。"""

    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True


class FakeTaskExecutor:
    """模拟 TaskExecutor，记录恢复与提交调用。"""

    def __init__(self):
        self.recovered = False
        self.submitted = []
        self.cleanup_scheduler = FakeCleanupScheduler()

    async def recover_listeners(self):
        self.recovered = True

    def submit_task(self, task):
        self.submitted.append(task.task_id)


class TestInitTaskExecutorRecovery:
    """测试 init_task_executor 完整启动恢复流程。"""

    @staticmethod
    def _build_ctx(db_path, task_manager):
        """构造最小化 AppContext（绕过 __init__，避免真实配置依赖）。"""
        from module.core.integration import AppContext

        config_manager = MagicMock()
        config_manager.resource_limits = {
            "max_concurrent_tasks": 2,
            "max_download_concurrency": 3,
            "max_upload_concurrency": 1,
            "max_forward_concurrency": 1,
        }
        config_manager.load_config.return_value = {"save_directory": "./downloads"}
        config_manager.save_directory = "./downloads"
        file_manager = MagicMock()
        repository_manager = MagicMock()
        repository_manager.should_use_repository.return_value = False

        ctx = AppContext.__new__(AppContext)
        ctx.db_path = db_path
        ctx.task_manager = task_manager
        ctx.file_manager = file_manager
        ctx.config_manager = config_manager
        ctx.repository_manager = repository_manager
        ctx.task_executor = None
        return ctx

    @pytest.mark.asyncio
    async def test_init_task_executor_full_recovery(self, db_path):
        """重启恢复：残留 running→failed、监听→恢复、排队→立即调度。"""
        from module.core import db
        from module.core.task.manager import TaskManager

        # 第一轮：模拟崩溃前遗留的三种状态
        await db.init_db(db_path)
        tm1 = TaskManager(max_concurrent_tasks=2)
        running_dl = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        # 第二个运行中下载任务：填满 download 并发（常驻监听任务不再占用并发槽位）
        running_dl2 = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 101, "message_range_end": 110},
        )
        listen = await tm1.create_task(
            task_type=TaskType.LISTEN_DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        queued = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 11, "message_range_end": 20},
        )
        await tm1.start_task(running_dl.task_id)  # RUNNING
        await tm1.start_task(running_dl2.task_id)  # RUNNING
        await tm1.start_task(listen.task_id)  # RUNNING
        await tm1.start_task(queued.task_id)  # QUEUED
        assert queued.status == TaskStatus.QUEUED
        await db.close_db()

        # 第二轮：完整启动恢复流程
        await db.init_db(db_path)
        tm2 = TaskManager(max_concurrent_tasks=2)
        ctx = self._build_ctx(db_path, tm2)
        fake_executor = FakeTaskExecutor()
        with patch(
            "module.core.task.executor.TaskExecutor", return_value=fake_executor
        ):
            await ctx.init_task_executor(client=MagicMock())

        # 残留 running 非监听任务被标记为 failed
        rd = await tm2.get_task(running_dl.task_id)
        assert rd is not None
        assert rd.status == TaskStatus.FAILED
        assert "重启" in (rd.error_message or "")
        rd2 = await tm2.get_task(running_dl2.task_id)
        assert rd2 is not None
        assert rd2.status == TaskStatus.FAILED

        # 监听任务保持 running，且 recover_listeners 被调用
        lk = await tm2.get_task(listen.task_id)
        assert lk is not None
        assert lk.status == TaskStatus.RUNNING
        assert fake_executor.recovered is True

        # 排队任务被立即调度并提交执行
        qk = await tm2.get_task(queued.task_id)
        assert qk is not None
        assert qk.status == TaskStatus.RUNNING
        assert queued.task_id in fake_executor.submitted

        # 定时清理调度器随启动流程启动
        assert fake_executor.cleanup_scheduler.started is True
        await db.close_db()

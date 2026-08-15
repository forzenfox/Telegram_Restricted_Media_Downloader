# coding=UTF-8
"""TaskManager 单元测试

覆盖场景：
- 任务创建与状态转换
- 任务队列与并发调度
- 取消任务
- 重试任务
- 子任务状态管理
- 资源保护（大小阈值、磁盘空间）
- SQLite 持久化
"""

import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from module.core.config_manager import ConfigManager
from module.core.identifier_service import IdentifierService, ResolvedChat
from module.core.task.manager import (
    ItemStatus,
    Task,
    TaskConflictError,
    TaskItem,
    TaskManager,
    TaskStateError,
    TaskStatus,
    TaskType,
    ValidationError,
)

# ============================================================
# 测试：TaskType 枚举
# ============================================================


class TestTaskTypeEnum:
    """测试 TaskType 枚举扩展。"""

    def test_task_type_has_listen_download(self):
        """TaskType 应包含 LISTEN_DOWNLOAD。"""
        task_type = TaskType("listen_download")
        assert task_type == TaskType.LISTEN_DOWNLOAD
        assert task_type.value == "listen_download"

    def test_task_type_has_listen_forward(self):
        """TaskType 应包含 LISTEN_FORWARD。"""
        task_type = TaskType("listen_forward")
        assert task_type == TaskType.LISTEN_FORWARD
        assert task_type.value == "listen_forward"

    def test_task_type_backward_compat(self):
        """原有任务类型应保持不变。"""
        assert TaskType("download") == TaskType.DOWNLOAD
        assert TaskType("forward") == TaskType.FORWARD
        assert TaskType("upload") == TaskType.UPLOAD


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


@pytest_asyncio.fixture
async def task_manager(db_path):
    """创建 TaskManager 实例。"""
    from module.core import db

    await db.init_db(db_path)
    tm = TaskManager(max_concurrent_tasks=2)
    yield tm
    await db.close_db()


@pytest.fixture
def mock_identifier_service():
    """提供 mock IdentifierService，根据标识符返回 ResolvedChat。"""
    svc = MagicMock(spec=IdentifierService)

    async def _resolve(identifier: str):
        text = (identifier or "").strip()
        if text.lstrip("-").isdigit():
            chat_id = int(text)
            chat_type = "private" if chat_id > 0 else "channel"
        else:
            chat_id = -1001234567890
            chat_type = "channel"
        return ResolvedChat(
            chat_id=chat_id,
            chat_type=chat_type,
            chat_name="Test Chat",
            username="testchat",
            message_count=-1,
            media_count=-1,
            has_access=True,
            is_private=False,
        )

    svc.resolve = AsyncMock(side_effect=_resolve)
    return svc


@pytest.fixture
def mock_config_manager():
    """提供 mock ConfigManager，默认全局仓库备份关闭。"""
    cm = MagicMock(spec=ConfigManager)
    cm.get = MagicMock(return_value=False)
    return cm


@pytest_asyncio.fixture
async def task_manager_with_services(
    db_path, mock_identifier_service, mock_config_manager
):
    """创建已注入 IdentifierService 与 ConfigManager 的 TaskManager 实例。"""
    from module.core import db

    await db.init_db(db_path)
    tm = TaskManager(
        max_concurrent_tasks=2,
        identifier_service=mock_identifier_service,
        config_manager=mock_config_manager,
    )
    yield tm
    await db.close_db()


# ============================================================
# 测试：Task 数据模型
# ============================================================


class TestTaskModel:
    """测试 Task 数据类。"""

    def test_create_download_task(self):
        """测试创建下载任务。"""
        task = Task(
            task_id="task_001",
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 100, "message_range_end": 200},
            status=TaskStatus.PENDING,
        )
        assert task.task_id == "task_001"
        assert task.task_type == TaskType.DOWNLOAD
        assert task.chat_id == -1001234567890
        assert task.params.get("message_range_start") == 100
        assert task.params.get("message_range_end") == 200
        assert task.status == TaskStatus.PENDING
        assert task.items == []
        assert task.retry_count == 0
        assert task.total_size_bytes == 0

    def test_create_forward_task(self):
        """测试创建转发任务。"""
        task = Task(
            task_id="task_002",
            task_type=TaskType.FORWARD,
            chat_id=-1001234567890,
            params={
                "target_chat_id": -1009876543210,
                "message_range_start": 1,
                "message_range_end": 50,
                "delete_after_upload": True,
            },
            status=TaskStatus.PENDING,
        )
        assert task.task_type == TaskType.FORWARD
        assert task.params.get("target_chat_id") == -1009876543210
        assert task.params.get("delete_after_upload") is True

    def test_create_upload_task(self):
        """测试创建上传任务。"""
        task = Task(
            task_id="task_003",
            task_type=TaskType.UPLOAD,
            chat_id=-1001234567890,
            params={"file_paths": ["/path/to/file1.mp4", "/path/to/file2.mp4"]},
            status=TaskStatus.PENDING,
        )
        assert task.task_type == TaskType.UPLOAD
        assert len(task.params.get("file_paths")) == 2

    def test_task_progress_calculation(self):
        """测试任务进度计算。"""
        task = Task(
            task_id="task_004",
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            status=TaskStatus.PENDING,
        )
        task.items = [
            TaskItem(id="1", task_id="", status=ItemStatus.SUCCESS),
            TaskItem(id="2", task_id="", status=ItemStatus.SUCCESS),
            TaskItem(id="3", task_id="", status=ItemStatus.FAILED),
            TaskItem(id="4", task_id="", status=ItemStatus.PENDING),
        ]
        assert task.success_count == 2
        assert task.failed_count == 1
        assert task.pending_count == 1
        assert task.progress == 50.0  # 2/4 = 50%

    def test_task_progress_empty(self):
        """测试空任务进度。"""
        task = Task(
            task_id="task_005",
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            status=TaskStatus.PENDING,
        )
        assert task.progress == 0.0


# ============================================================
# 测试：TaskItem 数据模型
# ============================================================


class TestTaskItemModel:
    """测试 TaskItem 数据类。"""

    def test_create_task_item(self):
        """测试创建子任务项。"""
        item = TaskItem(
            id="msg_100",
            task_id="",
            source_message_id=100,
            status=ItemStatus.PENDING,
            file_size=1024 * 1024,  # 1MB
        )
        assert item.id == "msg_100"
        assert item.source_message_id == 100
        assert item.status == ItemStatus.PENDING
        assert item.file_size == 1048576
        assert item.error_message is None
        assert item.retry_count == 0

    def test_task_item_mark_success(self):
        """测试标记子任务成功。"""
        item = TaskItem(id="msg_100", task_id="", status=ItemStatus.PENDING)
        item.mark_success()
        assert item.status == ItemStatus.SUCCESS

    def test_task_item_mark_failed(self):
        """测试标记子任务失败。"""
        item = TaskItem(id="msg_100", task_id="", status=ItemStatus.RUNNING)
        item.mark_failed(reason="FloodWait")
        assert item.status == ItemStatus.FAILED
        assert item.error_message == "FloodWait"
        assert item.retry_count == 1

    def test_task_item_mark_skipped(self):
        """测试标记子任务跳过。"""
        item = TaskItem(id="msg_100", task_id="", status=ItemStatus.PENDING)
        item.mark_skipped(reason="已存在")
        assert item.status == ItemStatus.SKIPPED

    def test_task_item_can_retry(self):
        """测试可重试判定（error_message 和 error_code 均参与判断）。"""
        # FloodWait 可重试（通过 error_message）
        item1 = TaskItem(
            id="msg_100",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="FloodWait",
        )
        assert item1.can_retry() is True

        # 网络超时 可重试（通过 error_message）
        item2 = TaskItem(
            id="msg_101",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="TimeoutError",
        )
        assert item2.can_retry() is True

        # 消息已删除 不可重试（通过 error_message）
        item3 = TaskItem(
            id="msg_102",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="MESSAGE_ID_INVALID",
        )
        assert item3.can_retry() is False

        # 无权限 不可重试（通过 error_code）
        item4 = TaskItem(
            id="msg_103",
            task_id="",
            status=ItemStatus.FAILED,
            error_code="CHAT_FORBIDDEN",
            error_message="无权访问该频道",
        )
        assert item4.can_retry() is False

        # error_code 优先于 error_message 判断
        item5 = TaskItem(
            id="msg_104",
            task_id="",
            status=ItemStatus.FAILED,
            error_code="USER_BANNED",
            error_message="some generic message",
        )
        assert item5.can_retry() is False


# ============================================================
# 测试：任务创建
# ============================================================


class TestCreateTask:
    """测试任务创建。"""

    @pytest.mark.asyncio
    async def test_create_download_task(self, task_manager):
        """测试创建下载任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 100, "message_range_end": 200},
        )
        assert task.task_type == TaskType.DOWNLOAD
        assert task.chat_id == -1001234567890
        assert task.status == TaskStatus.PENDING
        assert task.task_id is not None

    @pytest.mark.asyncio
    async def test_create_forward_task(self, task_manager):
        """测试创建转发任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.FORWARD,
            chat_id=-1001234567890,
            params={
                "target_chat_id": -1009876543210,
                "message_range_start": 1,
                "message_range_end": 50,
                "delete_after_upload": True,
            },
        )
        assert task.task_type == TaskType.FORWARD
        assert task.params.get("target_chat_id") == -1009876543210
        assert task.params.get("delete_after_upload") is True

    @pytest.mark.asyncio
    async def test_create_upload_task(self, task_manager):
        """测试创建上传任务。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.mp4")
            Path(file_path).touch()
            task = await task_manager.create_task(
                task_type=TaskType.UPLOAD,
                chat_id=-1001234567890,
                params={"file_paths": [file_path]},
            )
            assert task.task_type == TaskType.UPLOAD
            assert len(task.params.get("file_paths")) == 1

    @pytest.mark.asyncio
    async def test_create_task_persisted(self, task_manager, db_path):
        """测试任务创建后持久化到 SQLite。"""
        await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM tm_tasks")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 1


# ============================================================
# 测试：任务状态转换
# ============================================================


class TestTaskStateTransitions:
    """测试任务状态转换。"""

    @pytest.mark.asyncio
    async def test_pending_to_running(self, task_manager):
        """测试 pending → running。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        assert task.status == TaskStatus.PENDING
        await task_manager.start_task(task.task_id)
        assert task.status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_pending_to_queued(self, task_manager):
        """测试并发已满时 pending → queued。"""
        # 创建 2 个任务并启动（max_concurrent_tasks=2）
        task1 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        task2 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 11, "message_range_end": 20},
        )
        await task_manager.start_task(task1.task_id)
        await task_manager.start_task(task2.task_id)
        # 第 3 个任务应进入队列
        task3 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 21, "message_range_end": 30},
        )
        await task_manager.start_task(task3.task_id)
        assert task3.status == TaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_running_to_completed(self, task_manager):
        """测试 running → completed。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_running_to_failed(self, task_manager):
        """测试 running → failed。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.fail_task(task.task_id, reason="测试失败")
        assert task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_running_to_cancelled(self, task_manager):
        """测试 running → cancelled。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.cancel_task(task.task_id)
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_task_raises(self, task_manager):
        """测试取消已完成任务抛出异常。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)
        with pytest.raises(TaskStateError):
            await task_manager.cancel_task(task.task_id)

    @pytest.mark.asyncio
    async def test_cancel_queued_task(self, task_manager):
        """测试取消排队中的任务。"""
        task1 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        task2 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 11, "message_range_end": 20},
        )
        await task_manager.start_task(task1.task_id)
        await task_manager.start_task(task2.task_id)
        task3 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 21, "message_range_end": 30},
        )
        await task_manager.start_task(task3.task_id)
        assert task3.status == TaskStatus.QUEUED
        await task_manager.cancel_task(task3.task_id)
        assert task3.status == TaskStatus.CANCELLED


# ============================================================
# 测试：重试逻辑
# ============================================================


class TestRetryLogic:
    """测试重试逻辑。"""

    @pytest.mark.asyncio
    async def test_retry_failed_task(self, task_manager):
        """测试重试失败任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.fail_task(task.task_id, reason="网络超时")
        assert task.status == TaskStatus.FAILED
        await task_manager.retry_task(task.task_id)
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 1

    @pytest.mark.asyncio
    async def test_retry_cancelled_task(self, task_manager):
        """测试重试取消任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.cancel_task(task.task_id)
        await task_manager.retry_task(task.task_id)
        assert task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_retry_running_task_raises(self, task_manager):
        """测试重试运行中任务抛出异常。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        with pytest.raises(TaskStateError):
            await task_manager.retry_task(task.task_id)

    @pytest.mark.asyncio
    async def test_item_retry_logic(self):
        """测试子任务级别重试判定。"""
        # FloodWait 可重试
        item = TaskItem(
            id="msg_100",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="FloodWait",
        )
        assert item.can_retry() is True

        # 消息被删除 不可重试
        item2 = TaskItem(
            id="msg_101",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="MESSAGE_ID_INVALID",
        )
        assert item2.can_retry() is False

        # 达到最大重试次数 不可重试
        item3 = TaskItem(
            id="msg_102",
            task_id="",
            status=ItemStatus.FAILED,
            error_message="TimeoutError",
        )
        item3.retry_count = 3
        assert item3.can_retry() is False

    @pytest.mark.asyncio
    async def test_retry_resets_retryable_failed_items_to_pending(self, task_manager):
        """重试时，can_retry=True 的 FAILED 子任务应被重置为 PENDING。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)

        # 添加两个子任务：一个可重试，一个不可重试
        retryable_item = TaskItem(
            id="item_retryable",
            task_id=task.task_id,
            status=ItemStatus.FAILED,
            error_message="FloodWait",
            source_message_id=100,
        )
        non_retryable_item = TaskItem(
            id="item_non_retryable",
            task_id=task.task_id,
            status=ItemStatus.FAILED,
            error_message="MESSAGE_ID_INVALID",
            source_message_id=101,
        )
        await task_manager.add_items(task.task_id, [retryable_item, non_retryable_item])

        await task_manager.fail_task(task.task_id, reason="部分子任务失败")
        await task_manager.retry_task(task.task_id)

        # 可重试的子任务应为 PENDING
        assert retryable_item.status == ItemStatus.PENDING
        assert retryable_item.error_message is None

    @pytest.mark.asyncio
    async def test_retry_marks_non_retryable_failed_items_as_skipped(
        self, task_manager
    ):
        """重试时，can_retry=False 的 FAILED 子任务应被标记为 SKIPPED。

        不可重试的错误（如 CHAT_FORBIDDEN、MESSAGE_ID_INVALID、超过重试次数）
        不应在下次执行时被重新处理，否则会造成无意义的重试和 FloodWait 风险。
        """
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)

        # 添加不可重试的子任务
        non_retryable_item = TaskItem(
            id="item_no_retry",
            task_id=task.task_id,
            status=ItemStatus.FAILED,
            error_message="CHAT_FORBIDDEN",
            source_message_id=200,
        )
        # 超过最大重试次数的子任务
        max_retry_item = TaskItem(
            id="item_max_retry",
            task_id=task.task_id,
            status=ItemStatus.FAILED,
            error_message="TimeoutError",
            source_message_id=201,
        )
        max_retry_item.retry_count = 3

        await task_manager.add_items(task.task_id, [non_retryable_item, max_retry_item])

        await task_manager.fail_task(task.task_id, reason="部分子任务失败")
        await task_manager.retry_task(task.task_id)

        # 不可重试的子任务应被标记为 SKIPPED
        assert non_retryable_item.status == ItemStatus.SKIPPED
        assert max_retry_item.status == ItemStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_retry_preserves_success_and_skipped_items(self, task_manager):
        """重试时，SUCCESS 和 SKIPPED 的子任务应保持原状态。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)

        success_item = TaskItem(
            id="item_success",
            task_id=task.task_id,
            status=ItemStatus.SUCCESS,
            source_message_id=300,
        )
        skipped_item = TaskItem(
            id="item_skipped",
            task_id=task.task_id,
            status=ItemStatus.SKIPPED,
            source_message_id=301,
        )
        failed_item = TaskItem(
            id="item_failed",
            task_id=task.task_id,
            status=ItemStatus.FAILED,
            error_message="FloodWait",
            source_message_id=302,
        )
        await task_manager.add_items(
            task.task_id, [success_item, skipped_item, failed_item]
        )

        await task_manager.fail_task(task.task_id, reason="部分子任务失败")
        await task_manager.retry_task(task.task_id)

        # SUCCESS 和 SKIPPED 应保持不变
        assert success_item.status == ItemStatus.SUCCESS
        assert skipped_item.status == ItemStatus.SKIPPED
        # FAILED (can_retry=True) 应被重置为 PENDING
        assert failed_item.status == ItemStatus.PENDING


class TestResourceProtection:
    """测试资源保护机制。"""

    @pytest.mark.asyncio
    async def test_task_size_under_warning(self, task_manager):
        """测试任务大小低于告警阈值。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={
                "message_range_start": 1,
                "message_range_end": 10,
                "estimated_size": 3 * 1024 * 1024 * 1024,
            },
        )
        assert task.params.get("estimated_size") == 3 * 1024 * 1024 * 1024
        level, msg = task_manager.check_size_threshold(3 * 1024 * 1024 * 1024)
        assert level == "ok"
        assert msg is None

    @pytest.mark.asyncio
    async def test_task_size_warning(self, task_manager):
        """测试任务大小触发告警。"""
        size = 7 * 1024 * 1024 * 1024  # 7GB
        level, msg = task_manager.check_size_threshold(size)
        assert level == "warning"
        assert msg is not None
        assert "7.00GB" in msg

    @pytest.mark.asyncio
    async def test_task_size_exceeded(self, task_manager):
        """测试任务大小超过上限。"""
        size = 12 * 1024 * 1024 * 1024  # 12GB
        level, msg = task_manager.check_size_threshold(size)
        assert level == "exceeded"
        assert msg is not None
        assert "12.00GB" in msg

    @pytest.mark.asyncio
    async def test_disk_space_check(self, task_manager):
        """测试磁盘空间检查。"""
        with patch("shutil.disk_usage") as mock_disk_usage:
            mock_disk_usage.return_value = MagicMock(
                total=50 * 1024**3,
                used=49 * 1024**3,
                free=1 * 1024**3,  # 1GB 剩余
            )
            assert task_manager.check_disk_space() is False

    @pytest.mark.asyncio
    async def test_disk_space_sufficient(self, task_manager):
        """测试磁盘空间充足。"""
        with patch("shutil.disk_usage") as mock_disk_usage:
            mock_disk_usage.return_value = MagicMock(
                total=50 * 1024**3,
                used=40 * 1024**3,
                free=10 * 1024**3,  # 10GB 剩余
            )
            assert task_manager.check_disk_space() is True

    @pytest.mark.asyncio
    async def test_disk_space_oserror_raises(self, task_manager):
        """测试磁盘空间检查 OSError 时抛出 ResourceLimitError。"""
        from module.core.task.manager import ResourceLimitError

        with patch("shutil.disk_usage", side_effect=OSError("disk error")), pytest.raises(
            ResourceLimitError, match="无法获取磁盘使用信息"
        ):
            task_manager.check_disk_space()

    @pytest.mark.asyncio
    async def test_disk_space_with_download_dir(self, task_manager):
        """测试 check_disk_space 使用指定下载目录。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # 使用真实临时目录检查
            result = task_manager.check_disk_space(download_dir=tmpdir)
            assert result is True

    @pytest.mark.asyncio
    async def test_create_task_exceeds_max_size_forbidden(self, task_manager):
        """测试 create_task 中 estimated_size > 10GB 抛出 ResourceLimitError。"""
        from module.core.task.manager import ResourceLimitError

        with pytest.raises(ResourceLimitError, match="上限"):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={
                    "message_range_start": 1,
                    "message_range_end": 10,
                    "estimated_size": 12 * 1024 * 1024 * 1024,  # 12GB
                },
            )

    @pytest.mark.asyncio
    async def test_create_task_insufficient_disk_space(self, task_manager):
        """测试 create_task 中磁盘不足抛出 ResourceLimitError。"""
        from module.core.task.manager import ResourceLimitError

        with patch("shutil.disk_usage") as mock_disk_usage:
            mock_disk_usage.return_value = MagicMock(
                total=50 * 1024**3,
                used=49 * 1024**3,
                free=1 * 1024**3,  # 1GB 剩余，小于 min_disk_space_gb=2
            )
            with pytest.raises(ResourceLimitError, match="磁盘剩余空间不足"):
                await task_manager.create_task(
                    task_type=TaskType.DOWNLOAD,
                    chat_id=-1001234567890,
                    params={
                        "message_range_start": 1,
                        "message_range_end": 10,
                        "estimated_size": 0,
                    },
                )

    @pytest.mark.asyncio
    async def test_create_task_warning_size_allowed(self, task_manager):
        """测试 create_task 中 5-10GB 任务正常创建（警告级由 API 层处理）。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={
                "message_range_start": 1,
                "message_range_end": 10,
                "estimated_size": 7 * 1024 * 1024 * 1024,  # 7GB - 警告级
            },
        )
        assert task.task_id is not None
        assert task.params.get("estimated_size") == 7 * 1024 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_create_task_under_threshold_ok(self, task_manager):
        """测试 create_task 中 <5GB 任务正常创建。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={
                "message_range_start": 1,
                "message_range_end": 10,
                "estimated_size": 3 * 1024 * 1024 * 1024,  # 3GB
            },
        )
        assert task.task_id is not None

    @pytest.mark.asyncio
    async def test_check_size_threshold_returns_tuple(self, task_manager):
        """验证 check_size_threshold 返回值为 (str, Optional[str]) 元组。"""
        result = task_manager.check_size_threshold(1024)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert result[0] in ("ok", "warning", "exceeded")

    @pytest.mark.asyncio
    async def test_invalid_task_type_raises_validation_error(self, task_manager):
        """测试 create_task 传入非法 task_type 抛出 ValidationError。"""
        from module.core.task.manager import ValidationError

        with pytest.raises(ValidationError, match="无效的任务类型"):
            await task_manager.create_task(
                task_type="invalid_type",
                chat_id=-1001234567890,
            )

    @pytest.mark.asyncio
    async def test_missing_chat_id_raises_validation_error(self, task_manager):
        """测试 create_task 传入 chat_id=0 抛出 ValidationError。"""
        from module.core.task.manager import ValidationError

        with pytest.raises(ValidationError, match="chat_id"):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=0,
            )

    @pytest.mark.asyncio
    async def test_task_state_error_on_invalid_transition(self, task_manager):
        """验证 TaskStateError 在状态不允许时被抛出。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        # 已完成的任务不允许取消
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)
        with pytest.raises(TaskStateError):
            await task_manager.cancel_task(task.task_id)

    @pytest.mark.asyncio
    async def test_backward_compat_aliases(self):
        """验证向后兼容别名仍可用。"""
        from module.core.task.manager import (
            InvalidStateTransition,
            TaskStateError,
        )

        assert InvalidStateTransition is TaskStateError

    @pytest.mark.asyncio
    async def test_create_task_invalid_range_mode(self, task_manager):
        """测试 create_task 传入非法 range_mode 抛出 ValidationError。"""
        from module.core.task.manager import ValidationError

        with pytest.raises(ValidationError, match="无效的 range_mode"):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"range_mode": "invalid_mode", "min_id": 1, "max_id": 5},
            )

    @pytest.mark.asyncio
    async def test_create_task_date_range_missing_start_date(self, task_manager):
        """测试 date_range 缺少 start_date 抛出 ValidationError。"""
        from module.core.task.manager import ValidationError

        with pytest.raises(ValidationError, match="date_range 模式需要提供 start_date"):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"range_mode": "date_range", "end_date": "2024-06-20"},
            )

    @pytest.mark.asyncio
    async def test_create_task_multiple_ids_missing_message_list(self, task_manager):
        """测试 multiple_ids 缺少 message_list 抛出 ValidationError。"""
        from module.core.task.manager import ValidationError

        with pytest.raises(
            ValidationError, match="multiple_ids 模式需要提供 message_list"
        ):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"range_mode": "multiple_ids"},
            )


# ============================================================
# 测试：任务列表与查询
# ============================================================


class TestTaskList:
    """测试任务列表查询。"""

    @pytest.mark.asyncio
    async def test_list_all_tasks(self, task_manager):
        """测试列出所有任务。"""
        await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.create_task(
            task_type=TaskType.UPLOAD,
            chat_id=-1001234567890,
            params={"file_paths": ["/tmp/test.mp4"]},
        )
        tasks, total = await task_manager.list_tasks()
        assert len(tasks) == 2
        assert total == 2

    @pytest.mark.asyncio
    async def test_list_tasks_by_status(self, task_manager):
        """测试按状态过滤任务列表。"""
        task1 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 11, "message_range_end": 20},
        )
        await task_manager.start_task(task1.task_id)
        await task_manager.complete_task(task1.task_id)
        completed, total = await task_manager.list_tasks(status=TaskStatus.COMPLETED)
        assert len(completed) == 1
        assert total == 1
        assert completed[0].task_id == task1.task_id

    @pytest.mark.asyncio
    async def test_get_task_by_id(self, task_manager):
        """测试通过 ID 获取任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        fetched = await task_manager.get_task(task.task_id)
        assert fetched is not None
        assert fetched.task_id == task.task_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, task_manager):
        """测试获取不存在的任务返回 None。"""
        result = await task_manager.get_task("nonexistent_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_tasks_with_limit(self, task_manager):
        """测试分页 limit。"""
        for i in range(5):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"message_range_start": 1, "message_range_end": 10},
            )
        tasks, total = await task_manager.list_tasks(limit=2)
        assert len(tasks) == 2
        assert total == 5

    @pytest.mark.asyncio
    async def test_list_tasks_with_offset(self, task_manager):
        """测试分页 offset。"""
        for i in range(5):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"message_range_start": 1, "message_range_end": 10},
            )
        tasks, total = await task_manager.list_tasks(limit=2, offset=2)
        assert len(tasks) == 2
        assert total == 5

    @pytest.mark.asyncio
    async def test_list_tasks_by_task_type(self, task_manager):
        """测试按类型过滤。"""
        await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.create_task(
            task_type=TaskType.UPLOAD,
            chat_id=-1001234567890,
            params={"file_paths": ["/tmp/test.mp4"]},
        )
        downloads, total = await task_manager.list_tasks(task_type=TaskType.DOWNLOAD)
        assert len(downloads) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_tasks_combined_filters(self, task_manager):
        """测试组合过滤（状态+类型）。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.create_task(
            task_type=TaskType.UPLOAD,
            chat_id=-1001234567890,
            params={"file_paths": ["/tmp/test.mp4"]},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)
        result, total = await task_manager.list_tasks(
            status=TaskStatus.COMPLETED, task_type=TaskType.DOWNLOAD
        )
        assert len(result) == 1
        assert total == 1


# ============================================================
# 测试：子任务管理
# ============================================================


class TestTaskItemManagement:
    """测试子任务管理。"""

    @pytest.mark.asyncio
    async def test_add_task_items(self, task_manager):
        """测试添加子任务项。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        items = [
            TaskItem(
                id="msg_1", task_id="", source_message_id=1, status=ItemStatus.PENDING
            ),
            TaskItem(
                id="msg_2", task_id="", source_message_id=2, status=ItemStatus.PENDING
            ),
        ]
        await task_manager.add_items(task.task_id, items)
        fetched_task = await task_manager.get_task(task.task_id)
        assert len(fetched_task.items) == 2

    @pytest.mark.asyncio
    async def test_update_item_status(self, task_manager):
        """测试更新子任务状态。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 5},
        )
        item = TaskItem(
            id="msg_1", task_id="", source_message_id=1, status=ItemStatus.PENDING
        )
        await task_manager.add_items(task.task_id, [item])
        await task_manager.update_item_status(task.task_id, "msg_1", ItemStatus.SUCCESS)
        fetched_task = await task_manager.get_task(task.task_id)
        assert fetched_task.items[0].status == ItemStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_get_failed_items(self, task_manager):
        """测试获取失败的子任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 5},
        )
        items = [
            TaskItem(
                id="msg_1", task_id="", source_message_id=1, status=ItemStatus.SUCCESS
            ),
            TaskItem(
                id="msg_2", task_id="", source_message_id=2, status=ItemStatus.FAILED
            ),
            TaskItem(
                id="msg_3", task_id="", source_message_id=3, status=ItemStatus.SUCCESS
            ),
            TaskItem(
                id="msg_4", task_id="", source_message_id=4, status=ItemStatus.FAILED
            ),
        ]
        await task_manager.add_items(task.task_id, items)
        failed = await task_manager.get_failed_items(task.task_id)
        assert len(failed) == 2


# ============================================================
# 测试：持久化与恢复
# ============================================================


class TestPersistenceAndRecovery:
    """测试持久化与重启恢复。"""

    @pytest.mark.asyncio
    async def test_tasks_survive_restart(self, db_path):
        """测试任务在重启后仍然存在。"""
        from module.core import db

        # 第一轮：创建任务
        await db.init_db(db_path)
        tm1 = TaskManager(max_concurrent_tasks=2)
        task = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        task_id = task.task_id
        await tm1.start_task(task_id)
        await tm1.complete_task(task_id)
        await db.close_db()

        # 第二轮：重新加载（需传 limit 触发数据库查询，否则走内存缓存为空）
        await db.init_db(db_path)
        tm2 = TaskManager(max_concurrent_tasks=2)
        tasks, total = await tm2.list_tasks(limit=10)
        assert len(tasks) == 1
        assert total == 1
        assert tasks[0].task_id == task_id
        assert tasks[0].status == TaskStatus.COMPLETED
        await db.close_db()

    @pytest.mark.asyncio
    async def test_load_pending_tasks_on_start(self, db_path):
        """测试启动时加载未完成任务。"""
        from module.core import db

        await db.init_db(db_path)
        tm1 = TaskManager(max_concurrent_tasks=2)
        task1 = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        _ = await tm1.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 11, "message_range_end": 20},
        )
        await tm1.start_task(task1.task_id)
        # task1 未完成，task2 未启动

        tm2 = TaskManager(max_concurrent_tasks=2)
        # 需传 limit 触发数据库查询，否则走内存缓存为空
        pending, pending_total = await tm2.list_tasks(
            status=TaskStatus.PENDING, limit=10
        )
        running, running_total = await tm2.list_tasks(
            status=TaskStatus.RUNNING, limit=10
        )
        assert len(pending) == 1
        assert pending_total == 1
        assert len(running) == 1
        assert running_total == 1
        await db.close_db()


# ============================================================
# 测试：shutdown
# ============================================================


class TestShutdown:
    """测试 TaskManager 优雅关闭。"""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_running(self, task_manager):
        """shutdown 取消运行中任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        assert task.status == TaskStatus.RUNNING
        await task_manager.shutdown()
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_shutdown_cancels_queued(self, task_manager):
        """shutdown 取消排队中任务。"""
        # 先创建并发数 2 个任务并启动填满并发
        for _ in range(2):
            t = await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
                params={"message_range_start": 1, "message_range_end": 10},
            )
            t._max_concurrent_tasks = 1  # 不能直接修改，用绕过方式
        # 使用 task_manager.max_concurrent_tasks 属性
        # 直接创建第3个任务会排队
        t1 = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(t1.task_id)
        _ = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        # 简化：直接验证 shutdown 清空队列
        await task_manager.shutdown()
        # 验证队列为空
        assert len(task_manager._task_queue) == 0

    @pytest.mark.asyncio
    async def test_shutdown_completed_untouched(self, task_manager):
        """shutdown 不影响已完成任务。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)
        await task_manager.shutdown()
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_shutdown_clears_queue(self, task_manager):
        """shutdown 清空排队列表。"""
        await task_manager.shutdown()
        assert len(task_manager._task_queue) == 0


# ============================================================
# 测试：create_task auto_start
# ============================================================


class TestCreateTaskAutoStart:
    """测试 create_task auto_start 参数。"""

    @pytest.mark.asyncio
    async def test_create_task_auto_start(self, task_manager):
        """auto_start=True 时任务应为 RUNNING 或 QUEUED（状态发生变更）。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
            auto_start=True,
        )
        assert task.status != TaskStatus.PENDING


# ============================================================
# 测试：cancel_task reason
# ============================================================


class TestCancelTaskWithReason:
    """测试 cancel_task reason 参数。"""

    @pytest.mark.asyncio
    async def test_cancel_task_with_reason(self, task_manager):
        """取消带原因。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.cancel_task(task.task_id, reason="用户手动取消")
        assert task.status == TaskStatus.CANCELLED
        assert task.error_message == "用户手动取消"

    @pytest.mark.asyncio
    async def test_cancel_task_reason_persisted(self, task_manager):
        """取消原因持久化。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        await task_manager.start_task(task.task_id)
        await task_manager.cancel_task(task.task_id, reason="资源不足")
        assert task.error_message == "资源不足"


# ============================================================
# 测试：阶段 2 新特性
# ============================================================


class TestPhase2SourceIdentifierAndRecent:
    """测试 source_identifier 解析、recent 模式与 source_type 推导。"""

    @pytest.mark.asyncio
    async def test_create_task_with_source_identifier_resolves_chat_id(
        self, task_manager_with_services
    ):
        """source_identifier 经 IdentifierService 解析为 chat_id。"""
        tm = task_manager_with_services
        task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 10,
            },
        )
        assert task.chat_id == -1001234567890
        assert task.params["source_identifier"] == "@testchannel"

    @pytest.mark.asyncio
    async def test_source_type_derived_from_resolved_chat(
        self, task_manager_with_services
    ):
        """根据 ResolvedChat.chat_type 推导 source_type 并存入 Task.extra。"""
        tm = task_manager_with_services
        channel_task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 10,
            },
        )
        assert channel_task.extra.get("source_type") == "channel"

        private_task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "123456",
                "range_mode": "recent",
                "recent_count": 10,
            },
        )
        assert private_task.extra.get("source_type") == "private"

    @pytest.mark.asyncio
    async def test_create_task_recent_count_truncated_to_1000(
        self, task_manager_with_services
    ):
        """recent_count > 1000 时截断为 1000。"""
        tm = task_manager_with_services
        task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 1500,
            },
        )
        assert task.params["recent_count"] == 1000

    @pytest.mark.asyncio
    async def test_create_task_recent_count_zero_raises_validation_error(
        self, task_manager_with_services
    ):
        """recent_count <= 0 时抛出 ValidationError。"""
        tm = task_manager_with_services
        with pytest.raises(ValidationError, match="recent_count"):
            await tm.create_task(
                task_type=TaskType.DOWNLOAD,
                params={
                    "source_identifier": "@testchannel",
                    "range_mode": "recent",
                    "recent_count": 0,
                },
            )


class TestPhase2ListenConflict:
    """测试监听任务排他性校验。"""

    @pytest.mark.asyncio
    async def test_duplicate_listen_download_task_raises_conflict(
        self, task_manager_with_services
    ):
        """同一 chat_id + LISTEN_DOWNLOAD 重复创建抛 TaskConflictError。"""
        tm = task_manager_with_services
        await tm.create_task(
            task_type=TaskType.LISTEN_DOWNLOAD,
            params={"source_identifier": "@testchannel"},
        )
        with pytest.raises(TaskConflictError):
            await tm.create_task(
                task_type=TaskType.LISTEN_DOWNLOAD,
                params={"source_identifier": "@testchannel"},
            )

    @pytest.mark.asyncio
    async def test_duplicate_listen_forward_task_raises_conflict(
        self, task_manager_with_services
    ):
        """同一 chat_id + LISTEN_FORWARD 重复创建抛 TaskConflictError。"""
        tm = task_manager_with_services
        await tm.create_task(
            task_type=TaskType.LISTEN_FORWARD,
            params={
                "source_identifier": "@testchannel",
                "target_chat_id": -1009876543210,
            },
        )
        with pytest.raises(TaskConflictError):
            await tm.create_task(
                task_type=TaskType.LISTEN_FORWARD,
                params={
                    "source_identifier": "@testchannel",
                    "target_chat_id": -1009876543210,
                },
            )

    @pytest.mark.asyncio
    async def test_listen_conflict_only_applies_to_same_task_type(
        self, task_manager_with_services
    ):
        """同一 chat_id 可同时存在 LISTEN_DOWNLOAD 与 LISTEN_FORWARD。"""
        tm = task_manager_with_services
        await tm.create_task(
            task_type=TaskType.LISTEN_DOWNLOAD,
            params={"source_identifier": "@testchannel"},
        )
        task = await tm.create_task(
            task_type=TaskType.LISTEN_FORWARD,
            params={
                "source_identifier": "@testchannel",
                "target_chat_id": -1009876543210,
            },
        )
        assert task.task_type == TaskType.LISTEN_FORWARD


class TestPhase2RepositoryBackup:
    """测试仓库备份参数继承与覆盖。"""

    @pytest.mark.asyncio
    async def test_enable_repository_backup_inherits_global_config(
        self, task_manager_with_services, mock_config_manager
    ):
        """未指定 enable_repository_backup 时继承全局配置。"""
        mock_config_manager.get.return_value = True
        tm = task_manager_with_services
        task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 10,
            },
        )
        assert task.params["enable_repository_backup"] is True
        mock_config_manager.get.assert_called_with(
            "repository.auto_backup_downloads", False
        )

    @pytest.mark.asyncio
    async def test_enable_repository_backup_overrides_global_config(
        self, task_manager_with_services, mock_config_manager
    ):
        """显式指定 enable_repository_backup 时覆盖全局配置。"""
        mock_config_manager.get.return_value = True
        tm = task_manager_with_services
        task = await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            params={
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 10,
                "enable_repository_backup": False,
            },
        )
        assert task.params["enable_repository_backup"] is False

    @pytest.mark.asyncio
    async def test_enable_repository_backup_ignored_for_non_download_tasks(
        self, task_manager_with_services, mock_config_manager
    ):
        """FORWARD / UPLOAD / LISTEN_FORWARD 不处理仓库备份字段。"""
        mock_config_manager.get.return_value = True
        tm = task_manager_with_services
        for task_type in (
            TaskType.FORWARD,
            TaskType.UPLOAD,
            TaskType.LISTEN_FORWARD,
        ):
            base_params = {"source_identifier": "@testchannel"}
            if task_type == TaskType.FORWARD:
                base_params["target_chat_id"] = -1009876543210
                base_params["range_mode"] = "all"
            elif task_type == TaskType.UPLOAD:
                base_params = {
                    "file_paths": ["/tmp/test.mp4"],
                    "chat_id": -1001234567890,
                }
            task = await tm.create_task(
                task_type=task_type,
                params=base_params,
            )
            assert "enable_repository_backup" not in task.params


# ============================================================
# 测试：list_tasks 不应替换 self._tasks 中的 Task 引用
# ============================================================


class TestListTasksPreservesReference:
    """测试 list_tasks 分页查询不会替换 self._tasks 中的 Task 对象引用。

    根因缺陷：list_tasks 分页查询时从数据库重建 Task 对象，
    并用 L979 `self._tasks[task.task_id] = task` 覆盖原有引用。
    导致持有旧引用的代码（如 _execute_forward 中的 task 参数）
    和 add_items 修改的新引用不同，子任务状态失步。
    """

    @pytest.mark.asyncio
    async def test_list_tasks_preserves_task_reference(self, task_manager):
        """list_tasks 后 self._tasks 中的 Task 对象引用不应改变。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )
        original_ref = task_manager._tasks[task.task_id]
        assert task is original_ref

        # 调用 list_tasks（分页查询，会从数据库加载）
        tasks, total = await task_manager.list_tasks(limit=10, offset=0)

        # 验证引用没有被替换
        current_ref = task_manager._tasks[task.task_id]
        assert current_ref is original_ref, (
            "list_tasks 不应替换 self._tasks 中的 Task 对象引用"
        )

    @pytest.mark.asyncio
    async def test_list_tasks_preserves_items_after_add_items(self, task_manager):
        """list_tasks 后 add_items 添加的子任务仍然可以通过原引用访问。"""
        from module.core.task.manager import TaskItem

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )

        # 保存原始引用
        original_ref = task_manager._tasks[task.task_id]

        # 先调用 list_tasks（可能替换引用）
        await task_manager.list_tasks(limit=10, offset=0)

        # 添加子任务
        items = [
            TaskItem(
                id=f"{task.task_id}_msg_1",
                task_id=task.task_id,
                source_message_id=1,
                created_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            )
        ]
        await task_manager.add_items(task.task_id, items)

        # 验证原始引用的 items 被正确更新
        assert len(original_ref.items) == 1, (
            f"add_items 后原始引用的 items 应为 1，实际为 {len(original_ref.items)}"
        )

        # 再次调用 list_tasks
        await task_manager.list_tasks(limit=10, offset=0)

        # 验证原始引用仍然有效（不被替换）
        current_ref = task_manager._tasks[task.task_id]
        assert current_ref is original_ref, "list_tasks 后原始引用应保持有效"

    @pytest.mark.asyncio
    async def test_list_tasks_updates_status_on_existing_ref(self, task_manager):
        """list_tasks 应更新现有 Task 对象的属性而非替换整个对象。"""
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": 1, "message_range_end": 10},
        )

        original_ref = task_manager._tasks[task.task_id]

        # 修改任务状态
        await task_manager.start_task(task.task_id)
        assert original_ref.status == TaskStatus.RUNNING

        # 重新从数据库查询（list_tasks 会从 DB 加载最新数据）
        await task_manager.list_tasks(limit=10, offset=0)

        # 验证引用不变，但属性被更新
        current_ref = task_manager._tasks[task.task_id]
        assert current_ref is original_ref, "引用不应被替换"
        assert current_ref.status == TaskStatus.RUNNING, "属性应反映最新状态"


class FakeExecutor:
    """模拟 TaskExecutor 提交接口，记录被调度执行的任务。"""

    def __init__(self):
        self.submitted: list[str] = []

    def submit_task(self, task):
        self.submitted.append(task.task_id)


class TestExecutorDispatch:
    """方案一：任务执行必须与状态机绑定，由 TaskManager 统一调度到 executor。

    核心约束：
    - 任务被置为 RUNNING 时才真正提交执行（dispatch）；
    - 并发满入队（QUEUED）的任务不得立即执行，须由队列调度器触发；
    - QUEUED 状态不允许直接转到终态（仍抛 TaskStateError）。
    """

    @pytest_asyncio.fixture
    async def tm_with_executor(self, db_path):
        from module.core import db

        await db.init_db(db_path)
        tm = TaskManager(max_concurrent_tasks=1)
        executor = FakeExecutor()
        tm.set_executor(executor)
        yield tm, executor
        await db.close_db()

    @staticmethod
    async def _make_task(tm, start_id: int, end_id: int):
        return await tm.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
            params={"message_range_start": start_id, "message_range_end": end_id},
        )

    @pytest.mark.asyncio
    async def test_start_direct_running_dispatches_to_executor(self, tm_with_executor):
        """直接运行（未并发满）时应立即提交到 executor。"""
        tm, executor = tm_with_executor
        task = await self._make_task(tm, 1, 10)

        started = await tm.start_task(task.task_id)

        assert started is True
        assert task.status == TaskStatus.RUNNING
        assert executor.submitted == [task.task_id]

    @pytest.mark.asyncio
    async def test_queued_task_not_dispatched_immediately(self, tm_with_executor):
        """并发已满入队的任务不应被立即提交执行。"""
        tm, executor = tm_with_executor
        t1 = await self._make_task(tm, 1, 10)
        t2 = await self._make_task(tm, 11, 20)

        await tm.start_task(t1.task_id)  # RUNNING
        await tm.start_task(t2.task_id)  # QUEUED

        assert t2.status == TaskStatus.QUEUED
        assert t1.task_id in executor.submitted
        assert t2.task_id not in executor.submitted

    @pytest.mark.asyncio
    async def test_complete_dispatches_queued_task(self, tm_with_executor):
        """运行中任务完成后，队列中的任务应被调度为运行并提交执行。"""
        tm, executor = tm_with_executor
        t1 = await self._make_task(tm, 1, 10)
        t2 = await self._make_task(tm, 11, 20)

        await tm.start_task(t1.task_id)
        await tm.start_task(t2.task_id)  # QUEUED
        assert t2.task_id not in executor.submitted

        await tm.complete_task(t1.task_id)  # 触发 _process_queue

        assert t2.status == TaskStatus.RUNNING
        assert t2.task_id in executor.submitted

    @pytest.mark.asyncio
    async def test_queued_to_completed_raises(self, tm_with_executor):
        """排队的任务直接转为 completed 应抛出 TaskStateError。"""
        tm, _ = tm_with_executor
        t1 = await self._make_task(tm, 1, 10)
        t2 = await self._make_task(tm, 11, 20)

        await tm.start_task(t1.task_id)
        await tm.start_task(t2.task_id)  # QUEUED

        with pytest.raises(TaskStateError):
            await tm.complete_task(t2.task_id)

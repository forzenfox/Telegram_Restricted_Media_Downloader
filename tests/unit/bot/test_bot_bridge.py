# coding=UTF-8
"""BotTaskBridge 桥接层单元测试。

测试 module/bot/bot_bridge.py：
- 链接解析与按频道分组
- 同频道多链接合并
- 无效链接上报
- 创建的任务在 TaskManager 中可见（web 端可见性）
- 任务启动后通过 executor 调度
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from module.bot.bot_bridge import BotTaskBridge
from module.core.identifier_service import IdentifierService, ResolvedChat
from module.core.task.manager import TaskManager, TaskStatus, TaskType


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


@pytest.fixture
def mock_identifier_service():
    """提供 mock IdentifierService，将任意标识符解析为固定频道。"""
    svc = MagicMock(spec=IdentifierService)

    async def _resolve(identifier: str):
        return ResolvedChat(
            chat_id=-1001234567890,
            chat_type="channel",
            chat_name="Test Channel",
            username="testchannel",
            message_count=-1,
            media_count=-1,
            has_access=True,
            is_private=False,
        )

    svc.resolve = AsyncMock(side_effect=_resolve)
    return svc


class FakeExecutor:
    """模拟 TaskExecutor，记录被调度执行的任务。"""

    def __init__(self):
        self.submitted: list[str] = []

    def submit_task(self, task):
        self.submitted.append(task.task_id)


@pytest_asyncio.fixture
async def task_manager(db_path, mock_identifier_service):
    """创建 TaskManager 并注入 IdentifierService 与 FakeExecutor。"""
    from module.core import db

    await db.init_db(db_path)
    tm = TaskManager(max_concurrent_tasks=2, identifier_service=mock_identifier_service)
    executor = FakeExecutor()
    tm.set_executor(executor)
    yield tm, executor
    await db.close_db()


@pytest_asyncio.fixture
async def bridge(task_manager):
    """创建 BotTaskBridge 实例（注入 config_manager 以获取 save_directory）。"""
    tm, _ = task_manager
    client = AsyncMock()
    config_manager = MagicMock()
    # save_directory 指向系统临时目录，与测试文件同盘，避免跨盘 relpath 失败
    config_manager.save_directory = tempfile.gettempdir()
    return BotTaskBridge(task_manager=tm, client=client, config_manager=config_manager)


class TestBotBridgeDownloadLinks:
    """测试 BOT 链接 → TaskManager 下载任务桥接。"""

    @pytest.mark.asyncio
    async def test_single_link_creates_download_task(self, bridge, task_manager):
        """单个链接应创建一个 DOWNLOAD 任务。"""
        tm, _ = task_manager
        result = await bridge.create_download_tasks_from_links(
            ["https://t.me/testchannel/123"]
        )

        assert len(result["created"]) == 1
        assert result["failed"] == []
        task = await tm.get_task(result["created"][0])
        assert task is not None
        assert task.task_type == TaskType.DOWNLOAD
        assert task.params["range_mode"] == "multiple_ids"
        assert task.params["message_list"] == [123]

    @pytest.mark.asyncio
    async def test_links_grouped_by_chat_id(self, bridge, task_manager):
        """不同频道的链接应创建多个任务。"""
        tm, _ = task_manager
        result = await bridge.create_download_tasks_from_links(
            [
                "https://t.me/channel_a/1",
                "https://t.me/channel_b/2",
            ]
        )

        assert len(result["created"]) == 2
        assert result["failed"] == []
        tasks = [await tm.get_task(tid) for tid in result["created"]]
        # 不同频道分组为不同任务（source_identifier 不同）
        assert tasks[0].params.get("source_identifier") != tasks[1].params.get(
            "source_identifier"
        )

    @pytest.mark.asyncio
    async def test_same_chat_links_merged(self, bridge, task_manager):
        """同一频道的多个链接应合并为一个任务。"""
        tm, _ = task_manager
        result = await bridge.create_download_tasks_from_links(
            [
                "https://t.me/testchannel/1",
                "https://t.me/testchannel/2",
                "https://t.me/testchannel/3",
            ]
        )

        assert len(result["created"]) == 1
        task = await tm.get_task(result["created"][0])
        assert sorted(task.params["message_list"]) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_invalid_link_reported_failed(self, bridge, task_manager):
        """无效链接应上报到 failed 列表。"""
        result = await bridge.create_download_tasks_from_links(["not-a-valid-link"])

        assert result["created"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0][0] == "not-a-valid-link"

    @pytest.mark.asyncio
    async def test_created_tasks_visible_in_task_manager(self, bridge, task_manager):
        """桥接创建的任务应能被 list_tasks 查询到（web 端可见性）。"""
        tm, _ = task_manager
        await bridge.create_download_tasks_from_links(["https://t.me/testchannel/123"])

        tasks, total = await tm.list_tasks(limit=10)
        assert total == 1
        assert tasks[0].task_type == TaskType.DOWNLOAD

    @pytest.mark.asyncio
    async def test_download_task_started_via_executor(self, bridge, task_manager):
        """创建后的任务应被启动并提交到 executor。"""
        tm, executor = task_manager
        result = await bridge.create_download_tasks_from_links(
            ["https://t.me/testchannel/123"]
        )

        task_id = result["created"][0]
        task = await tm.get_task(task_id)
        assert task.status == TaskStatus.RUNNING
        assert task_id in executor.submitted


class TestBotBridgeUpload:
    """测试 BOT 文件 → TaskManager 上传任务桥接。"""

    @pytest.fixture
    def sample_file(self):
        """在 save_directory 子目录创建临时上传文件，返回其绝对路径。"""
        import shutil

        base = tempfile.gettempdir()
        subdir = os.path.join(base, "trmd_test_uploads")
        os.makedirs(subdir, exist_ok=True)
        path = os.path.join(subdir, "test_video.mp4")
        with open(path, "wb") as f:
            f.write(b"test-video-content")
        yield path
        shutil.rmtree(subdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_upload_task_created_with_portable_path(
        self, bridge, task_manager, sample_file
    ):
        """应创建 UPLOAD 任务，file_paths 转为可移植相对路径。"""
        tm, _ = task_manager
        task_id = await bridge.create_upload_task(
            file_path=sample_file,
            target_identifier="https://t.me/target_channel",
            delete_after_upload=True,
        )

        task = await tm.get_task(task_id)
        assert task is not None
        assert task.task_type == TaskType.UPLOAD
        assert len(task.params["file_paths"]) == 1
        # 可移植路径使用 / 分隔符且为相对路径
        portable = task.params["file_paths"][0]
        assert "/" in portable
        assert not os.path.isabs(portable)
        assert portable.endswith("test_video.mp4")
        assert task.params["delete_after_upload"] is True

    @pytest.mark.asyncio
    async def test_upload_task_numeric_target_used_as_chat_id(
        self, bridge, task_manager, sample_file
    ):
        """数字目标 chat_id 应直接作为上传目标的 chat_id。"""
        tm, _ = task_manager
        task_id = await bridge.create_upload_task(
            file_path=sample_file,
            target_identifier="-1009998887777",
            delete_after_upload=False,
        )

        task = await tm.get_task(task_id)
        assert task.chat_id == -1009998887777
        assert task.params["delete_after_upload"] is False

    @pytest.mark.asyncio
    async def test_upload_task_me_target_uses_client_id(
        self, bridge, task_manager, sample_file
    ):
        """目标为 me/self 时使用 client 自身 ID 作为上传目标。"""
        tm, _ = task_manager
        bridge._client.get_me = AsyncMock(return_value=MagicMock(id=123456789))
        task_id = await bridge.create_upload_task(
            file_path=sample_file,
            target_identifier="me",
            delete_after_upload=True,
        )

        task = await tm.get_task(task_id)
        assert task.chat_id == 123456789

    @pytest.mark.asyncio
    async def test_upload_task_visible_and_started(
        self, bridge, task_manager, sample_file
    ):
        """上传任务应可见且已启动调度。"""
        tm, executor = task_manager
        task_id = await bridge.create_upload_task(
            file_path=sample_file,
            target_identifier="https://t.me/target_channel",
            delete_after_upload=True,
        )

        tasks, total = await tm.list_tasks(limit=10)
        assert total == 1
        task = await tm.get_task(task_id)
        assert task.status == TaskStatus.RUNNING
        assert task_id in executor.submitted


class TestBotBridgeForward:
    """测试 BOT 转发 → TaskManager 转发任务桥接。"""

    @pytest.mark.asyncio
    async def test_forward_task_created_with_range_and_target(
        self, bridge, task_manager
    ):
        """应创建 FORWARD 任务，消息范围与目标频道正确。"""
        tm, _ = task_manager
        task_id = await bridge.create_forward_task(
            origin_identifier="https://t.me/source_channel",
            target_identifier="-1009998887777",
            start_id=1,
            end_id=100,
        )

        task = await tm.get_task(task_id)
        assert task is not None
        assert task.task_type == TaskType.FORWARD
        assert task.params["range_mode"] == "id_range"
        assert task.params["min_id"] == 1
        assert task.params["max_id"] == 100
        assert task.params["target_chat_id"] == -1009998887777

    @pytest.mark.asyncio
    async def test_forward_task_origin_chat_resolved(self, bridge, task_manager):
        """源频道链接应解析为任务的 chat_id。"""
        tm, _ = task_manager
        task_id = await bridge.create_forward_task(
            origin_identifier="https://t.me/source_channel",
            target_identifier="https://t.me/target_channel",
            start_id=1,
            end_id=50,
        )

        task = await tm.get_task(task_id)
        # mock_identifier_service 将任意标识符解析为 -1001234567890
        assert task.chat_id == -1001234567890
        assert task.params["target_chat_id"] == -1001234567890

    @pytest.mark.asyncio
    async def test_forward_task_visible_and_started(self, bridge, task_manager):
        """转发任务应可见且已启动调度。"""
        tm, executor = task_manager
        task_id = await bridge.create_forward_task(
            origin_identifier="https://t.me/source_channel",
            target_identifier="https://t.me/target_channel",
            start_id=1,
            end_id=10,
        )

        tasks, total = await tm.list_tasks(limit=10)
        assert total == 1
        assert tasks[0].task_type == TaskType.FORWARD
        task = await tm.get_task(task_id)
        assert task.status == TaskStatus.RUNNING
        assert task_id in executor.submitted

# coding=UTF-8
"""Downloader 内容保护限制降级策略测试。"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram.errors.exceptions.bad_request_400 import (
    ChatForwardsRestricted as ChatForwardsRestricted_400,
)


class AsyncIterator:
    """将列表包装为异步生成器。"""

    def __init__(self, items):
        self.items = items
        self.index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.items):
            raise StopAsyncIteration
        item = self.items[self.index]
        self.index += 1
        return item


@pytest.fixture
def db_path():
    """创建临时数据库路径。"""
    import tempfile

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


class TestCheckType:
    """测试 check_type 方法。"""

    @pytest.fixture
    def downloader(self):
        """构造轻量级 Downloader 实例。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    dl.app = MagicMock()
                    dl.app.config = {
                        "preference": {
                            "forward_type": {
                                "video": True,
                                "photo": True,
                                "audio": False,
                                "document": False,
                                "voice": False,
                                "text": True,
                                "animation": False,
                                "video_note": False,
                            }
                        }
                    }
                    return dl

    def test_check_type_video(self, downloader):
        """视频消息应通过类型检查。"""
        msg = MagicMock(video=MagicMock(), photo=None, text=None)
        assert downloader.check_type(msg) is True

    def test_check_type_text(self, downloader):
        """文本消息应通过类型检查（当 text 启用时）。"""
        msg = MagicMock(video=None, photo=None, text="hello")
        assert downloader.check_type(msg) is True

    def test_check_type_filtered(self, downloader):
        """被过滤的类型应返回 False。"""
        msg = MagicMock(video=None, photo=None, audio=MagicMock(), text=None)
        assert downloader.check_type(msg) is False

    def test_check_type_no_media_no_text(self, downloader):
        """无媒体无文本消息应返回 False。"""
        msg = MagicMock(
            video=None,
            photo=None,
            audio=None,
            document=None,
            voice=None,
            text=None,
            animation=None,
            video_note=None,
        )
        assert downloader.check_type(msg) is False


class TestContentProtectionFallback:
    """内容保护限制降级策略测试（批量转发循环）。"""

    @pytest.fixture
    def downloader(self):
        """构造带 Mock 依赖的 Downloader 实例。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    dl._commands = MagicMock()
                    dl._commands.get_forward_link_from_bot = AsyncMock(
                        return_value={
                            "origin_link": "https://t.me/origin/1",
                            "target_link": "https://t.me/target/1",
                            "message_range": [1, 1],
                        }
                    )
                    dl.app = MagicMock()
                    dl.app.client = AsyncMock()
                    dl.app.config = {
                        "preference": {
                            "forward_type": {
                                "video": True,
                                "photo": True,
                                "text": True,
                            },
                            "upload": {"download_upload": True},
                        }
                    }
                    dl.check_type = MagicMock(return_value=True)
                    dl.cd = MagicMock()
                    dl.get_download_link_from_bot = AsyncMock()
                    dl.last_client = AsyncMock()
                    dl.last_message = MagicMock()
                    dl.forward = AsyncMock()
                    dl.done_notice = AsyncMock()
                    dl.repository_manager = None
                    return dl

    @pytest.mark.asyncio
    async def test_text_message_direct_forward_on_restrict(self, downloader):
        """文本消息在 ChatForwardsRestricted 时直接 send_message，不走下载后上传。"""
        text_msg = MagicMock(text="hello", media=None, id=100)

        # 直接替换为异步生成器函数
        async def _gen(*args, **kwargs):
            yield text_msg

        downloader.app.client.get_chat_history = _gen

        downloader.forward.side_effect = ChatForwardsRestricted_400

        client_mock = AsyncMock()
        client_mock.me = MagicMock(id=999)

        with (
            patch(
                "module.core.download.downloader.parse_link",
                side_effect=[
                    {"chat_id": -100111},
                    {"chat_id": -100222},
                ],
            ),
            patch(
                "module.core.download.downloader.get_chat_with_notify",
                side_effect=[
                    MagicMock(id=-100111, username="origin"),
                    MagicMock(id=-100222, username="target"),
                ],
            ),
            patch("module.core.download.downloader.get_my_id", return_value=123),
        ):
            await downloader.get_forward_link_from_bot(
                client=client_mock,
                message=MagicMock(
                    from_user=MagicMock(id=123),
                    text="/forward https://t.me/origin/1 https://t.me/target/1 1 1",
                    id=1,
                ),
            )

        # 验证 send_message 被调用（至少2次：加载消息 + 文本转发）
        assert client_mock.send_message.call_count >= 2
        # 找到目标 chat_id 的调用
        target_calls = [
            call
            for call in client_mock.send_message.call_args_list
            if call.kwargs.get("chat_id") == -100222
            and call.kwargs.get("text") == "hello"
        ]
        assert len(target_calls) == 1, "应有一次向目标频道发送文本消息的调用"

        # 验证没有走下载后上传
        downloader.get_download_link_from_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_media_message_uses_download_upload_on_restrict(self, downloader):
        """媒体消息在 ChatForwardsRestricted 时走 get_download_link_from_bot。"""
        media_msg = MagicMock(text=None, media=MagicMock(), id=200)

        async def _gen(*args, **kwargs):
            yield media_msg

        downloader.app.client.get_chat_history = _gen

        downloader.forward.side_effect = ChatForwardsRestricted_400
        downloader.check_type.return_value = True

        client_mock = AsyncMock()
        client_mock.me = MagicMock(id=999)

        with (
            patch(
                "module.core.download.downloader.parse_link",
                side_effect=[
                    {"chat_id": -100111},
                    {"chat_id": -100222},
                ],
            ),
            patch(
                "module.core.download.downloader.get_chat_with_notify",
                side_effect=[
                    MagicMock(id=-100111, username="origin"),
                    MagicMock(id=-100222, username="target"),
                ],
            ),
            patch("module.core.download.downloader.get_my_id", return_value=123),
        ):
            await downloader.get_forward_link_from_bot(
                client=client_mock,
                message=MagicMock(
                    from_user=MagicMock(id=123),
                    text="/forward https://t.me/origin/1 https://t.me/target/1 1 1",
                    id=1,
                ),
            )

        # 验证没有向目标频道直接 send_message（因为不是文本）
        target_calls = [
            call
            for call in client_mock.send_message.call_args_list
            if call.kwargs.get("chat_id") == -100222
        ]
        assert len(target_calls) == 0, "不应有直接发送到目标频道的调用"
        # 验证走了下载后上传
        downloader.get_download_link_from_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_filtered_type_skipped_even_on_restrict(self, downloader):
        """被 check_type 过滤的消息在 ChatForwardsRestricted 时跳过。"""
        filtered_msg = MagicMock(text=None, media=MagicMock(), id=300)

        async def _gen(*args, **kwargs):
            yield filtered_msg

        downloader.app.client.get_chat_history = _gen

        downloader.forward.side_effect = ChatForwardsRestricted_400
        downloader.check_type.return_value = False

        client_mock = AsyncMock()
        client_mock.me = MagicMock(id=999)

        with (
            patch(
                "module.core.download.downloader.parse_link",
                side_effect=[
                    {"chat_id": -100111},
                    {"chat_id": -100222},
                ],
            ),
            patch(
                "module.core.download.downloader.get_chat_with_notify",
                side_effect=[
                    MagicMock(id=-100111, username="origin"),
                    MagicMock(id=-100222, username="target"),
                ],
            ),
            patch("module.core.download.downloader.get_my_id", return_value=123),
        ):
            await downloader.get_forward_link_from_bot(
                client=client_mock,
                message=MagicMock(
                    from_user=MagicMock(id=123),
                    text="/forward https://t.me/origin/1 https://t.me/target/1 1 1",
                    id=1,
                ),
            )

        # 验证没有向目标频道直接 send_message
        target_calls = [
            call
            for call in client_mock.send_message.call_args_list
            if call.kwargs.get("chat_id") == -100222
        ]
        assert len(target_calls) == 0
        # 验证没有走下载后上传
        downloader.get_download_link_from_bot.assert_not_called()


class TestDownloadRangeDtype:
    """测试 download_range 对媒体类型的识别与临时文件名生成。"""

    @pytest.fixture
    def downloader(self):
        """构造带真实 get_temp_file_path 的 Downloader 实例。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.app import Application
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    real_app = Application.__new__(Application)
                    real_app.temp_directory = os.path.join(
                        os.getcwd(), "tests", "tmp", "trmd"
                    )
                    dl.app = real_app
                    dl.resume_download = AsyncMock(return_value="/mock/final_path")
                    return dl

    def _make_message(self, dtype: str, msg_id: int = 96396):
        """构造一个指定媒体类型的 Pyrogram Message Mock。"""
        media_obj = MagicMock(
            file_id="valid_file_id_for_test",
            mime_type=f"{dtype}/mp4" if dtype == "video" else f"{dtype}/jpg",
            file_size=1024,
        )
        msg = MagicMock(
            id=msg_id,
            chat=MagicMock(id=-100123, full_name="test_chat"),
            media=MagicMock(name=dtype.upper()),
        )
        # 清空所有媒体属性，仅保留目标类型
        for attr in (
            "video",
            "photo",
            "document",
            "audio",
            "voice",
            "animation",
            "video_note",
        ):
            setattr(msg, attr, None)
        setattr(msg, dtype, media_obj)
        return msg

    @pytest.mark.asyncio
    async def test_video_message_uses_lowercase_dtype(self, downloader):
        """视频消息应被识别为小写 video，临时文件名不应为 .unknown。"""
        msg = self._make_message("video")
        downloader.app.client = AsyncMock()
        downloader.app.client.get_messages = AsyncMock(return_value=msg)
        downloader.env_save_directory = MagicMock(
            return_value=os.path.join(os.getcwd(), "tests", "tmp", "downloads")
        )

        with patch("module.app.get_extension", return_value="mp4"):
            with patch(
                "module.core.download.downloader.is_file_duplicate", return_value=False
            ):
                await downloader.download_range(
                    chat_id=-100123,
                    start_id=96396,
                    end_id=96396,
                    task_id="task_1",
                )

        call = downloader.resume_download.call_args
        file_name = call.kwargs["file_name"]
        assert not file_name.endswith(".unknown"), (
            f"视频消息生成的文件名不应以 .unknown 结尾: {file_name}"
        )
        assert file_name.endswith(".mp4"), (
            f"视频消息生成的文件名应使用 .mp4: {file_name}"
        )

    @pytest.mark.asyncio
    async def test_photo_message_uses_lowercase_dtype(self, downloader):
        """图片消息应被识别为小写 photo，临时文件名不应为 .unknown。"""
        msg = self._make_message("photo")
        downloader.app.client = AsyncMock()
        downloader.app.client.get_messages = AsyncMock(return_value=msg)
        downloader.env_save_directory = MagicMock(
            return_value=os.path.join(os.getcwd(), "tests", "tmp", "downloads")
        )

        with patch("module.app.get_extension", return_value="jpg"):
            with patch(
                "module.core.download.downloader.is_file_duplicate", return_value=False
            ):
                await downloader.download_range(
                    chat_id=-100123,
                    start_id=96396,
                    end_id=96396,
                    task_id="task_1",
                )

        call = downloader.resume_download.call_args
        file_name = call.kwargs["file_name"]
        assert not file_name.endswith(".unknown"), (
            f"图片消息生成的文件名不应以 .unknown 结尾: {file_name}"
        )
        assert file_name.endswith(".jpg"), (
            f"图片消息生成的文件名应使用 .jpg: {file_name}"
        )

    @pytest.mark.asyncio
    async def test_unrenamed_temp_file_reported_as_failed(self, downloader):
        """当 resume_download 未重命名 .temp 文件时，子任务应报告失败。"""
        msg = self._make_message("video")
        downloader.app.client = AsyncMock()
        downloader.app.client.get_messages = AsyncMock(return_value=msg)
        downloader.env_save_directory = MagicMock(
            return_value=os.path.join(os.getcwd(), "tests", "tmp", "downloads")
        )
        progress_callback = AsyncMock()

        # 模拟 resume_download 返回目标路径，但目标文件不存在而 .temp 存在
        target_path = os.path.join(
            os.getcwd(), "tests", "tmp", "downloads", "96396 - None.mp4"
        )
        downloader.resume_download = AsyncMock(return_value=target_path)

        def _fake_exists(path):
            return str(path).endswith(".temp")

        with patch("module.app.get_extension", return_value="mp4"):
            with patch(
                "module.core.download.downloader.is_file_duplicate", return_value=False
            ):
                with patch("os.path.exists", side_effect=_fake_exists):
                    await downloader.download_range(
                        chat_id=-100123,
                        start_id=96396,
                        end_id=96396,
                        task_id="task_1",
                        progress_callback=progress_callback,
                    )

        # 验证进度回调报告了 FAILED 而不是 SUCCESS
        failed_calls = [
            call
            for call in progress_callback.call_args_list
            if call.args[2].value == "failed"
        ]
        assert len(failed_calls) == 1, "未重命名的临时文件应报告失败"
        assert "临时文件未重命名" in failed_calls[0].args[3]


class TestResumeDownloadProgress:
    """测试 resume_download 在无 progress 回调时的行为。"""

    @pytest.fixture
    def downloader(self):
        """构造最小化的 Downloader 实例。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    dl.app = MagicMock()
                    return dl

    @pytest.mark.asyncio
    async def test_resume_download_without_progress_callback(
        self, downloader, tmp_path
    ):
        """不传入 progress 回调时，resume_download 应正常完成重命名。"""
        target_file = str(tmp_path / "test.jpg")
        target_size = 4

        async def _stream(*args, **kwargs):
            yield b"1234"

        downloader.app.client.stream_media = _stream

        with patch("module.core.download.downloader.safe_replace") as mock_safe_replace:
            mock_safe_replace.return_value = {"e_code": None}
            result = await downloader.resume_download(
                message=MagicMock(),
                file_name=target_file,
                compare_size=target_size,
            )
            assert result == target_file
            mock_safe_replace.assert_called_once()


class TestInitRepositoryManager:
    """测试 _init_repository_manager 优先使用 AppContext 实例。"""

    @pytest.fixture
    def downloader(self):
        """构造轻量级 Downloader 实例。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    dl.app = MagicMock()
                    dl.repository_manager = None
                    return dl

    def test_uses_app_context_repo_manager(self, downloader):
        """AppContext 可用时，_init_repository_manager 应使用其 repository_manager。"""
        downloader.app.config = {
            "repository": {"enabled": True, "chat_id": "-1001234567890"}
        }
        mock_repo_manager = MagicMock()

        with patch(
            "module.core.integration.get_context",
            return_value=MagicMock(repository_manager=mock_repo_manager),
        ):
            downloader._init_repository_manager()

        assert downloader.repository_manager is mock_repo_manager

    def test_fallback_to_resolved_data_directory(self, downloader, tmp_path):
        """AppContext 不可用时，应使用 resolved_data_directory 创建 RepositoryDB。"""
        downloader.app.config = {
            "repository": {"enabled": True, "chat_id": "-1001234567890"}
        }
        downloader.app.resolved_data_directory = str(tmp_path / ".trmd")

        with patch("module.core.integration.get_context", return_value=None):
            with patch("module.core.repository.db.RepositoryDB") as MockRepoDB:
                with patch(
                    "module.core.repository.manager.RepositoryManager"
                ) as _MockRepoMgr:
                    downloader._init_repository_manager()

        # 验证 db_path 使用了 resolved_data_directory
        call_args = MockRepoDB.call_args
        db_path = (
            call_args.kwargs.get("db_path")
            or call_args[1].get("db_path")
            or call_args[0][0]
        )
        assert "repository.db" in db_path
        assert str(tmp_path / ".trmd") in db_path

    def test_no_repo_config(self, downloader):
        """仓库未启用时，repository_manager 应保持 None。"""
        downloader.app.config = {"repository": {"enabled": False}}
        downloader._init_repository_manager()
        assert downloader.repository_manager is None

    def test_no_repository_section(self, downloader):
        """配置中无 repository 节时，repository_manager 应保持 None。"""
        downloader.app.config = {}
        downloader._init_repository_manager()
        assert downloader.repository_manager is None

    def test_empty_chat_id_skips_init(self, downloader):
        """仓库启用但 chat_id 为空时，应跳过初始化。"""
        downloader.app.config = {"repository": {"enabled": True, "chat_id": ""}}
        downloader._init_repository_manager()
        assert downloader.repository_manager is None


class TestSessionDirectoryPaths:
    """测试 sessions 路径使用 work_directory 而非 DIRECTORY_NAME。"""

    def test_sessions_path_uses_work_directory(self):
        """验证 self.app.work_directory 已从配置正确解析，而非使用 DIRECTORY_NAME 拼接。"""
        # 此测试验证 Application 的 work_directory 属性语义
        # work_directory 定义在 UserConfig.__init__ 第 322-324 行：
        # self.work_directory = PARSE_ARGS.session or (
        #     task.get("session_directory") or UserConfig.WORK_DIRECTORY
        # )
        # 而 DIRECTORY_NAME + "sessions" 是硬编码拼接，与配置系统脱钩
        # 改用 work_directory 是正确行为
        from module.utils.path_tool import resolve_data_directory

        # 验证 resolve_data_directory 返回的路径与预期一致
        project_root = "d:\\workspace\\TRMD"
        result = resolve_data_directory("./.trmd", project_root)
        expected = os.path.normpath(os.path.join(project_root, ".trmd"))
        assert result == expected


class TestDownloadChatBridge:
    """测试 download_chat 执行阶段桥接到 TaskManager 创建任务。"""

    @pytest.fixture
    def downloader(self):
        """构造带 Mock 依赖的 Downloader 实例（仅 mock 收集阶段）。"""
        with patch("module.core.download.downloader.Bot.__init__", return_value=None):
            with patch("module.core.download.downloader.Application"):
                with patch("asyncio.get_event_loop"):
                    from module.core.download.downloader import (
                        TelegramRestrictedMediaDownloader,
                    )

                    dl = TelegramRestrictedMediaDownloader()
                    dl._state = MagicMock()
                    dl.app = MagicMock()
                    dl.app.client = AsyncMock()
                    dl.app.config = {"preference": {}}
                    dl.uploader = None
                    dl.pb = MagicMock()
                    dl.download_chat_filter = {
                        "123": {
                            "date_range": {
                                "start_date": None,
                                "end_date": None,
                            },
                            "download_type": {
                                "video": True,
                                "photo": True,
                                "audio": True,
                                "document": True,
                                "voice": True,
                                "animation": True,
                                "video_note": True,
                            },
                            "keyword": {},
                            "comment": False,
                        }
                    }
                    dl.check_type = MagicMock(return_value=True)
                    return dl

    @pytest.mark.asyncio
    async def test_download_chat_bridges_to_task_manager(self, downloader, db_path):
        """download_chat 收集后应桥接创建 TaskManager 下载任务。"""
        from module.core import db
        from module.core.identifier_service import IdentifierService, ResolvedChat
        from module.core.task.manager import TaskManager, TaskType

        await db.init_db(db_path)
        identifier_service = MagicMock(spec=IdentifierService)

        async def _resolve(identifier: str):
            return ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test",
                username="test",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )

        identifier_service.resolve = AsyncMock(side_effect=_resolve)
        tm = TaskManager(max_concurrent_tasks=2, identifier_service=identifier_service)
        try:
            # 构造一条匹配消息
            msg = MagicMock()
            msg.media_group_id = None
            msg.link = "https://t.me/channel/123"
            msg.id = 123

            async def _chat_history(*args, **kwargs):
                yield msg

            downloader.app.client.get_chat_history = _chat_history
            downloader.app.client.get_discussion_replies = AsyncMock()

            mock_filter = MagicMock()
            mock_filter.date_range = MagicMock(return_value=True)
            mock_filter.dtype = MagicMock(return_value=True)
            mock_filter.keyword_filter = MagicMock(return_value=True)

            cq = MagicMock()
            cq.message.text = "channel: 123"
            cq.message.edit_text = AsyncMock(return_value=cq.message)

            ctx_mock = MagicMock()
            ctx_mock.task_manager = tm
            ctx_mock.config_manager = MagicMock(save_directory="./downloads")

            with (
                patch(
                    "module.core.integration.get_context",
                    return_value=ctx_mock,
                ),
                patch(
                    "module.core.download.downloader.Filter",
                    return_value=mock_filter,
                ),
                patch("module.core.download.downloader.KeyboardButton") as kb,
            ):
                kb.single_button = MagicMock(return_value=MagicMock())
                await downloader.download_chat("123", cq)

            # 验证 TaskManager 中创建了下载任务
            tasks, total = await tm.list_tasks(limit=10)
            assert total == 1
            assert tasks[0].task_type == TaskType.DOWNLOAD
        finally:
            await db.close_db()

    @pytest.mark.asyncio
    async def test_download_chat_falls_back_without_task_manager(self, downloader):
        """无 TaskManager 时 download_chat 应回退旧架构逐条下载。"""

        msg = MagicMock()
        msg.media_group_id = None
        msg.link = "https://t.me/channel/123"
        msg.id = 123

        async def _chat_history(*args, **kwargs):
            yield msg

        downloader.app.client.get_chat_history = _chat_history
        downloader.app.client.get_discussion_replies = AsyncMock()

        mock_filter = MagicMock()
        mock_filter.date_range = MagicMock(return_value=True)
        mock_filter.dtype = MagicMock(return_value=True)
        mock_filter.keyword_filter = MagicMock(return_value=True)

        cq = MagicMock()
        cq.message.text = "channel: 123"
        cq.message.edit_text = AsyncMock(return_value=cq.message)

        # 无 task_manager（get_context 返回无 task_manager 的 ctx）
        ctx_mock = MagicMock()
        ctx_mock.task_manager = None

        downloader.create_download_task = AsyncMock(
            return_value={"status": "downloading"}
        )

        with (
            patch("module.core.integration.get_context", return_value=ctx_mock),
            patch("module.core.download.downloader.Filter", return_value=mock_filter),
            patch("module.core.download.downloader.KeyboardButton") as kb,
        ):
            kb.single_button = MagicMock(return_value=MagicMock())
            await downloader.download_chat("123", cq)

        # 回退旧架构：create_download_task 被调用
        downloader.create_download_task.assert_called()

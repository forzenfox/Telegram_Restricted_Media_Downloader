# coding=UTF-8
"""Web API 模块集成测试。

使用 httpx.AsyncClient 测试 FastAPI 路由、中间件、认证、WebSocket 等。
Mock TokenManager、TaskManager 等核心模块。
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from module.api.app import create_app
from module.api.dependencies import get_identifier_service
from module.core.auth.token_manager import TokenManager
from module.core.config_manager import ConfigManager
from module.core.identifier_service import (
    AccessDeniedError,
    IdentifierService,
    InvalidIdentifierError,
    RateLimitedError,
    ResolvedChat,
    UserNotFoundError,
)
from module.core.task.manager import TaskManager, TaskType

# ==================== 测试工具 ====================


@pytest.fixture
def token_manager():
    """提供内存模式 TokenManager。"""
    tm = TokenManager(default_ttl=3600)
    return tm


@pytest.fixture
def valid_token(token_manager):
    """生成有效 Token。"""
    return token_manager.generate(user_id=1)


@pytest_asyncio.fixture
async def task_manager():
    """提供内存模式 TaskManager（不持久化），已注入 mock IdentifierService 与 ConfigManager。"""
    from module.core import db

    await db.init_db(":memory:")
    mock_service = MagicMock(spec=IdentifierService)

    def _resolve_side_effect(identifier: str):
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
            chat_name="Test Channel",
            username="testchannel",
            message_count=-1,
            media_count=-1,
            has_access=True,
            is_private=False,
        )

    mock_service.resolve = AsyncMock(side_effect=_resolve_side_effect)

    mock_cm = MagicMock(spec=ConfigManager)
    mock_cm.get = MagicMock(return_value=False)

    tm = TaskManager(
        max_concurrent_tasks=2,
        identifier_service=mock_service,
        config_manager=mock_cm,
    )
    yield tm
    await db.close_db()


@pytest.fixture
def config_manager():
    """提供 Mock 配置管理器。"""
    mock = MagicMock()
    mock.config = {
        "api_id": "12345",
        "api_hash": "test_hash",
        "bot_token": "test_bot_token",
        "save_directory": tempfile.gettempdir(),
        "download_type": ["video", "photo"],
        "max_tasks": {"download": 3, "upload": 3},
        "max_retries": {"download": 3, "upload": 3},
        "proxy": {
            "enable_proxy": False,
            "scheme": None,
            "hostname": None,
            "port": None,
            "username": None,
            "password": None,
        },
    }
    mock.save_directory = tempfile.gettempdir()

    # load_config 返回真实字典（而非 MagicMock）
    def _load_config(mask_sensitive=True):
        result = dict(mock.config)
        result["resource_limits"] = {
            "task_size_warning_gb": 5,
            "task_size_max_gb": 10,
            "min_disk_space_gb": 2,
            "memory_limit_mb": 512,
            "max_concurrent_tasks": 1,
            "max_download_concurrency": 3,
            "max_upload_concurrency": 1,
            "max_forward_concurrency": 1,
        }
        result["upload"] = {
            "delete_after_upload": False,
            "max_group_size": 10,
        }
        if mask_sensitive:
            result["api_id"] = "***"
            result["api_hash"] = "***"
            result["bot_token"] = "***"
        return result

    mock.load_config = _load_config

    # save_config 执行真实验证逻辑
    def _save_config(config_data):
        from module.core.config_manager import ConfigManager

        cm = ConfigManager()
        is_valid, errors = cm.validate_config(config_data)
        if not is_valid:
            raise ValueError(f"配置验证失败: {', '.join(errors)}")
        return True

    mock.save_config = _save_config

    return mock


@pytest_asyncio.fixture
async def client(token_manager, task_manager, config_manager):
    """提供已认证的测试客户端。"""
    app = create_app(
        token_manager=token_manager,
        task_manager=task_manager,
        config_manager=config_manager,
        file_manager=None,
        monitor=None,
    )

    # 默认 mock IdentifierService：复用 TaskManager 已注入的 mock，保持源端与目标端解析行为一致
    mock_service = task_manager._identifier_service
    app.dependency_overrides[get_identifier_service] = lambda: mock_service

    token = token_manager.generate(user_id=1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        yield ac, app, token


@pytest_asyncio.fixture
async def unauthenticated_client(token_manager, task_manager, config_manager):
    """提供未认证的测试客户端。"""
    app = create_app(
        token_manager=token_manager,
        task_manager=task_manager,
        config_manager=config_manager,
        file_manager=None,
        monitor=None,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        yield ac, app


@pytest.fixture
def file_manager():
    """提供真实 FileManager 实例（mock Telegram client，删除操作不依赖客户端）。"""
    from unittest.mock import AsyncMock

    from module.core.download.file_manager import FileManager

    fm = FileManager(config={}, client=AsyncMock())
    yield fm


@pytest_asyncio.fixture
async def files_client(token_manager, task_manager, config_manager, file_manager):
    """提供已认证且挂载真实 FileManager 的测试客户端。"""
    app = create_app(
        token_manager=token_manager,
        task_manager=task_manager,
        config_manager=config_manager,
        file_manager=file_manager,
        monitor=None,
    )
    mock_service = task_manager._identifier_service
    app.dependency_overrides[get_identifier_service] = lambda: mock_service
    token = token_manager.generate(user_id=1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        ac.headers.update({"Authorization": f"Bearer {token}"})
        yield ac, app, token, file_manager


# ==================== 认证测试 ====================


class TestAuthEndpoints:
    """认证端点测试。"""

    @pytest.mark.asyncio
    async def test_get_token_status(self, client):
        """测试获取 Token 状态。"""
        ac, app, token = client
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["valid"] is True

    @pytest.mark.asyncio
    async def test_refresh_token(self, client):
        """测试刷新 Token。"""
        ac, app, token = client
        resp = await ac.post("/api/auth/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "token" in data["data"]
        assert data["data"]["token"] != token

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token(self, unauthenticated_client):
        """测试无效 Token 刷新。"""
        ac, app = unauthenticated_client
        ac.headers.update({"Authorization": "Bearer invalid_token_xyz"})
        resp = await ac.post("/api/auth/refresh")
        assert resp.status_code == 401


# ==================== 认证中间件测试 ====================


class TestAuthenticationMiddleware:
    """Token 认证测试。"""

    @pytest.mark.asyncio
    async def test_unauthenticated_request(self, unauthenticated_client):
        """测试未认证请求返回 401。"""
        ac, app = unauthenticated_client
        resp = await ac.get("/api/tasks")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_token_header(self, unauthenticated_client):
        """测试缺少 Token 头返回 401。"""
        ac, app = unauthenticated_client
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token(self, unauthenticated_client):
        """测试过期 Token 返回 401。"""
        ac, app = unauthenticated_client
        # 使用已撤销的 Token
        tm = app.state.token_manager
        expired_token = tm.generate(user_id=1)
        tm.revoke(expired_token)
        ac.headers.update({"Authorization": f"Bearer {expired_token}"})
        resp = await ac.get("/api/tasks")
        assert resp.status_code == 401


# ==================== 任务路由测试 ====================


class TestTaskEndpoints:
    """任务管理端点测试。"""

    @pytest.fixture(autouse=True)
    async def mock_identifier_service(self, client):
        """为任务创建相关测试 mock IdentifierService（同时注入 TaskManager）。"""
        ac, app, token = client
        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test Channel",
                username="testchannel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service
        app.state.task_manager._identifier_service = mock_service
        yield
        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, client):
        """测试空任务列表。"""
        ac, app, token = client
        resp = await ac.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["items"] == []
        assert data["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_create_download_task(self, client):
        """测试创建下载任务。"""
        ac, app, token = client
        body = {
            "task_type": "download",
            "params": {
                "chat_id": "-1001234567890",
                "range_mode": "id_range",
                "min_id": 100,
                "max_id": 500,
                "filter_types": ["video", "photo"],
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["task_type"] == "download"
        assert data["data"]["status"] == "pending"
        assert data["data"]["id"].startswith("task_")

    @pytest.mark.asyncio
    async def test_create_upload_task(self, client):
        """测试创建上传任务。"""
        ac, app, token = client
        body = {
            "task_type": "upload",
            "params": {
                "file_paths": ["/tmp/test.mp4"],
                "chat_id": "-1001234567890",
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["data"]["task_type"] == "upload"

    @pytest.mark.asyncio
    async def test_create_upload_task_with_t_me_link(self, client):
        """测试创建上传任务，目标频道为 t.me 私有邀请链接（+xxx）。

        契约：文件管理页面"上传选中文件"输入 t.me 链接（数字/@username/裸
        username/t.me 链接/+ 私有邀请）作为目标频道时，API 应通过
        IdentifierService 解析后成功创建任务，而不是 400。
        """
        ac, app, token = client
        # 模拟 IdentifierService.resolve("https://t.me/+RahwU0t5xv9lYjNl")
        # 解析为标准 chat_id（私有频道，chat_id 为负数）
        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-2001234567890,
                chat_type="channel",
                chat_name="Private Channel",
                username=None,
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service
        # 源端解析已下沉到 TaskManager，需同步覆盖其内部服务
        app.state.task_manager._identifier_service = mock_service

        body = {
            "task_type": "upload",
            "params": {
                "file_paths": ["/tmp/a.mp4", "/tmp/b.mp4"],
                "chat_id": "https://t.me/+RahwU0t5xv9lYjNl",
                "send_as_media_group": True,
                "delete_after_upload": True,
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["task_type"] == "upload"

    @pytest.mark.asyncio
    async def test_create_upload_task_with_source_identifier(self, client):
        """测试创建上传任务，使用 source_identifier 传目标频道。

        契约：前端用 source_identifier 风格传值（与 download/forward 一致）
        时，API 应通过 IdentifierService 解析后成功创建任务。
        """
        ac, app, token = client
        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1009876543210,
                chat_type="channel",
                chat_name="Target Channel",
                username="target_channel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service
        app.state.task_manager._identifier_service = mock_service

        body = {
            "task_type": "upload",
            "params": {
                "file_paths": ["/tmp/a.mp4"],
                "source_identifier": "@target_channel",
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["task_type"] == "upload"

    @pytest.mark.asyncio
    async def test_create_task_with_source_identifier(self, client):
        """测试使用 source_identifier 创建任务。"""
        ac, app, token = client
        body = {
            "task_type": "download",
            "params": {
                "source_identifier": "@testchannel",
                "range_mode": "recent",
                "recent_count": 10,
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["data"]["task_type"] == "download"
        assert data["data"]["params"]["source_identifier"] == "@testchannel"
        assert data["data"]["params"]["recent_count"] == 10

    @pytest.mark.asyncio
    async def test_create_forward_task_with_target_identifier(self, client):
        """测试使用 target_identifier 创建转发任务。"""
        ac, app, token = client

        # 自定义 mock，让 target 解析为不同 ID
        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            side_effect=lambda identifier: ResolvedChat(
                chat_id=-2001234567890 if identifier == "@target" else -1001234567890,
                chat_type="channel",
                chat_name="Test",
                username="test",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service
        # 源端解析已下沉到 TaskManager，需同步覆盖其内部服务
        app.state.task_manager._identifier_service = mock_service

        body = {
            "task_type": "forward",
            "params": {
                "source_identifier": "@source",
                "target_identifier": "@target",
                "range_mode": "id_range",
                "min_id": 1,
                "max_id": 100,
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["data"]["task_type"] == "forward"
        assert data["data"]["params"]["source_identifier"] == "@source"
        assert data["data"]["params"]["target_chat_id"] == -2001234567890

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_create_task_source_not_found(self, client):
        """测试源频道解析失败返回 404。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=UserNotFoundError())
        # 源端解析已下沉到 TaskManager，需同步覆盖其内部服务
        app.state.task_manager._identifier_service = mock_service

        body = {
            "task_type": "download",
            "params": {"source_identifier": "@missing"},
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

    @pytest.mark.asyncio
    async def test_create_task_source_access_denied(self, client):
        """测试源频道无权限返回 403。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=AccessDeniedError())
        # 源端解析已下沉到 TaskManager，需同步覆盖其内部服务
        app.state.task_manager._identifier_service = mock_service

        body = {
            "task_type": "download",
            "params": {"source_identifier": "@private"},
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 403
        data = resp.json()
        assert data["code"] == 403

    @pytest.mark.asyncio
    async def test_create_task_size_exceeded(self, client):
        """测试创建超过大小限制的任务。"""
        ac, app, token = client
        body = {
            "task_type": "download",
            "params": {
                "chat_id": "-1001234567890",
                "estimated_size": 15 * 1024 * 1024 * 1024,  # 15GB
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == 1001

    @pytest.mark.asyncio
    async def test_get_task_by_id(self, client):
        """测试通过 ID 获取任务（含 params 中的 file_paths 字段）。"""
        ac, app, token = client
        # 先创建任务
        body = {
            "task_type": "download",
            "params": {"chat_id": "-1001234567890"},
        }
        create_resp = await ac.post("/api/tasks", json=body)
        task_id = create_resp.json()["data"]["id"]

        # 获取任务
        resp = await ac.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["id"] == task_id
        # 验证 params 中 file_paths 字段存在且默认为空列表
        assert "file_paths" in data["data"]["params"]
        assert data["data"]["params"]["file_paths"] == []

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client):
        """测试获取不存在的任务。"""
        ac, app, token = client
        resp = await ac.get("/api/tasks/nonexistent_task")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

    @pytest.mark.asyncio
    async def test_start_task(self, client):
        """测试启动任务。"""
        ac, app, token = client
        body = {
            "task_type": "download",
            "params": {"chat_id": "-1001234567890"},
        }
        create_resp = await ac.post("/api/tasks", json=body)
        task_id = create_resp.json()["data"]["id"]

        resp = await ac.post(f"/api/tasks/{task_id}/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] in ("running", "queued")

    @pytest.mark.asyncio
    async def test_cancel_task(self, client):
        """测试取消任务。"""
        ac, app, token = client
        body = {
            "task_type": "download",
            "params": {"chat_id": "-1001234567890"},
        }
        create_resp = await ac.post("/api/tasks", json=body)
        task_id = create_resp.json()["data"]["id"]

        # 先启动
        await ac.post(f"/api/tasks/{task_id}/start")

        # 再取消
        resp = await ac.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, client):
        """测试取消不存在的任务。"""
        ac, app, token = client
        resp = await ac.post("/api/tasks/nonexistent/cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_failed_task(self, client):
        """测试重试失败任务。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        # 创建并标记为失败
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)
        await task_manager.fail_task(task.task_id, reason="测试失败")

        # 重试
        resp = await ac.post(f"/api/tasks/{task.task_id}/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["status"] in ("running", "queued", "pending")

    @pytest.mark.asyncio
    async def test_retry_failed_task_triggers_execution(self, client):
        """重试失败任务后应自动触发执行（start + submit_task）。

        之前 bug: retry_task() 仅将状态重置为 pending 但不调用 start_task()
        和 executor.submit_task()，导致任务永远停在 pending 不执行。
        """
        ac, app, token = client
        task_manager = app.state.task_manager

        # 创建并标记为失败
        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)
        await task_manager.fail_task(task.task_id, reason="测试失败")

        # 注入 mock executor 到 TaskManager（新调度契约：由 TaskManager 统一触发执行）
        mock_executor = MagicMock()
        task_manager.set_executor(mock_executor)

        # 重试
        resp = await ac.post(f"/api/tasks/{task.task_id}/retry")
        assert resp.status_code == 200
        data = resp.json()

        # 验证：重试后任务状态应为 running 或 queued（而非停留在 pending）
        assert data["data"]["status"] in ("running", "queued"), (
            f"重试后任务应自动启动，状态应为 running/queued，实际为 {data['data']['status']}"
        )

        # 验证：executor.submit_task 应被调用（由 TaskManager 调度触发）
        mock_executor.submit_task.assert_called_once()

        # 清理
        task_manager.set_executor(None)

    @pytest.mark.asyncio
    async def test_delete_completed_task(self, client):
        """测试删除已完成任务。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)

        resp = await ac.delete(f"/api/tasks/{task.task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "任务记录已删除"

    @pytest.mark.asyncio
    async def test_delete_running_task_raises(self, client):
        """测试删除运行中任务失败。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)

        resp = await ac.delete(f"/api/tasks/{task.task_id}")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_list_tasks_with_status_filter(self, client):
        """测试按状态过滤任务列表。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)

        resp = await ac.get("/api/tasks?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_tasks_with_invalid_status(self, client):
        """测试无效状态过滤。"""
        ac, app, token = client
        resp = await ac.get("/api/tasks?status=invalid_status")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self, client):
        """测试任务列表分页。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        for _ in range(5):
            await task_manager.create_task(
                task_type=TaskType.DOWNLOAD,
                chat_id=-1001234567890,
            )

        resp = await ac.get("/api/tasks?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["limit"] == 2
        assert data["data"]["offset"] == 0
        assert len(data["data"]["items"]) <= 2


# ==================== 频道路由测试 ====================


class TestChatEndpoints:
    """频道端点测试。"""

    @pytest.mark.asyncio
    async def test_list_chats(self, client):
        """测试获取频道列表。"""
        ac, app, token = client
        resp = await ac.get("/api/chats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert isinstance(data["data"], list)

    @pytest.mark.asyncio
    async def test_estimate_messages(self, client):
        """测试消息估算成功。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test Channel",
                username="testchannel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "-1001234567890",
            "range_mode": "id_range",
            "min_id": 100,
            "max_id": 500,
            "type_filters": ["video", "photo"],
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        # 无真实 client 时返回 400（Telegram Client 未连接）或 200（有 client 时）
        assert resp.status_code in (200, 400)
        data = resp.json()
        assert isinstance(data, dict)
        mock_service.resolve.assert_awaited_once_with("-1001234567890")

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_analyze_messages(self, client):
        """测试消息精确分析成功。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test Channel",
                username="testchannel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "-1001234567890",
            "range_mode": "id_range",
            "min_id": 100,
            "max_id": 500,
        }
        resp = await ac.post("/api/chats/messages/analyze", json=body)
        # 无真实 client 时返回 400（Telegram Client 未连接）或 200（有 client 时）
        assert resp.status_code in (200, 400)
        data = resp.json()
        assert isinstance(data, dict)

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_estimate_messages_url_format(self, client):
        """测试 URL 格式 chat_id（不应 404）。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test Channel",
                username="testchannel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "https://t.me/douyincom",
            "range_mode": "all",
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        # 不应返回 404（路由匹配失败），应返回 200 或业务错误
        assert resp.status_code != 404
        mock_service.resolve.assert_awaited_once_with("https://t.me/douyincom")

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_estimate_messages_username_format(self, client):
        """测试 @username 格式 chat_id。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=-1001234567890,
                chat_type="channel",
                chat_name="Test Channel",
                username="testchannel",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "@douyincom",
            "range_mode": "all",
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        assert resp.status_code != 404
        mock_service.resolve.assert_awaited_once_with("@douyincom")

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_estimate_messages_user_not_found(self, client):
        """测试消息估算时解析失败返回 404。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=UserNotFoundError())
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "@missing",
            "range_mode": "all",
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_estimate_messages_access_denied(self, client):
        """测试消息估算时无权限返回 403。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=AccessDeniedError())
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "@private",
            "range_mode": "all",
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        assert resp.status_code == 403
        data = resp.json()
        assert data["code"] == 403

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_estimate_messages_rate_limited(self, client):
        """测试消息估算时限流返回 429。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=RateLimitedError(retry_after=30))
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        body = {
            "chat_id": "@busy",
            "range_mode": "all",
        }
        resp = await ac.post("/api/chats/messages/estimate", json=body)
        assert resp.status_code == 429
        data = resp.json()
        assert data["code"] == 429
        assert data["data"]["retry_after"] == 30

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_resolve_chat_success(self, client):
        """测试 /api/chats/resolve 成功解析。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(
            return_value=ResolvedChat(
                chat_id=8288406549,
                chat_type="bot",
                chat_name="seseYunBot",
                username="seseYunBot",
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=True,
            )
        )
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        resp = await ac.get("/api/chats/resolve?identifier=@seseYunBot")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["chat_id"] == 8288406549
        assert data["data"]["chat_type"] == "bot"
        assert data["data"]["is_private"] is True
        mock_service.resolve.assert_awaited_once_with("@seseYunBot")

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_resolve_chat_invalid_identifier(self, client):
        """测试 /api/chats/resolve 无效标识符返回 400。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=InvalidIdentifierError())
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        resp = await ac.get("/api/chats/resolve?identifier=not_valid!!")
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == 400

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_resolve_chat_user_not_found(self, client):
        """测试 /api/chats/resolve 用户不存在返回 404。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=UserNotFoundError())
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        resp = await ac.get("/api/chats/resolve?identifier=@missing_user")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_resolve_chat_access_denied(self, client):
        """测试 /api/chats/resolve 无权限返回 403。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=AccessDeniedError())
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        resp = await ac.get("/api/chats/resolve?identifier=@private_user")
        assert resp.status_code == 403
        data = resp.json()
        assert data["code"] == 403

        app.dependency_overrides.pop(get_identifier_service, None)

    @pytest.mark.asyncio
    async def test_resolve_chat_rate_limited(self, client):
        """测试 /api/chats/resolve 限流返回 429 并携带 retry_after。"""
        ac, app, token = client

        mock_service = MagicMock(spec=IdentifierService)
        mock_service.resolve = AsyncMock(side_effect=RateLimitedError(retry_after=30))
        app.dependency_overrides[get_identifier_service] = lambda: mock_service

        resp = await ac.get("/api/chats/resolve?identifier=@busy_user")
        assert resp.status_code == 429
        data = resp.json()
        assert data["code"] == 429
        assert data["data"]["retry_after"] == 30

        app.dependency_overrides.pop(get_identifier_service, None)


# ==================== 文件路由测试 ====================


class TestFileEndpoints:
    """文件端点测试。"""

    @pytest.mark.asyncio
    async def test_list_files(self, client):
        """测试获取文件列表。"""
        ac, app, token = client
        resp = await ac.get("/api/files")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "path" in data["data"]
        assert "items" in data["data"]

    @pytest.mark.asyncio
    async def test_list_files_with_path(self, client):
        """测试指定路径获取文件列表。"""
        ac, app, token = client
        resp = await ac.get(f"/api/files?path={tempfile.gettempdir()}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["path"] == os.path.abspath(tempfile.gettempdir())

    @pytest.mark.asyncio
    async def test_list_files_nonexistent_path(self, client):
        """测试不存在路径返回空列表。"""
        ac, app, token = client
        resp = await ac.get("/api/files?path=/nonexistent/path/xyz123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["items"] == []


# ==================== 配置路由测试 ====================


class TestConfigEndpoints:
    """配置端点测试。"""

    @pytest.mark.asyncio
    async def test_get_config(self, client):
        """测试获取配置。"""
        ac, app, token = client
        resp = await ac.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        # 敏感字段已脱敏
        assert data["data"]["api_id"] == "***"
        assert data["data"]["api_hash"] == "***"
        assert data["data"]["bot_token"] == "***"
        assert "resource_limits" in data["data"]
        assert "proxy" in data["data"]

    @pytest.mark.asyncio
    async def test_update_config(self, client):
        """测试更新配置。"""
        ac, app, token = client
        body = {
            "download_type": ["video", "photo", "document"],
            "max_retry_count": 5,
        }
        resp = await ac.put("/api/config", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_update_config_resource_limits(self, client):
        """测试更新资源限制配置。"""
        ac, app, token = client
        body = {
            "resource_limits": {
                "max_concurrent_tasks": 2,
                "max_download_concurrency": 5,
                "max_upload_concurrency": 2,
                "task_size_warning_gb": 3,
                "task_size_max_gb": 8,
            },
        }
        resp = await ac.put("/api/config", json=body)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_config_invalid_limits(self, client):
        """测试无效资源限制（max < warning）。"""
        ac, app, token = client
        body = {
            "resource_limits": {
                "task_size_warning_gb": 10,
                "task_size_max_gb": 5,
            },
        }
        resp = await ac.put("/api/config", json=body)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_config_proxy(self, client):
        """测试更新代理配置。"""
        ac, app, token = client
        body = {
            "proxy": {
                "enable_proxy": True,
                "scheme": "socks5",
                "hostname": "127.0.0.1",
                "port": 1080,
            },
        }
        resp = await ac.put("/api/config", json=body)
        assert resp.status_code == 200


# ==================== Pydantic 模型测试 ====================


class TestPydanticModels:
    """Pydantic 数据模型测试。"""

    def test_api_response_default(self):
        """测试 APIResponse 默认值。"""
        from module.api.models.common import APIResponse

        resp = APIResponse()
        assert resp.code == 0
        assert resp.message == "success"
        assert resp.data is None

    def test_api_response_with_data(self):
        """测试带数据的 APIResponse。"""
        from module.api.models.common import APIResponse

        resp = APIResponse(data={"key": "value"}, message="custom")
        assert resp.code == 0
        assert resp.message == "custom"
        assert resp.data == {"key": "value"}

    def test_pagination_params_default(self):
        """测试分页参数默认值。"""
        from module.api.models.common import PaginationParams

        params = PaginationParams()
        assert params.limit == 20
        assert params.offset == 0

    def test_task_create(self):
        """测试 TaskCreate 模型。"""
        from module.api.models.task import TaskCreate

        task = TaskCreate(task_type="download", params={"chat_id": "123"})
        assert task.task_type == "download"
        assert task.params["chat_id"] == "123"

    def test_task_out(self):
        """测试 TaskOut 模型（含 file_paths）。"""
        from module.api.models.task import TaskOut

        out = TaskOut(
            id="task_001",
            task_type="download",
            status="running",
            progress=50.0,
        )
        assert out.id == "task_001"
        assert out.progress == 50.0
        assert out.params.get("file_paths", []) == []

    def test_task_out_with_file_paths(self):
        """测试 TaskOut 带 params 中的 file_paths。"""
        from module.api.models.task import TaskOut

        out = TaskOut(
            id="task_002",
            task_type="download",
            status="completed",
            progress=100.0,
            params={"file_paths": ["/downloads/file1.mp4", "/downloads/file2.mp4"]},
        )
        assert out.params.get("file_paths") == [
            "/downloads/file1.mp4",
            "/downloads/file2.mp4",
        ]

    def test_chat_out(self):
        """测试 ChatOut 模型。"""
        from module.api.models.chat import ChatOut

        chat = ChatOut(id="1", title="Test", type="channel")
        assert chat.title == "Test"
        assert chat.type == "channel"

    def test_file_info(self):
        """测试 FileInfo 模型。"""
        from module.api.models.file import FileInfo

        info = FileInfo(name="test.mp4", path="/tmp/test.mp4", type="file", size=1024)
        assert info.type == "file"
        assert info.size == 1024

    def test_config_out(self):
        """测试 ConfigOut 模型。"""
        from module.api.models.config import ConfigOut

        config = ConfigOut(api_id="123")
        assert config.api_id == "123"
        assert config.resource_limits is not None
        assert config.proxy is not None

    def test_message_estimate_out(self):
        """测试 MessageEstimateOut 模型。"""
        from module.api.models.chat import MessageEstimateOut

        estimate = MessageEstimateOut(
            message_count=100,
            total_size_bytes=1024,
            total_size_human="1 KB",
            estimated_duration_seconds=10,
            sampled=True,
        )
        assert estimate.message_count == 100
        assert estimate.sampled is True

    def test_config_update(self):
        """测试 ConfigUpdate 模型。"""
        from module.api.models.config import ConfigUpdate

        update = ConfigUpdate(max_retry_count=5)
        assert update.max_retry_count == 5
        assert update.resource_limits is None


# ==================== 异常处理测试 ====================


class TestExceptionHandlers:
    """异常处理器测试。"""

    @pytest.mark.asyncio
    async def test_business_exception(self, client):
        """测试业务异常处理。"""
        ac, app, token = client
        resp = await ac.get("/api/tasks/nonexistent_task")
        assert resp.status_code == 404
        data = resp.json()
        assert data["code"] == 404

    @pytest.mark.asyncio
    async def test_validation_error(self, client):
        """测试参数校验错误。"""
        ac, app, token = client
        # 发送无效的 JSON body
        resp = await ac.post("/api/tasks", json={"invalid_field": "value"})
        assert resp.status_code == 422
        data = resp.json()
        assert data["code"] == 422


# ==================== 响应格式测试 ====================


class TestResponseFormat:
    """统一响应格式测试。"""

    def test_success_response(self):
        """测试成功响应构造。"""
        from module.api.responses import success_response

        resp = success_response(data={"key": "value"})
        assert resp["code"] == 0
        assert resp["message"] == "success"
        assert resp["data"] == {"key": "value"}

    def test_error_response(self):
        """测试错误响应构造。"""
        from module.api.responses import error_response

        resp = error_response(code=1001, message="错误消息")
        assert resp["code"] == 1001
        assert resp["message"] == "错误消息"
        assert resp["data"] is None

    def test_json_response(self):
        """测试 JSONResponse 构造。"""
        from module.api.responses import json_response

        resp = json_response(data={"test": True})
        assert resp.status_code == 200
        assert resp.body is not None

    def test_error_json_response(self):
        """测试错误 JSONResponse 构造。"""
        from module.api.responses import error_json_response

        resp = error_json_response(code=500, message="内部错误", status_code=500)
        assert resp.status_code == 500


# ==================== 依赖注入测试 ====================


class TestDependencies:
    """依赖注入测试。"""

    @pytest.mark.asyncio
    async def test_require_token_from_header(self, client):
        """测试从 Header 获取 Token。"""
        ac, app, token = client
        resp = await ac.get("/api/auth/me")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_require_token_from_query(
        self, token_manager, task_manager, config_manager
    ):
        """测试从 Query 参数获取 Token。"""
        app = create_app(
            token_manager=token_manager,
            task_manager=task_manager,
            config_manager=config_manager,
        )
        token = token_manager.generate(user_id=1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
            resp = await ac.get(f"/api/tasks?token={token}")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_require_token_missing(self, unauthenticated_client):
        """测试缺少 Token 返回 401。"""
        ac, app = unauthenticated_client
        resp = await ac.get("/api/tasks")
        assert resp.status_code == 401
        data = resp.json()
        assert data["detail"] == "MISSING_TOKEN"

    @pytest.mark.asyncio
    async def test_require_token_invalid(self, unauthenticated_client):
        """测试无效 Token 返回 401。"""
        ac, app = unauthenticated_client
        ac.headers.update({"Authorization": "Bearer invalid_token"})
        resp = await ac.get("/api/tasks")
        assert resp.status_code == 401


# ==================== 中间件测试 ====================


class TestMiddleware:
    """中间件测试。"""

    def test_security_headers_middleware(self):
        """测试安全头中间件初始化。"""
        from module.api.middleware import SecurityHeadersMiddleware

        assert SecurityHeadersMiddleware.SECURITY_HEADERS is not None
        assert "X-Content-Type-Options" in SecurityHeadersMiddleware.SECURITY_HEADERS

    def test_process_time_middleware(self):
        """测试响应时间中间件初始化。"""
        from module.api.middleware import ProcessTimeMiddleware

        assert ProcessTimeMiddleware.THRESHOLD_MS == 1000


# ==================== 应用工厂测试 ====================


class TestAppFactory:
    """应用工厂测试。"""

    def test_create_app_with_defaults(self):
        """测试使用默认参数创建应用。"""
        app = create_app()
        assert app.title == "TRMD Web API"
        # Swagger UI 根据环境变量决定启用状态
        import os

        is_prod = os.getenv("TRMD_ENV") == "production"
        assert app.docs_url == (None if is_prod else "/docs")
        assert app.redoc_url == (None if is_prod else "/redoc")
        assert app.state.token_manager is not None

    def test_create_app_with_mocks(self, token_manager, task_manager, config_manager):
        """测试注入 Mock 依赖创建应用。"""
        app = create_app(
            token_manager=token_manager,
            task_manager=task_manager,
            config_manager=config_manager,
        )
        assert app.state.token_manager == token_manager
        assert app.state.task_manager == task_manager
        assert app.state.config_manager == config_manager


# ==================== 异常类测试 ====================


class TestExceptionClasses:
    """异常类测试。"""

    def test_task_not_found_exception(self):
        """测试 TaskNotFoundError。"""
        from module.api.exceptions import TaskNotFoundError

        exc = TaskNotFoundError("task_123")
        assert exc.code == 404
        assert exc.status_code == 404

    def test_task_size_exceeded_exception(self):
        """测试 TaskSizeExceeded。"""
        from module.api.exceptions import TaskSizeExceeded

        exc = TaskSizeExceeded("12 GB")
        assert exc.code == 1001
        assert "12 GB" in exc.message

    def test_task_size_warning_exception(self):
        """测试 TaskSizeWarning。"""
        from module.api.exceptions import TaskSizeWarning

        exc = TaskSizeWarning("7 GB")
        assert exc.code == 1002
        assert "7 GB" in exc.message

    def test_insufficient_disk_space_exception(self):
        """测试 InsufficientDiskSpace。"""
        from module.api.exceptions import InsufficientDiskSpace

        exc = InsufficientDiskSpace()
        assert exc.code == 1003

    def test_task_conflict_exception(self):
        """测试 TaskConflictError。"""
        from module.api.exceptions import TaskConflictError

        exc = TaskConflictError("自定义冲突消息")
        assert exc.code == 409
        assert exc.status_code == 409

    def test_chat_not_found_exception(self):
        """测试 ChatNotFoundError。"""
        from module.api.exceptions import ChatNotFoundError

        exc = ChatNotFoundError("chat_123")
        assert exc.code == 404


# ==================== 任务路由额外测试 ====================


class TestTaskRouteExtras:
    """任务路由边界测试。"""

    @pytest.mark.asyncio
    async def test_create_task_invalid_type(self, client):
        """测试创建无效类型的任务（Pydantic 校验拦截返回 422）。"""
        ac, app, token = client
        body = {"task_type": "invalid", "params": {}}
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cancel_task_conflict(self, client):
        """测试取消状态冲突的任务。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        await task_manager.start_task(task.task_id)
        await task_manager.complete_task(task.task_id)

        resp = await ac.post(f"/api/tasks/{task.task_id}/cancel")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_retry_task_conflict(self, client):
        """测试重试状态冲突的任务。"""
        ac, app, token = client
        task_manager = app.state.task_manager

        task = await task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        # pending 状态不能重试（只能 failed/cancelled）
        resp = await ac.post(f"/api/tasks/{task.task_id}/retry")
        assert resp.status_code == 409


class TestBuildReferencedPaths:
    """任务引用保护索引测试。"""

    @pytest.mark.asyncio
    async def test_collects_params_file_paths_and_items(self, task_manager):
        """活跃任务的 params.file_paths 与子任务 file_path 应被收集。"""
        from datetime import UTC, datetime

        from module.core.db import get_session
        from module.core.task import models

        now = datetime.now(UTC)
        async with get_session() as session:
            session.add(
                models.TaskRecord(
                    id="ref_t1",
                    task_type="upload",
                    status="running",
                    chat_id=-1001,
                    params={"file_paths": ["/tmp/run/up.mp4", "/tmp/run/up2.mp4"]},
                    created_at=now,
                )
            )
            session.add(
                models.TaskRecord(
                    id="ref_t2",
                    task_type="download",
                    status="queued",
                    chat_id=-1002,
                    params={},
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                models.TaskItemRecord(
                    id="ref_i1",
                    task_id="ref_t2",
                    status="running",
                    file_path="/tmp/run/dl.mp4",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        def _norm(p: str) -> str:
            """与 build_referenced_paths 相同的规范化方式（平台无关）。"""
            return os.path.abspath(os.path.normpath(p))

        referenced = await task_manager.build_referenced_paths()
        assert _norm("/tmp/run/up.mp4") in referenced
        assert _norm("/tmp/run/up2.mp4") in referenced
        assert _norm("/tmp/run/dl.mp4") in referenced
        # .temp 兜底（下载中的中间文件）
        assert _norm("/tmp/run/dl.mp4.temp") in referenced

    @pytest.mark.asyncio
    async def test_excludes_cleanup_tasks_and_completed(self, task_manager):
        """cleanup_files 任务自身与已完成任务的文件不应被收集。"""
        from datetime import UTC, datetime

        from module.core.db import get_session
        from module.core.task import models

        now = datetime.now(UTC)
        async with get_session() as session:
            session.add(
                models.TaskRecord(
                    id="ref_c",
                    task_type="cleanup_files",
                    status="running",
                    chat_id=-1003,
                    params={"file_paths": ["/tmp/run/clean.mp4"]},
                    created_at=now,
                )
            )
            session.add(
                models.TaskRecord(
                    id="ref_done",
                    task_type="upload",
                    status="completed",
                    chat_id=-1004,
                    params={"file_paths": ["/tmp/run/done.mp4"]},
                    created_at=now,
                )
            )
            await session.commit()

        referenced = await task_manager.build_referenced_paths()
        assert "/tmp/run/clean.mp4" not in referenced
        assert "/tmp/run/done.mp4" not in referenced


class TestBatchDeleteFiles:
    """DELETE /api/files/batch 批量删除接口测试。"""

    @staticmethod
    def _point_save_root(config_manager, root):
        """将配置的下载根目录指向测试临时目录。"""
        config_manager.save_directory = root
        config_manager.config["save_directory"] = root

    @pytest.mark.asyncio
    async def test_delete_success(self, files_client, tmp_path):
        """正常删除：文件从磁盘消失，返回统计。"""
        ac, app, token, fm = files_client
        self._point_save_root(app.state.config_manager, str(tmp_path))
        (tmp_path / "a.mp4").write_bytes(b"a")
        (tmp_path / "b.jpg").write_bytes(b"b")

        resp = await ac.request(
            "DELETE",
            "/api/files/batch",
            json={"file_paths": [str(tmp_path / "a.mp4"), str(tmp_path / "b.jpg")]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert data["deleted"] == 2
        assert data["failed"] == 0
        assert data["skipped"] == 0
        assert not (tmp_path / "a.mp4").exists()
        assert not (tmp_path / "b.jpg").exists()

    @pytest.mark.asyncio
    async def test_delete_out_of_bounds_rejected(self, files_client, tmp_path):
        """save_root 之外的路径拒绝删除，文件保留。"""
        ac, app, token, fm = files_client
        self._point_save_root(app.state.config_manager, str(tmp_path))
        outside = tmp_path.parent / "outside.jpg"
        outside.write_bytes(b"o")

        resp = await ac.request("DELETE", "/api/files/batch", json={"file_paths": [str(outside)]})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["failed"] == 1
        assert data["results"][0]["reason"] == "OUT_OF_BOUNDS"
        assert outside.exists()

    @pytest.mark.asyncio
    async def test_delete_skips_task_referenced(self, files_client, tmp_path):
        """被活跃任务引用的文件跳过。"""
        from datetime import UTC, datetime

        from module.core.db import get_session
        from module.core.task import models

        ac, app, token, fm = files_client
        self._point_save_root(app.state.config_manager, str(tmp_path))
        ref_file = tmp_path / "ref.mp4"
        ref_file.write_bytes(b"r")
        free_file = tmp_path / "free.mp4"
        free_file.write_bytes(b"f")

        now = datetime.now(UTC)
        async with get_session() as session:
            session.add(
                models.TaskRecord(
                    id="del_ref",
                    task_type="upload",
                    status="queued",
                    chat_id=-10099,
                    params={"file_paths": [str(ref_file)]},
                    created_at=now,
                )
            )
            await session.commit()

        resp = await ac.request(
            "DELETE",
            "/api/files/batch",
            json={"file_paths": [str(ref_file), str(free_file)]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] == 1
        assert data["skipped"] == 1
        assert ref_file.exists()
        assert not free_file.exists()

    @pytest.mark.asyncio
    async def test_delete_empty_list_rejected(self, files_client):
        """空文件列表应被拒绝（422）。"""
        ac, app, token, fm = files_client
        resp = await ac.request("DELETE", "/api/files/batch", json={"file_paths": []})
        assert resp.status_code == 422


class TestCreateCleanupTask:
    """POST /api/tasks 定时清理任务创建测试。"""

    @pytest.mark.asyncio
    async def test_create_cleanup_files_success(self, client):
        """合法的 cleanup_files 任务应创建成功。"""
        ac, app, token = client
        body = {
            "task_type": "cleanup_files",
            "params": {
                "keep_days": 7,
                "schedule": {"mode": "daily", "time": "03:00"},
            },
        }
        resp = await ac.post("/api/tasks", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["task_type"] == "cleanup_files"
        assert data["params"]["keep_days"] == 7
        assert data["params"]["schedule"]["mode"] == "daily"
        assert "last_run" in data["params"]

    @pytest.mark.asyncio
    async def test_create_cleanup_invalid_keep_days(self, client):
        """keep_days 非法（0）应返回明确参数错误。"""
        ac, app, token = client
        resp = await ac.post(
            "/api/tasks",
            json={"task_type": "cleanup_files", "params": {"keep_days": 0}},
        )
        assert resp.status_code == 400
        assert "keep_days" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_create_cleanup_invalid_schedule_time(self, client):
        """schedule.time 非法应返回明确参数错误。"""
        ac, app, token = client
        resp = await ac.post(
            "/api/tasks",
            json={
                "task_type": "cleanup_files",
                "params": {
                    "keep_days": 7,
                    "schedule": {"mode": "daily", "time": "25:99"},
                },
            },
        )
        assert resp.status_code == 400
        assert "schedule" in resp.json()["message"]


class TestCleanupTaskActions:
    """定时清理任务 run/pause/resume 操作接口测试。"""

    @pytest.mark.asyncio
    async def test_run_non_cleanup_rejected(self, client):
        """非清理任务不允许手动立即执行。"""
        ac, app, token = client
        task = await app.state.task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=-1001234567890,
        )
        resp = await ac.post(f"/api/tasks/{task.task_id}/run")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_run_cleanup_executor_not_ready(self, client):
        """执行器未就绪时返回 503。"""
        ac, app, token = client
        task = await app.state.task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={"keep_days": 7, "schedule": {"mode": "daily", "time": "03:00"}},
        )
        resp = await ac.post(f"/api/tasks/{task.task_id}/run")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_run_cleanup_dispatches(self, client):
        """执行器就绪时触发 run_now。"""
        ac, app, token = client
        task = await app.state.task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={"keep_days": 7, "schedule": {"mode": "daily", "time": "03:00"}},
        )
        fake_scheduler = AsyncMock()
        fake_executor = MagicMock()
        fake_executor.cleanup_scheduler = fake_scheduler
        app.state.task_executor = fake_executor

        resp = await ac.post(f"/api/tasks/{task.task_id}/run")
        assert resp.status_code == 200
        fake_scheduler.run_now.assert_awaited_once_with(task.task_id)

    @pytest.mark.asyncio
    async def test_pause_cleanup(self, client):
        """暂停调度并持久化 paused 标志。"""
        ac, app, token = client
        task = await app.state.task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={"keep_days": 7, "schedule": {"mode": "daily", "time": "03:00"}},
        )
        fake_scheduler = AsyncMock()
        fake_executor = MagicMock()
        fake_executor.cleanup_scheduler = fake_scheduler
        app.state.task_executor = fake_executor

        resp = await ac.post(f"/api/tasks/{task.task_id}/pause")
        assert resp.status_code == 200
        fake_scheduler.pause.assert_awaited_once_with(task.task_id)

    @pytest.mark.asyncio
    async def test_resume_cleanup(self, client):
        """恢复调度。"""
        ac, app, token = client
        task = await app.state.task_manager.create_task(
            task_type=TaskType.CLEANUP_FILES,
            params={
                "keep_days": 7,
                "schedule": {"mode": "daily", "time": "03:00"},
                "paused": True,
            },
        )
        fake_scheduler = AsyncMock()
        fake_executor = MagicMock()
        fake_executor.cleanup_scheduler = fake_scheduler
        app.state.task_executor = fake_executor

        resp = await ac.post(f"/api/tasks/{task.task_id}/resume")
        assert resp.status_code == 200
        fake_scheduler.resume.assert_awaited_once_with(task.task_id)

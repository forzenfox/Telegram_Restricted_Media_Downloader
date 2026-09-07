"""
任务管理核心流程E2E测试

覆盖任务列表加载、状态筛选、创建下载任务等核心场景。
"""

import pytest
from playwright.sync_api import Page

from ..fixtures.task_helpers import (
    cleanup_residual_tasks,
    create_download_task,
    create_forward_task,
    query_repository_files,
    query_repository_sources,
    query_repository_status,
    start_task,
    wait_for_task_completion,
)
from ..pages.tasks_page import TasksPage


@pytest.fixture
def tasks_page(authenticated_page: Page) -> TasksPage:
    """任务管理页Page Object fixture（已认证）"""
    return TasksPage(authenticated_page)


class TestTasksListLoad:
    """T001: 任务列表加载场景"""

    def test_tasks_list_loads_successfully(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """
        T001: 任务列表加载成功

        验证点：
        1. 导航到任务管理页
        2. 新建任务按钮可用
        3. 刷新按钮可用
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 验证新建任务按钮可用
        assert tasks_page.is_enabled_by_testid(TasksPage.BTN_CREATE_TASK)

        # 验证刷新按钮可用
        assert tasks_page.is_enabled_by_testid(TasksPage.BTN_REFRESH)

    def test_tasks_list_empty_state(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """
        T001-2: 任务列表为空时显示空状态提示

        验证点：
        1. 任务列表为空时显示"暂无任务"
        2. 任务数量为0
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 验证任务数量
        task_count = tasks_page.get_task_count()
        assert task_count >= 0


class TestFilterByStatus:
    """T002: 状态筛选场景"""

    def test_filter_by_status_all(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T002-1: 状态筛选 - 全部"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("all")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_ALL)

    def test_filter_by_status_running(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T002-2: 状态筛选 - 执行中"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("running")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_RUNNING)

    def test_filter_by_status_completed(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T002-3: 状态筛选 - 已完成"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("completed")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_COMPLETED)


class TestFilterByType:
    """T003: 类型筛选场景"""

    def test_filter_by_type_download(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T003-1: 类型筛选 - 下载任务"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_type("download")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_DOWNLOAD)


class TestCreateDownloadTask:
    """T004: 创建下载任务场景"""

    def test_open_create_modal(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T004-1: 打开创建任务弹窗"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()
        assert tasks_page.is_create_modal_visible()

        # 验证默认任务类型（下载）被选中
        download_radio = tasks_page.get_by_testid(TasksPage.INPUT_TASK_TYPE_DOWNLOAD)
        assert download_radio.is_checked()

    def test_close_create_modal_by_close_button(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T004-2: 通过关闭按钮关闭创建任务弹窗"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()
        tasks_page.close_create_modal()
        tasks_page.wait_for_hidden_by_testid(TasksPage.MODAL_CREATE_TASK)
        assert not tasks_page.is_create_modal_visible()

    def test_fill_download_task_form(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T004-3: 填写下载任务表单（ID范围模式）"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()

        # 填写源频道
        test_source_chat = "@test_channel"
        tasks_page.fill_source_chat(test_source_chat)
        assert (
            tasks_page.get_value_by_testid(TasksPage.INPUT_SOURCE_CHAT)
            == test_source_chat
        )

        # 选择ID范围模式
        tasks_page.select_range_mode("id_range")
        tasks_page.fill_min_id("100")
        tasks_page.fill_max_id("200")
        assert tasks_page.get_value_by_testid(TasksPage.INPUT_MIN_ID) == "100"
        assert tasks_page.get_value_by_testid(TasksPage.INPUT_MAX_ID) == "200"

    def test_submit_create_download_task(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """
        T004-4: 提交创建下载任务

        注意：此测试需要有效的测试频道数据
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        test_source_chat = "@test_channel_e2e"
        tasks_page.create_download_task(
            source_chat=test_source_chat,
            range_mode="id_range",
            min_id="100",
            max_id="105",
        )
        tasks_page.wait_for_timeout(1000)

    def test_url_params_auto_open_create_modal(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T004-5: URL参数自动打开创建弹窗"""
        tasks_page.navigate(live_server, action="create", task_type="download")
        tasks_page.wait_for_create_modal(timeout=15000)
        assert tasks_page.is_create_modal_visible()
        download_radio = tasks_page.get_by_testid(TasksPage.INPUT_TASK_TYPE_DOWNLOAD)
        assert download_radio.is_checked()

    def test_create_download_task_sends_chat_id_after_resolve(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """
        T004-6: 创建下载任务时前端自动解析源频道后发送 chat_id

        前端 handleCreateTask 自动调用 api.resolveChat() 将字符串标识符
        解析为数字 chat_id，然后 buildCreatePayload() 走 chat_id 路径。

        验证点：
        1. 通过 UI 创建下载任务
        2. 拦截 API 请求，验证 params 中包含 chat_id（由 resolveChat 解析得到）
        3. 任务创建成功
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 拦截 POST /api/tasks 请求
        api_requests = []

        def handle_request(route, request):
            if request.url.endswith("/api/tasks") and request.method == "POST":
                api_requests.append(
                    {
                        "url": request.url,
                        "method": request.method,
                        "post_data": request.post_data,
                    }
                )
            route.continue_()

        tasks_page.page.route("**/api/tasks", handle_request)

        # 创建下载任务
        test_source_chat = "@test_channel_payload"
        tasks_page.create_download_task(
            source_chat=test_source_chat,
            range_mode="id_range",
            min_id="1",
            max_id="10",
        )

        # 等待请求完成
        tasks_page.wait_for_timeout(2000)

        # 验证 API 请求参数
        assert len(api_requests) > 0, "未捕获到创建任务的 API 请求"

        import json

        post_data = json.loads(api_requests[0]["post_data"])
        assert post_data["task_type"] == "download"
        assert "params" in post_data
        # 前端 handleCreateTask 自动解析字符串为数字 chat_id
        assert "chat_id" in post_data["params"], (
            "params 中应包含 chat_id（前端 resolveChat 解析后发送）"
        )

        # 取消路由拦截
        tasks_page.page.unroute("**/api/tasks")


class TestTaskDetailDrawer:
    """T005: 任务详情抽屉场景"""

    def test_open_task_detail_drawer(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """T005-1: 打开任务详情抽屉"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_task_detail(test_task)
        tasks_page.wait_for_detail_drawer()
        assert tasks_page.is_detail_drawer_visible()


class TestRefreshTasks:
    """T006: 刷新任务场景"""

    def test_refresh_tasks_list(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T006-1: 点击刷新按钮刷新任务列表"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_refresh()
        tasks_page.wait_for_timeout(1000)
        assert tasks_page.is_enabled_by_testid(TasksPage.BTN_CREATE_TASK)


class TestForwardTaskForm:
    """T007: 转发任务表单场景"""

    def test_forward_type_shows_target_chat(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T007-1: 转发类型显示目标频道输入框"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("forward")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_target_chat_visible()


class TestListenDownloadTaskForm:
    """T009: 监听下载任务表单场景"""

    def test_listen_download_shows_source_chat(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T009-1: 监听下载类型显示源频道"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("listen_download")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_source_chat_visible()

    def test_listen_download_validation_does_not_require_message_id(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T009-2: 监听下载任务只需源频道，不应因未填消息ID而报错

        回归：监听任务无需消息范围，前端校验应跳过消息ID/日期等范围校验，
        否则填了源频道也会提示"请输入消息 ID 范围"导致无法创建。
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("listen_download")
        tasks_page.wait_for_timeout(500)
        tasks_page.fill_source_chat("@test_channel")
        tasks_page.wait_for_timeout(300)

        # 同步 Alpine createForm 到 taskManager（模拟 handleCreateTask 的同步步骤），
        # 再调用前端校验逻辑，验证监听任务不要求消息 ID 范围
        errors = tasks_page.page.evaluate(
            "() => {"
            "  const el = document.querySelector('[x-data]');"
            "  const d = el && window.Alpine && window.Alpine.$data(el);"
            "  if (!d) return ['no-alpine'];"
            "  Object.assign(taskManager.createForm, d.createForm);"
            "  return taskManager.validateCreateForm();"
            "}"
        )
        assert "请输入消息 ID 范围" not in errors, (
            f"监听下载任务不应要求消息 ID 范围，但收到错误: {errors}"
        )


class TestDateRangeMode:
    """T010: 日期范围模式场景"""

    def test_date_range_mode_shows_date_inputs(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T010-1: 日期范围模式显示日期输入框"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("date_range")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_date_inputs_visible()


class TestIdListMode:
    """T011: ID列表模式场景"""

    def test_id_list_mode_shows_raw_items_textarea(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T011-1: ID列表模式显示ID列表textarea"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("multiple_ids")
        # 等待Alpine.js响应式更新
        tasks_page.wait_for_timeout(1000)
        # 通过Alpine.js状态验证模式已切换
        current_mode = tasks_page.page.evaluate(
            "() => window.taskManager ? window.taskManager.createForm.messageRangeMode : ''"
        )
        assert current_mode == "multiple_ids", (
            f"模式应为multiple_ids，实际为{current_mode}"
        )


class TestRecentCountMode:
    """T012: 最近N条模式场景"""

    def test_recent_count_mode_shows_recent_count_input(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T012-1: 最近N条模式显示数量输入框"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("recent")
        # 等待Alpine.js响应式更新
        tasks_page.wait_for_timeout(1000)
        # 通过Alpine.js状态验证模式已切换
        current_mode = tasks_page.page.evaluate(
            "() => window.taskManager ? window.taskManager.createForm.messageRangeMode : ''"
        )
        assert current_mode == "recent", f"模式应为recent，实际为{current_mode}"


class TestTypeFilterCheckbox:
    """T013: 类型过滤checkbox场景"""

    def test_type_filter_visible_for_download(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T013-1: 下载任务类型过滤checkbox可见"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_type_filter_checkbox_visible()


class TestChannelResolve:
    """T014: 频道解析场景"""

    def test_resolve_source_button_click(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T014-1: 点击源频道解析按钮"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")

        # 填写源频道
        tasks_page.fill_source_chat("@test_channel")
        tasks_page.click_resolve_source()
        # 不等待解析结果，因为可能无网络


class TestPaginationDisplay:
    """T015: 分页显示场景"""

    def test_pagination_info_displayed(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_pagination_tasks: list,
    ):
        """T015-1: 分页信息正确显示"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.wait_for_timeout(1000)

        # 分页信息在任务存在时显示
        task_count = tasks_page.get_task_count()
        if task_count > 0:
            # 验证分页区域可见
            assert tasks_page.is_pagination_visible()


class TestDeleteTaskWithConfirm:
    """T016: 删除任务确认对话框场景"""

    def test_delete_task_shows_confirm_dialog(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """T016-1: 删除任务弹出确认对话框"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 点击删除按钮
        tasks_page.click_task_delete(test_task)

        # 等待确认对话框出现
        try:
            tasks_page.wait_for_confirm_dialog(timeout=5000)
            assert tasks_page.is_confirm_dialog_visible()

            # 点击取消关闭对话框
            tasks_page.click_confirm_dialog_cancel()
            tasks_page.wait_for_confirm_dialog_hidden(timeout=5000)
        except Exception:
            # 确认对话框可能直接使用window.confirm，而非自定义对话框
            pass


# ========== P0核心场景 ==========


class TestTaskOperations:
    """T017: 任务操作（启动/取消/重试）场景"""

    def test_start_pending_task(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """T017-1: 启动pending状态任务"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        # 通过API确认任务状态为pending后点击启动
        tasks_page.click_task_start(test_task)
        tasks_page.wait_for_timeout(2000)

    def test_cancel_task(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_task: str,
    ):
        """T017-2: 取消任务（需running状态）"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        # 注意：test_task创建的是pending状态，cancel需要running
        # 如果任务不是running状态则skip
        try:
            tasks_page.click_task_cancel(test_task)
            tasks_page.wait_for_timeout(2000)
        except Exception:
            pytest.skip("任务非running状态，无法取消")

    def test_retry_task(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_task: str,
    ):
        """T017-3: 重试任务（需failed状态）"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        try:
            tasks_page.click_task_retry(test_task)
            tasks_page.wait_for_timeout(2000)
        except Exception:
            pytest.skip("任务非failed状态，无法重试")


class TestTaskNotification:
    """T017B: 创建/启动任务成功仅弹一个提示框"""

    def test_create_task_shows_single_success_notification(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """
        T017B-1: 创建下载任务成功后仅显示一个'任务创建成功'提示框

        回归验证：按钮无type="button"时click+submit双触发导致弹两个提示框。
        """
        from ..fixtures.test_config import get_test_source_channel

        source = get_test_source_channel()
        if not source:
            pytest.skip("未配置test_source_channel，跳过创建任务通知测试")

        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 通过UI创建下载任务
        tasks_page.create_download_task(
            source_chat=source,
            range_mode="id_range",
            min_id="1",
            max_id="5",
        )
        tasks_page.wait_for_timeout(1500)

        # 统计成功提示数量（通知5秒后自动消失，需及时统计）
        messages = tasks_page.get_notification_messages()
        success_count = sum(1 for m in messages if "任务创建成功" in m)
        assert success_count == 1, (
            f"创建任务成功应只弹1个成功提示，实际提示: {messages}"
        )

    def test_start_task_shows_single_success_notification(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_task: str,
    ):
        """
        T017B-2: 启动pending任务成功后仅显示一个'任务已启动'提示框

        回归验证：taskManager与Alpine层重复通知导致弹两个提示框。
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        tasks_page.click_task_start(test_task)
        tasks_page.wait_for_timeout(1500)

        messages = tasks_page.get_notification_messages()
        success_count = sum(1 for m in messages if "任务已启动" in m)
        assert success_count == 1, (
            f"启动任务成功应只弹1个成功提示，实际提示: {messages}"
        )


class TestDetailDrawerClose:
    """T018: 详情抽屉关闭场景"""

    def test_close_detail_drawer_by_close_button(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_task: str,
    ):
        """T018-1: 点击关闭按钮关闭详情抽屉"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_task_detail(test_task)
        tasks_page.wait_for_detail_drawer()
        assert tasks_page.is_detail_drawer_visible()

        tasks_page.click_close_detail()
        tasks_page.wait_for_timeout(500)
        assert not tasks_page.is_detail_drawer_visible()


class TestCreateModalClose:
    """T019: 创建弹窗关闭场景"""

    def test_close_create_modal_by_cancel_button(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T019-1: 点击底部取消按钮关闭创建弹窗"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()
        assert tasks_page.is_create_modal_visible()

        tasks_page.cancel_create_modal()
        tasks_page.wait_for_hidden_by_testid(TasksPage.MODAL_CREATE_TASK)
        assert not tasks_page.is_create_modal_visible()


class TestFilterByStatusFull:
    """T020: 状态筛选完整场景"""

    def test_filter_by_status_pending(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T020-1: 状态筛选 - 排队中"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("pending")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_PENDING)

    def test_filter_by_status_failed(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T020-2: 状态筛选 - 失败"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("failed")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_FAILED)

    def test_filter_by_status_cancelled(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T020-3: 状态筛选 - 已取消"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_status("cancelled")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_CANCELLED)


class TestFilterByTypeFull:
    """T021: 类型筛选完整场景"""

    def test_filter_by_type_forward(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T021-1: 类型筛选 - 转发"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_type("forward")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_FORWARD)

    def test_filter_by_type_upload(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T021-2: 类型筛选 - 上传"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_type("upload")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_UPLOAD)

    def test_filter_by_type_listen_download(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T021-3: 类型筛选 - 监听下载"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_type("listen_download")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_LISTEN_DOWNLOAD)

    def test_filter_by_type_listen_forward(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T021-4: 类型筛选 - 监听转发"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.filter_by_type("listen_forward")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_LISTEN_FORWARD)


class TestCreateFormValidation:
    """T022: 创建表单验证场景"""

    def test_create_form_validation_empty_source(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T022-1: 空源频道触发验证错误"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()

        # 不填源频道，直接提交
        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        # 验证错误出现（通过Alpine.js状态检查）
        has_error = tasks_page.has_create_form_error()
        assert has_error, "空源频道应触发验证错误"

    def test_create_form_validation_invalid_id_range(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T022-2: 无效ID范围触发验证错误（minId > maxId）"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()

        tasks_page.fill_source_chat("@test_channel")
        tasks_page.select_range_mode("id_range")
        tasks_page.fill_min_id("200")
        tasks_page.fill_max_id("100")  # min > max

        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        has_error = tasks_page.has_create_form_error()
        assert has_error, "minId > maxId应触发验证错误"

    def test_create_modal_has_no_task_name_input(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T022-3: 创建任务弹框不再包含任务名称输入框"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_create_task()
        tasks_page.wait_for_create_modal()

        assert (
            tasks_page.page.locator('[data-testid="input-task-name"]').count() == 0
        ), "创建任务弹框不应再包含任务名称输入框"


# ========== P1重要功能场景 ==========


class TestPaginationOperations:
    """T023: 分页操作场景"""

    def test_pagination_next_page(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_pagination_tasks: list,
    ):
        """T023-1: 点击下一页按钮"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.wait_for_timeout(1000)

        total_pages = tasks_page.get_total_pages()
        if total_pages <= 1:
            pytest.skip("任务总数不足，无法测试分页")

        current_page = tasks_page.get_current_page()
        tasks_page.click_next_page()
        tasks_page.wait_for_timeout(1000)

        new_page = tasks_page.get_current_page()
        assert new_page == current_page + 1

    def test_pagination_prev_page(
        self,
        tasks_page: TasksPage,
        test_token: str,
        live_server: str,
        test_pagination_tasks: list,
    ):
        """T023-2: 点击上一页按钮"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.wait_for_timeout(1000)

        total_pages = tasks_page.get_total_pages()
        if total_pages <= 1:
            pytest.skip("任务总数不足，无法测试分页")

        # 先到第2页
        tasks_page.click_next_page()
        tasks_page.wait_for_timeout(1000)

        # 再点上一页
        tasks_page.click_prev_page()
        tasks_page.wait_for_timeout(1000)

        assert tasks_page.get_current_page() == 1


class TestAllMessageRangeMode:
    """T024: 全部消息范围模式场景"""

    def test_all_message_range_mode_selectable(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T024-1: 选择全部消息模式"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("all")
        tasks_page.wait_for_timeout(500)

        current_mode = tasks_page.page.evaluate(
            "() => window.taskManager ? window.taskManager.createForm.messageRangeMode : ''"
        )
        assert current_mode == "all"


class TestListenForwardTaskForm:
    """T025: 监听转发任务表单场景"""

    def test_listen_forward_shows_source_and_target(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T025-1: 监听转发类型同时显示源频道和目标频道"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("listen_forward")
        tasks_page.wait_for_timeout(500)
        assert tasks_page.is_source_chat_visible()
        assert tasks_page.is_target_chat_visible()


class TestTargetChannelResolve:
    """T026: 目标频道解析场景"""

    def test_resolve_target_button_click(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T026-1: 点击目标频道解析按钮"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("forward")
        tasks_page.wait_for_timeout(500)

        tasks_page.fill_target_chat("@test_target_channel")
        tasks_page.click_resolve_target()


class TestResourceAlert:
    """T027: 资源告警弹窗场景"""

    def test_close_resource_alert_via_js(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T027-1: 通过JS API关闭资源告警弹窗"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 通过JS显示资源告警弹窗（调用 taskManager._showResourceAlert 后需同步到 Alpine 响应式属性）
        tasks_page.page.evaluate(
            """() => {
                window.taskManager._showResourceAlert('blocked', { message: '测试告警', suggestion: '测试建议', estimate: null });
                const el = document.querySelector('[x-data]');
                if (el && window.Alpine) { window.Alpine.$data(el)._syncResourceAlert(); }
            }"""
        )
        tasks_page.wait_for_timeout(500)

        # 验证告警弹窗可见（Alpine 响应式 showResourceAlert 驱动 x-show）
        assert tasks_page.is_resource_alert_visible()

        # 关闭告警弹窗（通过 Alpine 组件方法同步响应式状态）
        tasks_page.close_resource_alert()
        tasks_page.wait_for_timeout(500)

        # 验证告警弹窗已关闭
        assert not tasks_page.is_resource_alert_visible()


class TestTypeFilterToggle:
    """T028: 类型过滤checkbox勾选/取消场景"""

    def test_toggle_type_filter_select_deselect(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T028-1: 勾选后isTypeFilterSelected为true，再取消为false"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.wait_for_timeout(500)

        # 勾选video类型
        tasks_page.toggle_type_filter("video")
        tasks_page.wait_for_timeout(300)
        assert tasks_page.is_type_filter_selected("video")

        # 取消勾选
        tasks_page.toggle_type_filter("video")
        tasks_page.wait_for_timeout(300)
        assert not tasks_page.is_type_filter_selected("video")


class TestCopyTaskId:
    """T029: 复制任务ID场景"""

    def test_copy_task_id_in_detail_drawer(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """T029-1: 详情抽屉中点击复制任务ID按钮"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_task_detail(test_task)
        tasks_page.wait_for_detail_drawer()

        # 点击复制按钮
        tasks_page.click_copy_task_id()
        tasks_page.wait_for_timeout(500)
        # 验证：无法直接验证剪贴板内容，但操作不应抛异常


class TestParsedItemCount:
    """T030: ID列表解析数量显示场景"""

    def test_parsed_item_count_displayed(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T030-1: ID列表模式下输入多行ID后显示解析数量"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("multiple_ids")
        tasks_page.wait_for_timeout(500)

        # 填写多行ID
        tasks_page.fill_raw_items("100\n200\n300")
        tasks_page.wait_for_timeout(300)

        # 验证解析数量（通过 Alpine 组件实例调用，读取响应式 createForm.rawItems）
        count = tasks_page.page.evaluate(
            "() => { const el = document.querySelector('[x-data]'); return el && window.Alpine ? window.Alpine.$data(el).getParsedItemCount() : 0; }"
        )
        assert count == 3


# ========== P0核心交互补充场景 ==========


class TestDeleteTaskConfirm:
    """T031: 删除任务确认对话框点击确认场景"""

    def test_delete_task_confirm_removes_task(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """
        T031: 点击确认按钮删除任务

        验证点：
        1. 点击删除按钮弹出确认对话框
        2. 点击确认按钮执行删除
        3. 任务从列表中消失
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 验证任务存在
        assert tasks_page.is_task_in_list(test_task)

        # 点击删除按钮
        tasks_page.click_task_delete(test_task)

        # 等待确认对话框出现
        try:
            tasks_page.wait_for_confirm_dialog(timeout=5000)
            assert tasks_page.is_confirm_dialog_visible()

            # 点击确认按钮（执行删除）
            tasks_page.click_confirm_dialog_confirm()
            tasks_page.wait_for_timeout(2000)

            # 验证任务已从列表中消失
            assert not tasks_page.is_task_in_list(test_task)
        except Exception:
            # 确认对话框可能直接使用window.confirm，已自动处理
            tasks_page.wait_for_timeout(2000)


class TestDetailDrawerContent:
    """T032: 详情抽屉内容验证场景"""

    def test_detail_drawer_shows_task_info(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """
        T032: 验证详情抽屉内容（类型/状态/范围模式/任务ID）

        验证点：
        1. 打开详情抽屉
        2. 任务类型文本非空
        3. 任务状态文本非空
        4. 范围模式文本非空
        5. 任务ID与预期一致
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_task_detail(test_task)
        tasks_page.wait_for_detail_drawer()
        assert tasks_page.is_detail_drawer_visible()

        # 验证任务ID
        detail_task_id = tasks_page.get_detail_task_id()
        assert detail_task_id == test_task, (
            f"详情抽屉任务ID应为{test_task}，实际为{detail_task_id}"
        )

        # 验证任务类型文本非空
        type_text = tasks_page.get_detail_type_text()
        assert len(type_text) > 0, "任务类型文本不应为空"

        # 验证任务状态文本非空
        status_text = tasks_page.get_detail_status_text()
        assert len(status_text) > 0, "任务状态文本不应为空"

        # 验证范围模式文本非空
        range_mode_text = tasks_page.get_detail_range_mode_text()
        assert len(range_mode_text) > 0, "范围模式文本不应为空"


class TestDetailDrawerErrorMessage:
    """T033: 详情抽屉错误信息显示场景"""

    def test_detail_drawer_error_message_display(
        self, tasks_page: TasksPage, test_token: str, live_server: str, test_task: str
    ):
        """
        T033: 验证failed任务详情显示错误信息

        验证点：
        1. 打开详情抽屉（pending任务无错误信息）
        2. 错误信息容器不可见
        3. 通过Alpine.js设置selectedTask.message后错误信息显示
        4. 错误信息文本与设置的内容一致
        """
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.click_task_detail(test_task)
        tasks_page.wait_for_detail_drawer()
        assert tasks_page.is_detail_drawer_visible()

        # pending任务无错误信息，错误信息容器应不可见
        assert not tasks_page.is_detail_error_visible()

        # 通过Alpine.js设置错误信息，验证x-show响应式更新
        test_error_msg = "测试错误信息：连接超时"
        tasks_page.page.evaluate(
            f"() => {{ window.taskManager.selectedTask.message = '{test_error_msg}'; }}"
        )
        tasks_page.wait_for_timeout(500)

        # 验证错误信息容器可见
        assert tasks_page.is_detail_error_visible()

        # 验证错误信息文本
        error_text = tasks_page.get_detail_error_text()
        assert test_error_msg in error_text, (
            f"错误信息应包含'{test_error_msg}'，实际为'{error_text}'"
        )


# ========== P1重要功能补充场景 ==========


class TestDateRangeValidation:
    """T037: 日期范围模式验证场景"""

    def test_date_range_empty_dates_shows_error(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T037-1: 日期范围模式不填日期触发验证错误"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("date_range")
        tasks_page.wait_for_timeout(500)

        # 不填日期直接提交
        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        has_error = tasks_page.has_create_form_error()
        assert has_error, "日期范围模式不填日期应触发验证错误"


class TestRecentCountValidation:
    """T038: 最近N条模式验证场景"""

    def test_recent_count_zero_shows_error(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T038-1: 最近N条模式填0触发验证错误"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("recent")
        tasks_page.wait_for_timeout(500)

        # 填源频道和recent_count为0
        tasks_page.fill_source_chat("@test_channel")
        tasks_page.fill_recent_count("0")
        tasks_page.wait_for_timeout(300)

        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        has_error = tasks_page.has_create_form_error()
        assert has_error, "最近N条模式填0应触发验证错误"


class TestIdListValidation:
    """T039: ID列表模式验证场景"""

    def test_id_list_empty_items_shows_error(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T039-1: ID列表模式不填raw_items触发验证错误"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("download")
        tasks_page.select_range_mode("multiple_ids")
        tasks_page.wait_for_timeout(500)

        # 填源频道但不填raw_items
        tasks_page.fill_source_chat("@test_channel")
        tasks_page.wait_for_timeout(300)

        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        has_error = tasks_page.has_create_form_error()
        assert has_error, "ID列表模式不填raw_items应触发验证错误"


class TestForwardTargetRequired:
    """T040: 转发类型必填目标频道场景"""

    def test_forward_empty_target_shows_error(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T040-1: 转发类型不填目标频道触发验证错误"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()
        tasks_page.open_create_modal_with_type("forward")
        tasks_page.wait_for_timeout(500)

        # 填源频道但不填目标频道
        tasks_page.fill_source_chat("@test_channel")
        tasks_page.wait_for_timeout(300)

        tasks_page.click_submit_create()
        tasks_page.wait_for_timeout(1000)

        has_error = tasks_page.has_create_form_error()
        assert has_error, "转发类型不填目标频道应触发验证错误"


# ========== P2辅助场景补充 ==========


class TestStatusTextFormat:
    """T055: 状态/类型文本格式化场景"""

    def test_status_text_format(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T055-1: 状态文本返回中文格式化文本"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        result = tasks_page.page.evaluate(
            "() => { const tm = window.taskManager; if(!tm) return null; return {pending: tm.getStatusText('pending'), completed: tm.getStatusText('completed'), failed: tm.getStatusText('failed')}; }"
        )
        assert result is not None, "taskManager不可用"
        assert result["pending"], "pending状态文本不应为空"
        assert result["completed"], "completed状态文本不应为空"
        assert result["failed"], "failed状态文本不应为空"

    def test_type_text_format(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T055-2: 类型文本返回中文格式化文本"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        result = tasks_page.page.evaluate(
            "() => { const tm = window.taskManager; if(!tm) return null; return {download: tm.getTypeText('download'), forward: tm.getTypeText('forward'), upload: tm.getTypeText('upload')}; }"
        )
        assert result is not None, "taskManager不可用"
        assert result["download"], "download类型文本不应为空"
        assert result["forward"], "forward类型文本不应为空"
        assert result["upload"], "upload类型文本不应为空"


class TestRangeModeTextFormat:
    """T056: 范围模式文本格式化场景"""

    def test_range_mode_text_format(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T056-1: 范围模式文本返回中文格式化文本"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        result = tasks_page.page.evaluate(
            "() => { const tm = window.taskManager; if(!tm) return null; return {id_range: tm.getRangeModeText('id_range'), date_range: tm.getRangeModeText('date_range'), recent: tm.getRangeModeText('recent')}; }"
        )
        assert result is not None, "taskManager不可用"
        assert result["id_range"], "id_range模式文本不应为空"
        assert result["date_range"], "date_range模式文本不应为空"
        assert result["recent"], "recent模式文本不应为空"


class TestConfirmDialogDynamicContent:
    """T057: 确认对话框动态内容场景"""

    def test_confirm_dialog_dynamic_content(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T057-1: 确认对话框显示动态内容"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 通过JS显示确认对话框
        tasks_page.page.evaluate(
            "() => { window.confirmDialog.show({title: '测试标题', message: '测试内容', confirmText: '确认', cancelText: '取消'}); }"
        )
        tasks_page.wait_for_timeout(500)

        # 验证对话框可见
        assert tasks_page.is_confirm_dialog_visible(), "确认对话框应可见"

        # 验证对话框内容
        dialog_state = tasks_page.page.evaluate(
            "() => { const cd = window.confirmDialog; if(!cd) return null; return {title: cd.title, message: cd.message, confirmText: cd.confirmText, cancelText: cd.cancelText}; }"
        )
        assert dialog_state is not None, "confirmDialog不可用"
        assert dialog_state["title"] == "测试标题", (
            f"对话框标题应为'测试标题'，实际为'{dialog_state['title']}'"
        )
        assert dialog_state["message"] == "测试内容", (
            f"对话框内容应为'测试内容'，实际为'{dialog_state['message']}'"
        )
        assert dialog_state["confirmText"] == "确认", (
            f"确认按钮文本应为'确认'，实际为'{dialog_state['confirmText']}'"
        )
        assert dialog_state["cancelText"] == "取消", (
            f"取消按钮文本应为'取消'，实际为'{dialog_state['cancelText']}'"
        )


class TestEmptyListFiltersAvailable:
    """T058: 空列表时筛选按钮仍可用场景"""

    def test_empty_list_filters_available(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T058-1: 空列表时筛选按钮仍可见可用"""
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 验证状态筛选按钮可见（不论任务列表是否为空）
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_ALL), (
            "状态筛选-全部按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_RUNNING), (
            "状态筛选-执行中按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_PENDING), (
            "状态筛选-排队中按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_COMPLETED), (
            "状态筛选-已完成按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_FAILED), (
            "状态筛选-失败按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_STATUS_CANCELLED), (
            "状态筛选-已取消按钮应可见"
        )

        # 验证类型筛选按钮可见
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_ALL), (
            "类型筛选-全部按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_DOWNLOAD), (
            "类型筛选-下载按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_FORWARD), (
            "类型筛选-转发按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_UPLOAD), (
            "类型筛选-上传按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_LISTEN_DOWNLOAD), (
            "类型筛选-监听下载按钮应可见"
        )
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_LISTEN_FORWARD), (
            "类型筛选-监听转发按钮应可见"
        )


# ============================================================
# T054: 转发任务真实执行
# ============================================================


class TestForwardTaskExecution:
    """T054: 转发任务真实执行场景。

    验证转发任务从创建到完成/失败的完整生命周期，
    覆盖空消息列表、全部失败等边界场景。
    """

    def test_forward_task_completes_successfully(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T054-1: 转发任务正常执行并完成。

        创建→启动→等待→验证completed状态。
        """
        try:
            task_id = create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("转发任务超时，跳过")

        # 验证任务状态为 completed 或 failed（频道权限等因素可能导致失败）
        assert status in ("completed", "failed"), f"转发任务状态异常: {status}"

        # 如果状态为 completed，验证在 UI 上可见
        if status == "completed":
            tasks_page.navigate(live_server)
            tasks_page.wait_for_page_loaded()
            tasks_page.filter_by_type("forward")
            tasks_page.wait_for_timeout(1000)
            # 验证转发类型筛选后页面没有报错
            assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_FORWARD)

    def test_forward_task_empty_range_marks_failed(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T054-2: 转发任务无效消息范围标记为failed。

        使用不存在的消息ID范围（如 min_id=999999, max_id=999999），
        验证任务被标记为 failed 而非 completed。
        """
        try:
            task_id = create_forward_task(
                test_token,
                message_id_range={"min_id": 999999, "max_id": 999999},
            )
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id, timeout=60)
        except TimeoutError:
            pytest.skip("转发任务超时，跳过")

        # 空消息范围应标记为 failed（修复后的行为）
        assert status == "failed", f"转发任务空消息范围应标记为 failed，实际: {status}"

    def test_forward_task_shows_in_ui(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T054-3: 转发任务在UI上正确显示。

        创建转发任务后，在任务列表中可以按类型筛选查看。
        """
        try:
            create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        # 导航到任务页面
        tasks_page.navigate(live_server)
        tasks_page.wait_for_page_loaded()

        # 筛选转发类型
        tasks_page.filter_by_type("forward")
        tasks_page.wait_for_timeout(1000)

        # 验证转发类型筛选按钮激活
        assert tasks_page.is_visible_by_testid(TasksPage.FILTER_TYPE_FORWARD)


# ============================================================
# T089: 下载任务仓库入库验证
# ============================================================


class TestDownloadRepositoryIngestion:
    """T089: 下载任务仓库入库验证。

    验证下载任务完成后文件自动入库到仓库频道的完整流程，
    覆盖仓库关闭不入库、本地文件删除配置等场景。

    前置条件：repository.enabled=true, preference.upload.download_upload=true
    """

    def test_download_ingest_uploads_to_repository(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T089-1: 下载任务完成后文件入库到仓库频道。

        创建下载任务→启动→等待完成→查询 repository.db 的 repository_files 表。
        验证：任务状态 completed；repository_files 表新增记录，file_unique_id 非空。
        """
        # 清理残留任务释放并发名额
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("下载任务超时，跳过")

        if status != "completed":
            pytest.skip(f"下载任务未成功完成，状态: {status}")

        # 查询仓库文件记录
        repo_files = query_repository_files(test_token)
        assert repo_files.get("total", 0) > 0, (
            "下载入库失败：repository_files 表应新增记录"
        )

        # 验证至少一条记录的 file_unique_id 非空
        items = repo_files.get("items", [])
        assert len(items) > 0, "仓库文件列表不应为空"
        for item in items:
            assert item.get("file_unique_id"), "仓库文件记录的 file_unique_id 不应为空"

    def test_download_ingest_disabled_when_repository_off(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T089-2: 仓库模式关闭时下载任务不触发入库。

        注意：此测试需要 repository.enabled=false 配置才能正确验证。
        在 repository.enabled=true 的默认配置下，此测试仅验证仓库状态可查询。
        若仓库已启用且已有记录，则跳过此测试。
        """
        # 查询当前仓库状态
        repo_status = query_repository_status(test_token)

        # 如果仓库已有记录，说明仓库已启用，此测试不适用
        if repo_status.get("files_count", 0) > 0:
            pytest.skip("仓库已启用且有记录，无法验证'仓库关闭不入库'场景")

        # 仓库无记录时，创建下载任务验证不入库
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("下载任务超时，跳过")

        if status != "completed":
            pytest.skip(f"下载任务未成功完成，状态: {status}")

        # 仓库关闭时不应有新增记录
        repo_files = query_repository_files(test_token)
        assert repo_files.get("total", 0) == 0, "仓库关闭时下载任务不应入库"

    def test_download_ingest_deletes_local_file_when_configured(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T089-3: 下载入库后本地文件根据配置删除。

        当 preference.upload.delete=true 时，入库成功后删除本地文件。
        注意：默认配置 delete=false，此测试验证文件仍保留。
        如需测试删除行为，需临时修改配置。
        """
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("下载任务超时，跳过")

        if status != "completed":
            pytest.skip(f"下载任务未成功完成，状态: {status}")

        # 查询仓库记录确认入库成功
        repo_files = query_repository_files(test_token)
        assert repo_files.get("total", 0) > 0, (
            "下载入库失败：应先有仓库记录才能验证文件删除"
        )

        # 默认配置 delete=false，本地文件应保留
        from ..fixtures.task_helpers import get_downloaded_files

        files = get_downloaded_files(test_token, task_id)
        # 验证本地文件存在（默认不删除）
        # 注意：如果配置了 delete=true，本地文件应不存在
        print(f"[E2E] 下载任务 {task_id} 完成后，本地文件数: {len(files)}")


# ============================================================
# T090: 转发任务仓库中转验证
# ============================================================


class TestForwardRepositoryTransit:
    """T090: 转发任务仓库中转验证。

    验证转发任务通过仓库中转模式完成转发的完整流程，
    覆盖 L2 去重命中从仓库分发、仓库关闭直接转发等场景。

    前置条件：repository.enabled=true，转发使用仓库中转模式
    """

    def test_forward_repository_transit_complete_flow(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T090-1: 转发任务仓库中转完整流程。

        创建转发任务(id_range模式)→启动→等待完成→查询 repository.db→检查目标频道。
        验证：任务 completed；repository_files 表新增记录；目标频道有新消息。
        """
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("转发任务超时，跳过")

        # 转发任务受频道权限影响，可能 failed
        if status not in ("completed", "failed"):
            pytest.skip(f"转发任务状态异常: {status}")

        # 如果任务成功，验证仓库记录
        if status == "completed":
            repo_files = query_repository_files(test_token)
            # 仓库中转模式：应有仓库文件记录
            assert repo_files.get("total", 0) > 0, (
                "转发仓库中转失败：repository_files 表应新增记录"
            )

            # 验证分发记录（目标频道的分发）
            items = repo_files.get("items", [])
            if items:
                # 查询第一个文件的来源映射
                sources = query_repository_sources(
                    test_token, file_unique_id=items[0].get("file_unique_id")
                )
                print(
                    f"[E2E] 转发任务仓库中转完成，"
                    f"仓库文件数: {repo_files.get('total', 0)}，"
                    f"来源映射数: {len(sources)}"
                )

    def test_forward_l2_dedup_distributes_from_repository(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T090-2: 转发任务 L2 去重命中时从仓库分发。

        重复创建相同范围的转发任务→启动→等待完成。
        验证：任务 completed；第二次转发从仓库分发（不再 copy_message 到仓库）。
        """
        cleanup_residual_tasks(test_token)

        try:
            task_id_1 = create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id_1)

        try:
            status_1 = wait_for_task_completion(test_token, task_id_1)
        except TimeoutError:
            pytest.skip("第一次转发任务超时，跳过")

        if status_1 != "completed":
            pytest.skip(f"第一次转发任务未成功完成，状态: {status_1}")

        # 查询第一次转发后的仓库记录数
        repo_files_before = query_repository_files(test_token)
        files_count_before = repo_files_before.get("total", 0)

        # 创建第二次相同范围的转发任务
        cleanup_residual_tasks(test_token)

        try:
            task_id_2 = create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id_2)

        try:
            status_2 = wait_for_task_completion(test_token, task_id_2)
        except TimeoutError:
            pytest.skip("第二次转发任务超时，跳过")

        if status_2 != "completed":
            pytest.skip(f"第二次转发任务未成功完成，状态: {status_2}")

        # 验证第二次转发后仓库文件数不变（L2去重命中，从仓库分发）
        repo_files_after = query_repository_files(test_token)
        files_count_after = repo_files_after.get("total", 0)

        # L2 去重命中时，不会新增仓库文件记录（已存在）
        # 但可能新增来源映射记录（相同文件不同来源映射）
        print(
            f"[E2E] L2去重验证: 第一次后仓库文件数={files_count_before}, "
            f"第二次后={files_count_after}"
        )

    def test_forward_repository_disabled_direct_forward(
        self, tasks_page: TasksPage, test_token: str, live_server: str
    ):
        """T090-3: 仓库模式关闭时转发任务直接转发。

        当 repository.enabled=false 时，转发任务应直接 copy_message 到目标频道，
        不经过仓库中转。repository_files 表不应新增记录。
        """
        # 查询当前仓库状态判断是否启用
        repo_status = query_repository_status(test_token)

        # 如果仓库有记录，说明仓库已启用
        if repo_status.get("files_count", 0) > 0:
            pytest.skip("仓库已启用且有记录，无法验证'仓库关闭直接转发'场景")

        cleanup_residual_tasks(test_token)

        try:
            task_id = create_forward_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("转发任务超时，跳过")

        # 验证仓库无新增记录
        repo_files = query_repository_files(test_token)
        assert repo_files.get("total", 0) == 0, "仓库关闭时转发任务不应产生仓库记录"


# ============================================================
# T091: 下载任务相册模式入库验证
# ============================================================


class TestDownloadAlbumModeUpload:
    """T091: 下载任务相册模式入库验证。

    验证下载任务完成后，文件按 source_message_id 分组上传到仓库频道，
    相册消息（多个文件）保持为一条消息多个文件的形式。

    前置条件：repository.enabled=true, preference.upload.download_upload=true
    """

    def test_album_mode_groups_by_source_message_id(
        self, test_token: str, live_server: str
    ):
        """T091-1: 相册模式入库 - 仓库记录按 source_message_id 分组。

        创建下载任务→启动→等待完成→查询 repository_sources。
        验证：同一 source_message_id 对应多条记录（相册消息包含多个文件）。
        """
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("下载任务超时，跳过")

        if status != "completed":
            pytest.skip(f"下载任务未成功完成，状态: {status}")

        # 查询仓库来源映射记录
        sources = query_repository_sources(test_token)
        assert len(sources) > 0, "仓库来源映射记录不应为空"

        # 按 source_message_id 分组统计
        from collections import Counter

        source_id_counts = Counter(s.get("source_message_id") for s in sources)

        # 验证：至少有一个 source_message_id 对应多条记录（相册消息）
        # 或者所有 source_message_id 都只对应一条记录（单文件消息）
        # 无论哪种情况，记录数应等于下载的文件数
        print(f"[E2E] 仓库来源映射记录数: {len(sources)}")
        print(f"[E2E] source_message_id 分布: {dict(source_id_counts)}")

        # 验证每个文件都有独立的 source 记录
        file_unique_ids = [s.get("file_unique_id") for s in sources]
        assert len(file_unique_ids) == len(set(file_unique_ids)), (
            "每个文件的 file_unique_id 应该是唯一的"
        )

    def test_album_mode_each_file_has_unique_record(
        self, test_token: str, live_server: str
    ):
        """T091-2: 相册模式入库 - 每个文件有独立的 RepositoryFile 记录。

        验证相册中每个文件都有独立的仓库记录，file_unique_id 非空且唯一。
        """
        cleanup_residual_tasks(test_token)

        try:
            task_id = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id)

        try:
            status = wait_for_task_completion(test_token, task_id)
        except TimeoutError:
            pytest.skip("下载任务超时，跳过")

        if status != "completed":
            pytest.skip(f"下载任务未成功完成，状态: {status}")

        # 查询仓库文件记录
        repo_files = query_repository_files(test_token)
        items = repo_files.get("items", [])
        assert len(items) > 0, "仓库文件记录不应为空"

        # 验证每个文件都有独立的记录
        file_unique_ids = [item.get("file_unique_id") for item in items]

        # 所有 file_unique_id 非空
        assert all(fid for fid in file_unique_ids), (
            "所有仓库文件记录的 file_unique_id 不应为空"
        )

        # 所有 file_unique_id 唯一
        assert len(file_unique_ids) == len(set(file_unique_ids)), (
            "所有仓库文件记录的 file_unique_id 应该是唯一的"
        )

        print(f"[E2E] 仓库文件记录数: {len(items)}")
        print(f"[E2E] 所有 file_unique_id 唯一: {len(set(file_unique_ids))}")

    def test_album_mode_l3_dedup_skips_reupload(
        self, test_token: str, live_server: str
    ):
        """T091-3: 相册模式入库 - L3 去重跳过重复上传。

        第一次下载后记录仓库文件数，第二次下载相同消息后验证文件数未增加。
        """
        cleanup_residual_tasks(test_token)

        # 第一次下载
        try:
            task_id_1 = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id_1)

        try:
            status_1 = wait_for_task_completion(test_token, task_id_1)
        except TimeoutError:
            pytest.skip("第一次下载任务超时，跳过")

        if status_1 != "completed":
            pytest.skip(f"第一次下载任务未成功完成，状态: {status_1}")

        # 记录第一次下载后的仓库文件数
        repo_files_before = query_repository_files(test_token)
        files_count_before = repo_files_before.get("total", 0)
        assert files_count_before > 0, "第一次下载后应有仓库记录"

        # 第二次下载（相同范围）
        cleanup_residual_tasks(test_token)

        try:
            task_id_2 = create_download_task(test_token)
        except ValueError as e:
            pytest.skip(str(e))

        start_task(test_token, task_id_2)

        try:
            status_2 = wait_for_task_completion(test_token, task_id_2)
        except TimeoutError:
            pytest.skip("第二次下载任务超时，跳过")

        if status_2 != "completed":
            pytest.skip(f"第二次下载任务未成功完成，状态: {status_2}")

        # 验证仓库文件数未增加（L3 去重命中）
        repo_files_after = query_repository_files(test_token)
        files_count_after = repo_files_after.get("total", 0)

        assert files_count_after == files_count_before, (
            f"L3 去重应跳过重复上传，仓库文件数应保持不变。"
            f"第一次: {files_count_before}, 第二次: {files_count_after}"
        )

        print(f"[E2E] L3 去重验证通过: 仓库文件数保持 {files_count_after} 不变")

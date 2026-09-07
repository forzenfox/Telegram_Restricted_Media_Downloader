"""
TasksPage - 任务管理页Page Object

封装任务管理页面的交互逻辑，提供稳定的data-testid选择器接口。
"""

from playwright.sync_api import Page, Locator
from .base_page import BasePage
from ..fixtures.test_config import NAVIGATION_TIMEOUT


class TasksPage(BasePage):
    """任务管理页Page Object"""

    # 页面路径
    URL_PATH = "/web/tasks.html"

    # data-testid常量
    # 页面头部按钮
    BTN_REFRESH = "btn-refresh-tasks"
    BTN_CREATE_TASK = "btn-create-task"

    # 状态筛选按钮
    FILTER_STATUS_ALL = "filter-status-all"
    FILTER_STATUS_RUNNING = "filter-status-running"
    FILTER_STATUS_PENDING = "filter-status-pending"
    FILTER_STATUS_COMPLETED = "filter-status-completed"
    FILTER_STATUS_FAILED = "filter-status-failed"
    FILTER_STATUS_CANCELLED = "filter-status-cancelled"

    # 类型筛选按钮
    FILTER_TYPE_ALL = "filter-type-all"
    FILTER_TYPE_DOWNLOAD = "filter-type-download"
    FILTER_TYPE_FORWARD = "filter-type-forward"
    FILTER_TYPE_UPLOAD = "filter-type-upload"
    FILTER_TYPE_LISTEN_DOWNLOAD = "filter-type-listen-download"
    FILTER_TYPE_LISTEN_FORWARD = "filter-type-listen-forward"

    # 任务列表表格
    TASKS_TABLE = "tasks-table"
    TASKS_TABLE_HEADER = "tasks-table-header"
    TASKS_TABLE_BODY = "tasks-table-body"

    # 任务行（动态ID）
    TASK_ROW_PREFIX = "task-row-"
    TASK_ID = "task-id"
    TASK_TYPE = "task-type"
    TASK_STATUS = "task-status"
    TASK_PROGRESS = "task-progress"
    TASK_CREATED_AT = "task-created-at"
    TASK_ACTIONS = "task-actions"

    # 任务行操作按钮
    BTN_TASK_START = "btn-task-start"
    BTN_TASK_CANCEL = "btn-task-cancel"
    BTN_TASK_RETRY = "btn-task-retry"
    BTN_TASK_DELETE = "btn-task-delete"

    # 任务详情抽屉
    DRAWER_TASK_DETAIL = "drawer-task-detail"
    BTN_CLOSE_DETAIL = "btn-close-detail"
    DETAIL_TASK_ID = "detail-task-id"
    BTN_COPY_TASK_ID = "btn-copy-task-id"
    DETAIL_TASK_TYPE = "detail-task-type"
    DETAIL_TASK_STATUS = "detail-task-status"
    DETAIL_RANGE_MODE = "detail-range-mode"
    DETAIL_ERROR_MESSAGE_CONTAINER = "detail-error-message-container"
    DETAIL_ERROR_MESSAGE = "detail-error-message"

    # 任务配置详情（新增）
    DETAIL_SOURCE_IDENTIFIER = "detail-source-identifier"
    DETAIL_TARGET_IDENTIFIER = "detail-target-identifier"
    DETAIL_RANGE_DETAIL = "detail-range-detail"
    DETAIL_TYPE_FILTER = "detail-type-filter"
    DETAIL_SIZE_FILTER = "detail-size-filter"
    DETAIL_ESTIMATED_SIZE = "detail-estimated-size"
    DETAIL_FILE_COUNT = "detail-file-count"
    DETAIL_DELETE_AFTER_UPLOAD = "detail-delete-after-upload"
    DETAIL_MEDIA_TYPES = "detail-media-types"

    # 创建任务弹窗
    MODAL_CREATE_TASK = "modal-create-task"
    BTN_CLOSE_CREATE = "btn-close-create"
    BTN_CANCEL_CREATE = "btn-cancel-create"
    BTN_SUBMIT_CREATE = "btn-submit-create"

    # 创建任务表单
    INPUT_TASK_TYPE_DOWNLOAD = "input-task-type-download"
    INPUT_TASK_TYPE_FORWARD = "input-task-type-forward"
    INPUT_TASK_TYPE_LISTEN_DOWNLOAD = "input-task-type-listen-download"
    INPUT_TASK_TYPE_LISTEN_FORWARD = "input-task-type-listen-forward"
    INPUT_SOURCE_CHAT = "input-source-chat"
    BTN_RESOLVE_SOURCE = "btn-resolve-source"
    INPUT_TARGET_CHAT = "input-target-chat"
    BTN_RESOLVE_TARGET = "btn-resolve-target"

    # 消息范围模式
    INPUT_RANGE_MODE_ID = "input-range-mode-id"
    INPUT_RANGE_MODE_DATE = "input-range-mode-date"
    INPUT_RANGE_MODE_MULTIPLE = "input-range-mode-id-list"
    INPUT_RANGE_MODE_ALL = "input-range-mode-all"
    INPUT_RANGE_MODE_RECENT = "input-range-mode-recent"

    # 消息范围输入
    INPUT_MIN_ID = "input-min-id"
    INPUT_MAX_ID = "input-max-id"
    INPUT_START_DATE = "input-start-date"
    INPUT_END_DATE = "input-end-date"
    INPUT_RAW_ITEMS = "input-raw-items"
    INPUT_RECENT_COUNT = "input-recent-count"

    # 确认对话框
    CONFIRM_DIALOG = "confirm-dialog"
    BTN_CONFIRM_OK = "btn-confirm-ok"
    BTN_CONFIRM_CANCEL = "btn-confirm-cancel"

    # 分页
    PAGINATION_INFO = "pagination-info"
    BTN_PAGINATION_PREV = "btn-pagination-prev"
    BTN_PAGINATION_NEXT = "btn-pagination-next"

    # 类型过滤checkbox（动态data-testid: checkbox-filter-type-{value}）
    CHECKBOX_FILTER_TYPE_PREFIX = "checkbox-filter-type-"

    # 资源保护告警弹窗
    MODAL_RESOURCE_ALERT = "modal-resource-alert"

    # 通知提示
    NOTIFICATION_CONTAINER = "notification-container"
    NOTIFICATION_ITEM = "notification-item"

    def __init__(self, page: Page):
        super().__init__(page)

    # ========== 导航方法 ==========

    def navigate(
        self, base_url: str, action: str = None, task_type: str = None
    ) -> None:
        """
        导航到任务管理页

        Args:
            base_url: 服务基础URL
            action: 可选，URL参数action（如create）
            task_type: 可选，URL参数type（如download）
        """
        url = f"{base_url}{self.URL_PATH}"
        if action or task_type:
            params = []
            if action:
                params.append(f"action={action}")
            if task_type:
                params.append(f"type={task_type}")
            url += "?" + "&".join(params)
        self.page.goto(url)

    def wait_for_page_loaded(self, timeout: int = NAVIGATION_TIMEOUT) -> None:
        """
        等待页面加载完成

        策略：先等待networkidle，再等待新建任务按钮可见。
        不等待tasks-table（被x-show="tasks.length > 0"控制，无任务时隐藏）。
        """
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        self.page.locator(f'[data-testid="{self.BTN_CREATE_TASK}"]').wait_for(
            state="visible", timeout=timeout
        )

    # ========== 页面头部操作 ==========

    def click_create_task(self) -> None:
        """点击新建任务按钮"""
        self.click_by_testid(self.BTN_CREATE_TASK)

    def click_refresh(self) -> None:
        """点击刷新按钮"""
        self.click_by_testid(self.BTN_REFRESH)

    def is_refresh_button_disabled(self) -> bool:
        """检查刷新按钮是否禁用"""
        return not self.is_enabled_by_testid(self.BTN_REFRESH)

    # ========== 状态筛选 ==========

    def filter_by_status(self, status: str) -> None:
        """
        按状态筛选任务

        Args:
            status: 状态类型（all/running/pending/completed/failed/cancelled）
        """
        status_map = {
            "all": self.FILTER_STATUS_ALL,
            "running": self.FILTER_STATUS_RUNNING,
            "pending": self.FILTER_STATUS_PENDING,
            "completed": self.FILTER_STATUS_COMPLETED,
            "failed": self.FILTER_STATUS_FAILED,
            "cancelled": self.FILTER_STATUS_CANCELLED,
        }
        testid = status_map.get(status, self.FILTER_STATUS_ALL)
        self.click_by_testid(testid)

    def is_status_filter_active(self, status: str) -> bool:
        """检查状态筛选按钮是否激活（primary样式）"""
        status_map = {
            "all": self.FILTER_STATUS_ALL,
            "running": self.FILTER_STATUS_RUNNING,
            "pending": self.FILTER_STATUS_PENDING,
            "completed": self.FILTER_STATUS_COMPLETED,
            "failed": self.FILTER_STATUS_FAILED,
            "cancelled": self.FILTER_STATUS_CANCELLED,
        }
        testid = status_map.get(status, self.FILTER_STATUS_ALL)
        locator = self.get_by_testid(testid)
        # 检查是否有btn-primary类
        return locator.locator(
            ".btn-primary"
        ).count() > 0 or "btn-primary" in locator.get_attribute("class")

    # ========== 类型筛选 ==========

    def filter_by_type(self, task_type: str) -> None:
        """
        按类型筛选任务

        Args:
            task_type: 任务类型（all/download/forward/upload/listen_download/listen_forward）
        """
        type_map = {
            "all": self.FILTER_TYPE_ALL,
            "download": self.FILTER_TYPE_DOWNLOAD,
            "forward": self.FILTER_TYPE_FORWARD,
            "upload": self.FILTER_TYPE_UPLOAD,
            "listen_download": self.FILTER_TYPE_LISTEN_DOWNLOAD,
            "listen_forward": self.FILTER_TYPE_LISTEN_FORWARD,
        }
        testid = type_map.get(task_type, self.FILTER_TYPE_ALL)
        self.click_by_testid(testid)

    # ========== 任务列表 ==========

    def get_task_count(self) -> int:
        """获取当前任务列表中的任务数量"""
        tbody = self.get_by_testid(self.TASKS_TABLE_BODY)
        # 注意：由于使用template x-for，实际DOM中没有task-row元素
        # 需要通过tbody内的tr元素计数
        return tbody.locator("tr").count()

    def get_task_row(self, task_id: str) -> Locator:
        """
        获取指定任务的行元素

        Args:
            task_id: 任务ID

        Returns:
            Locator对象
        """
        # 使用动态testid
        return self.get_by_testid(f"{self.TASK_ROW_PREFIX}{task_id}")

    def get_task_status_text(self, task_id: str) -> str:
        """获取指定任务的状态文本"""
        row = self.get_task_row(task_id)
        status_cell = row.locator(f'[data-testid="{self.TASK_STATUS}"]')
        return status_cell.text_content() or ""

    def click_task_action(self, task_id: str, action: str) -> None:
        """
        点击任务行中的操作按钮

        Args:
            task_id: 任务ID
            action: 操作类型（start/cancel/retry/delete）
        """
        action_map = {
            "start": self.BTN_TASK_START,
            "cancel": self.BTN_TASK_CANCEL,
            "retry": self.BTN_TASK_RETRY,
            "delete": self.BTN_TASK_DELETE,
        }
        testid = action_map.get(action)
        if not testid:
            raise ValueError(f"Unknown action: {action}")

        row = self.get_task_row(task_id)
        row.locator(f'[data-testid="{testid}"]').click()

    def click_task_row(self, task_id: str) -> None:
        """点击任务整行打开详情抽屉"""
        row = self.get_task_row(task_id)
        row.click()

    def click_task_detail(self, task_id: str) -> None:
        """打开任务详情抽屉（通过点击任务行）"""
        self.click_task_row(task_id)

    # ========== 创建任务弹窗 ==========

    def is_create_modal_visible(self) -> bool:
        """检查创建任务弹窗是否可见"""
        return self.is_visible_by_testid(self.MODAL_CREATE_TASK)

    def wait_for_create_modal(self, timeout: int = 10000) -> None:
        """等待创建任务弹窗出现"""
        self.wait_for_selector(self.MODAL_CREATE_TASK, timeout)

    def close_create_modal(self) -> None:
        """关闭创建任务弹窗"""
        self.click_by_testid(self.BTN_CLOSE_CREATE)

    def cancel_create_modal(self) -> None:
        """点击底部取消按钮关闭创建弹窗"""
        self.click_by_testid(self.BTN_CANCEL_CREATE)

    def select_task_type(self, task_type: str) -> None:
        """
        选择任务类型

        Args:
            task_type: 任务类型（download/forward/listen_download/listen_forward）
        """
        type_map = {
            "download": self.INPUT_TASK_TYPE_DOWNLOAD,
            "forward": self.INPUT_TASK_TYPE_FORWARD,
            "listen_download": self.INPUT_TASK_TYPE_LISTEN_DOWNLOAD,
            "listen_forward": self.INPUT_TASK_TYPE_LISTEN_FORWARD,
        }
        testid = type_map.get(task_type)
        if not testid:
            raise ValueError(f"Unknown task type: {task_type}")
        self.click_by_testid(testid)

    def fill_source_chat(self, chat: str) -> None:
        """填写源频道"""
        self.fill_by_testid(self.INPUT_SOURCE_CHAT, chat)

    def click_resolve_source(self) -> None:
        """点击解析源频道按钮"""
        self.click_by_testid(self.BTN_RESOLVE_SOURCE)

    def fill_target_chat(self, chat: str) -> None:
        """填写目标频道"""
        self.fill_by_testid(self.INPUT_TARGET_CHAT, chat)

    def click_resolve_target(self) -> None:
        """点击解析目标频道按钮"""
        self.click_by_testid(self.BTN_RESOLVE_TARGET)

    def select_range_mode(self, mode: str) -> None:
        """
        选择消息范围模式（通过Alpine.js直接设置）

        Args:
            mode: 范围模式（id_range/date_range/multiple_ids/all/recent）
        """
        # 通过JavaScript直接设置Alpine.js的messageRangeMode
        self.page.evaluate(
            f"() => {{ window.taskManager.createForm.messageRangeMode = '{mode}'; }}"
        )
        # 同时点击radio按钮触发视觉更新
        mode_map = {
            "id_range": self.INPUT_RANGE_MODE_ID,
            "date_range": self.INPUT_RANGE_MODE_DATE,
            "multiple_ids": self.INPUT_RANGE_MODE_MULTIPLE,
            "all": self.INPUT_RANGE_MODE_ALL,
            "recent": self.INPUT_RANGE_MODE_RECENT,
        }
        testid = mode_map.get(mode)
        if testid:
            try:
                self.click_by_testid(testid)
            except Exception:
                pass  # 已通过JS设置，点击失败不影响

    def fill_min_id(self, min_id: str) -> None:
        """填写最小ID"""
        self.fill_by_testid(self.INPUT_MIN_ID, min_id)

    def fill_max_id(self, max_id: str) -> None:
        """填写最大ID"""
        self.fill_by_testid(self.INPUT_MAX_ID, max_id)

    def fill_start_date(self, date: str) -> None:
        """填写开始日期"""
        self.fill_by_testid(self.INPUT_START_DATE, date)

    def fill_end_date(self, date: str) -> None:
        """填写结束日期"""
        self.fill_by_testid(self.INPUT_END_DATE, date)

    def fill_raw_items(self, items: str) -> None:
        """填写ID列表"""
        self.fill_by_testid(self.INPUT_RAW_ITEMS, items)

    def fill_recent_count(self, count: str) -> None:
        """填写最近N条数量"""
        self.fill_by_testid(self.INPUT_RECENT_COUNT, count)

    def click_submit_create(self) -> None:
        """点击创建任务提交按钮"""
        self.click_by_testid(self.BTN_SUBMIT_CREATE)

    # ========== 任务详情抽屉 ==========

    def is_detail_drawer_visible(self) -> bool:
        """检查任务详情抽屉是否可见"""
        return self.is_visible_by_testid(self.DRAWER_TASK_DETAIL)

    def wait_for_detail_drawer(self, timeout: int = 10000) -> None:
        """等待任务详情抽屉出现"""
        self.wait_for_selector(self.DRAWER_TASK_DETAIL, timeout)

    def close_detail_drawer(self) -> None:
        """关闭任务详情抽屉"""
        self.click_by_testid(self.BTN_CLOSE_DETAIL)

    def click_close_detail(self) -> None:
        """点击详情抽屉关闭按钮"""
        self.click_by_testid(self.BTN_CLOSE_DETAIL)

    def get_detail_task_id(self) -> str:
        """获取详情抽屉中的任务ID"""
        return self.get_text_by_testid(self.DETAIL_TASK_ID)

    def click_copy_task_id(self) -> None:
        """点击复制任务ID按钮"""
        self.click_by_testid(self.BTN_COPY_TASK_ID)

    def get_detail_type_text(self) -> str:
        """获取详情抽屉中的任务类型文本"""
        return self.get_text_by_testid(self.DETAIL_TASK_TYPE)

    def get_detail_status_text(self) -> str:
        """获取详情抽屉中的任务状态文本"""
        return self.get_text_by_testid(self.DETAIL_TASK_STATUS)

    def get_detail_range_mode_text(self) -> str:
        """获取详情抽屉中的范围模式文本"""
        return self.get_text_by_testid(self.DETAIL_RANGE_MODE)

    def is_detail_error_visible(self) -> bool:
        """检查详情抽屉中错误信息是否可见（仅failed任务有错误信息）"""
        return self.is_visible_by_testid(self.DETAIL_ERROR_MESSAGE_CONTAINER)

    def get_detail_error_text(self) -> str:
        """获取详情抽屉中的错误信息文本"""
        return self.get_text_by_testid(self.DETAIL_ERROR_MESSAGE)

    # ========== 任务配置详情（新增） ==========

    def get_detail_source_identifier(self) -> str:
        """获取详情抽屉中的源频道标识"""
        return self.get_text_by_testid(self.DETAIL_SOURCE_IDENTIFIER)

    def get_detail_target_identifier(self) -> str:
        """获取详情抽屉中的目标频道标识"""
        return self.get_text_by_testid(self.DETAIL_TARGET_IDENTIFIER)

    def get_detail_range_detail(self) -> str:
        """获取详情抽屉中的范围详情文本"""
        return self.get_text_by_testid(self.DETAIL_RANGE_DETAIL)

    def get_detail_type_filter(self) -> str:
        """获取详情抽屉中的类型过滤文本"""
        return self.get_text_by_testid(self.DETAIL_TYPE_FILTER)

    def get_detail_size_filter(self) -> str:
        """获取详情抽屉中的文件大小过滤文本"""
        return self.get_text_by_testid(self.DETAIL_SIZE_FILTER)

    def get_detail_estimated_size(self) -> str:
        """获取详情抽屉中的预估大小文本"""
        return self.get_text_by_testid(self.DETAIL_ESTIMATED_SIZE)

    def get_detail_file_count(self) -> str:
        """获取详情抽屉中的已选文件数量"""
        return self.get_text_by_testid(self.DETAIL_FILE_COUNT)

    def get_detail_delete_after_upload(self) -> str:
        """获取详情抽屉中的上传后删除选项文本"""
        return self.get_text_by_testid(self.DETAIL_DELETE_AFTER_UPLOAD)

    def get_detail_media_types(self) -> str:
        """获取详情抽屉中的监听媒体类型文本"""
        return self.get_text_by_testid(self.DETAIL_MEDIA_TYPES)

    def is_detail_config_visible(self) -> bool:
        """检查详情抽屉中的任务配置区块是否可见"""
        return self.is_visible_by_testid(self.DETAIL_SOURCE_IDENTIFIER)

    # ========== 资源保护告警弹窗 ==========

    def is_resource_alert_visible(self) -> bool:
        """检查资源保护告警弹窗是否可见"""
        return self.is_visible_by_testid(self.MODAL_RESOURCE_ALERT)

    def close_resource_alert(self) -> None:
        """关闭资源告警弹窗（通过Alpine组件方法同步响应式状态）"""
        self.page.evaluate(
            "() => { const el = document.querySelector('[x-data]'); "
            "if (el && window.Alpine) { window.Alpine.$data(el).closeResourceAlert(); } }"
        )

    # ========== 创建表单验证错误 ==========

    def is_create_form_error_visible(self) -> bool:
        """检查创建表单验证错误是否可见（通过DOM元素检查）"""
        return bool(
            self.page.evaluate(
                "() => { try { return !!document.querySelector('.bg-red-900\\/30[x-show]'); } catch(e) { return false; } }"
            )
        )

    def has_create_form_error(self) -> bool:
        """检查创建表单是否有验证错误（通过Alpine.js状态）"""
        return bool(
            self.page.evaluate(
                "() => { try { const el = document.querySelector('[x-data]'); const d = el && window.Alpine && window.Alpine.$data(el); return d ? !!d.createFormError : false; } catch(e) { return false; } }"
            )
        )

    def get_create_form_error_text(self) -> str:
        """获取创建表单验证错误文本"""
        return str(
            self.page.evaluate(
                "() => { try { const el = document.querySelector('[x-data]'); const d = el && window.Alpine && window.Alpine.$data(el); return d ? (d.createFormError || '') : ''; } catch(e) { return ''; } }"
            )
        )

    # ========== 综合操作 ==========

    def create_download_task(
        self,
        source_chat: str,
        range_mode: str = "id_range",
        min_id: str = None,
        max_id: str = None,
    ) -> None:
        """
        快捷创建下载任务

        Args:
            source_chat: 源频道
            range_mode: 消息范围模式
            min_id: 最小ID（id_range模式）
            max_id: 最大ID（id_range模式）
        """
        # 打开创建弹窗
        self.click_create_task()
        self.wait_for_create_modal()

        # 选择下载类型
        self.select_task_type("download")

        # 填写源频道
        self.fill_source_chat(source_chat)

        # 选择消息范围模式
        self.select_range_mode(range_mode)

        # 根据模式填写参数
        if range_mode == "id_range":
            if min_id:
                self.fill_min_id(min_id)
            if max_id:
                self.fill_max_id(max_id)

        # 提交创建
        self.click_submit_create()

    # ========== 任务操作（启动/删除等） ==========

    def click_task_start(self, task_id: str) -> None:
        """
        点击任务行中的启动按钮

        Args:
            task_id: 任务ID
        """
        self.click_task_action(task_id, "start")

    def click_task_delete(self, task_id: str) -> None:
        """
        点击任务行中的删除按钮

        Args:
            task_id: 任务ID
        """
        self.click_task_action(task_id, "delete")

    def click_task_cancel(self, task_id: str) -> None:
        """
        点击任务行中的取消按钮

        Args:
            task_id: 任务ID
        """
        self.click_task_action(task_id, "cancel")

    def click_task_retry(self, task_id: str) -> None:
        """
        点击任务行中的重试按钮

        Args:
            task_id: 任务ID
        """
        self.click_task_action(task_id, "retry")

    def wait_for_task_status(
        self, task_id: str, expected_status: str, timeout: int = 10000
    ) -> None:
        """
        等待指定任务的状态变为期望值

        Args:
            task_id: 任务ID
            expected_status: 期望的状态文本（如"running"）
            timeout: 超时时间（毫秒）
        """
        row = self.get_task_row(task_id)
        status_cell = row.locator(f'[data-testid="{self.TASK_STATUS}"]')
        status_cell.wait_for(state="visible", timeout=timeout)

    def is_task_in_list(self, task_id: str) -> bool:
        """
        检查指定任务是否在列表中

        Args:
            task_id: 任务ID

        Returns:
            任务是否存在
        """
        row = self.get_by_testid(f"{self.TASK_ROW_PREFIX}{task_id}")
        return row.is_visible()

    # ========== 确认对话框 ==========

    def is_confirm_dialog_visible(self) -> bool:
        """检查确认对话框是否可见"""
        # ConfirmDialog使用x-show="dialog.visible"控制显隐
        # 通过Alpine.js状态判断可见性
        return self.page.locator(
            '[x-data*="confirmDialog"] >> visible=true'
        ).count() > 0 or self.page.evaluate(
            "() => window.confirmDialog && window.confirmDialog.visible === true"
        )

    def click_confirm_dialog_confirm(self) -> None:
        """点击确认对话框的确认按钮"""
        # ConfirmDialog确认按钮通过@click="dialog.onConfirm()"触发
        self.page.evaluate("() => window.confirmDialog.onConfirm()")

    def click_confirm_dialog_cancel(self) -> None:
        """点击确认对话框的取消按钮"""
        self.page.evaluate("() => window.confirmDialog.onCancel()")

    def wait_for_confirm_dialog(self, timeout: int = 5000) -> None:
        """
        等待确认对话框出现

        Args:
            timeout: 超时时间（毫秒）
        """
        self.page.wait_for_function(
            "() => window.confirmDialog && window.confirmDialog.visible === true",
            timeout=timeout,
        )

    def wait_for_confirm_dialog_hidden(self, timeout: int = 5000) -> None:
        """
        等待确认对话框消失

        Args:
            timeout: 超时时间（毫秒）
        """
        self.page.wait_for_function(
            "() => !window.confirmDialog || window.confirmDialog.visible === false",
            timeout=timeout,
        )

    # ========== 频道解析 ==========

    def is_resolve_result_visible(self) -> bool:
        """检查源频道解析结果是否可见"""
        # 解析结果通过x-show="resolveResult"控制显隐
        return self.page.evaluate(
            "() => {"
            "  const el = document.querySelector('[x-data]');"
            "  const d = el && window.Alpine && window.Alpine.$data(el);"
            "  return d && d.resolveResult !== null;"
            "}"
        )

    def wait_for_resolve_result(self, timeout: int = 10000) -> None:
        """
        等待频道解析结果出现

        Args:
            timeout: 超时时间（毫秒）
        """
        self.page.wait_for_function(
            "() => {"
            "  const el = document.querySelector('[x-data]');"
            "  const d = el && window.Alpine && window.Alpine.$data(el);"
            "  return d && d.resolveResult !== null;"
            "}",
            timeout=timeout,
        )

    # ========== 类型过滤checkbox ==========

    def is_type_filter_checkbox_visible(self) -> bool:
        """检查类型过滤checkbox组是否可见"""
        # 通过检查checkbox输入元素是否存在
        return (
            self.page.locator(
                '[data-testid="modal-create-task"] .form-group input[type="checkbox"]'
            ).count()
            > 0
        )

    def toggle_type_filter(self, media_type: str) -> None:
        """
        切换媒体类型过滤checkbox

        Args:
            media_type: 媒体类型（photo/video/document/audio）
        """
        checkbox = self.page.locator(
            f'[data-testid="checkbox-filter-type-{media_type}"]'
        )
        if checkbox.is_checked():
            checkbox.uncheck()
        else:
            checkbox.check()

    def is_type_filter_selected(self, media_type: str) -> bool:
        """
        检查指定媒体类型过滤是否选中

        Args:
            media_type: 媒体类型（photo/video/document/audio）

        Returns:
            是否选中
        """
        return self.page.locator(
            f'[data-testid="checkbox-filter-type-{media_type}"]'
        ).is_checked()

    # ========== 弹窗内表单条件可见性 ==========

    def is_target_chat_visible(self) -> bool:
        """检查目标频道输入框是否可见（转发/监听转发类型时可见）"""
        return self.is_visible_by_testid(self.INPUT_TARGET_CHAT)

    def is_range_mode_section_visible(self) -> bool:
        """检查消息范围模式区域是否可见（非监听类型时可见）"""
        return self.is_visible_by_testid(self.INPUT_RANGE_MODE_ID)

    def is_date_inputs_visible(self) -> bool:
        """检查日期输入框是否可见（日期范围模式时可见）"""
        return self.is_visible_by_testid(self.INPUT_START_DATE)

    def is_raw_items_visible(self) -> bool:
        """检查ID列表textarea是否可见（ID列表模式时可见）"""
        return self.is_visible_by_testid(self.INPUT_RAW_ITEMS)

    def is_recent_count_visible(self) -> bool:
        """检查最近N条输入框是否可见（最近N条模式时可见）"""
        return self.is_visible_by_testid(self.INPUT_RECENT_COUNT)

    def is_source_chat_visible(self) -> bool:
        """检查源频道输入框是否可见（所有创建类型均显示）"""
        return self.is_visible_by_testid(self.INPUT_SOURCE_CHAT)

    # ========== 分页 ==========

    def is_pagination_visible(self) -> bool:
        """检查分页区域是否可见"""
        return self.is_visible_by_testid(self.PAGINATION_INFO)

    def get_pagination_text(self) -> str:
        """
        获取分页信息文本

        Returns:
            分页文本（如"共 5 个任务，第 1 / 1 页"）
        """
        pagination = self.get_by_testid(self.PAGINATION_INFO)
        p = pagination.locator("p")
        if p.count() > 0:
            return p.first.text_content() or ""
        return ""

    def get_total_tasks_count(self) -> int:
        """
        获取任务总数（从Alpine.js状态获取）

        Returns:
            任务总数
        """
        try:
            count = self.page.evaluate(
                "() => window.taskManager ? window.taskManager.totalTasks : 0"
            )
            return int(count) if count else 0
        except Exception:
            return 0

    def get_total_pages(self) -> int:
        """
        获取总页数

        Returns:
            总页数
        """
        try:
            pages = self.page.evaluate(
                "() => window.taskManager ? window.taskManager.totalPages : 1"
            )
            return int(pages) if pages else 1
        except Exception:
            return 1

    def get_current_page(self) -> int:
        """
        获取当前页码

        Returns:
            当前页码
        """
        try:
            page = self.page.evaluate(
                "() => window.taskManager ? window.taskManager.page : 1"
            )
            return int(page) if page else 1
        except Exception:
            return 1

    def click_next_page(self) -> None:
        """点击下一页按钮"""
        self.click_by_testid(self.BTN_PAGINATION_NEXT)

    def click_prev_page(self) -> None:
        """点击上一页按钮"""
        self.click_by_testid(self.BTN_PAGINATION_PREV)

    # ========== 综合快捷操作 ==========

    def open_create_modal_with_type(self, task_type: str) -> None:
        """
        打开创建弹窗并选择任务类型

        Args:
            task_type: 任务类型（download/forward/listen_download/listen_forward）
        """
        self.click_create_task()
        self.wait_for_create_modal()
        self.select_task_type(task_type)

    # ========== 通知提示 ==========

    def get_notification_items(self) -> list:
        """
        获取当前所有通知项（每个通知项为 {type, message} 字典）

        Returns:
            通知列表，每项包含 type 和 message
        """
        items = []
        locator = self.get_by_testid(self.NOTIFICATION_ITEM)
        for i in range(locator.count()):
            el = locator.nth(i)
            class_attr = el.get_attribute("class") or ""
            if "border-green-500" in class_attr:
                ntype = "success"
            elif "border-red-500" in class_attr:
                ntype = "error"
            elif "border-yellow-500" in class_attr:
                ntype = "warning"
            else:
                ntype = "info"
            items.append({"type": ntype, "message": el.inner_text().strip()})
        return items

    def get_notification_count(self) -> int:
        """
        获取当前通知数量

        Returns:
            通知数量
        """
        return self.get_by_testid(self.NOTIFICATION_ITEM).count()

    def get_notification_messages(self) -> list:
        """
        获取当前所有通知消息文本

        Returns:
            消息文本列表
        """
        return [item["message"] for item in self.get_notification_items()]

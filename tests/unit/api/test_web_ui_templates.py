# coding=UTF-8
"""Web UI 静态模板逻辑测试。

直接解析 module/web/*.html，验证 Alpine.js 状态驱动逻辑与预期一致，
无需启动浏览器或后端服务。
"""

import re
from pathlib import Path

import pytest


def _load_web_file(name: str) -> str:
    """加载 module/web 下的静态文件文本。"""
    path = Path(__file__).parent.parent.parent.parent / "module" / "web" / name
    assert path.exists(), f"{name} 不存在: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def tasks_html_text() -> str:
    """加载任务页面 HTML 文本。"""
    return _load_web_file("tasks.html")


@pytest.fixture
def files_html_text() -> str:
    """加载文件管理页面 HTML 文本。"""
    return _load_web_file("files.html")


@pytest.fixture
def api_js_text() -> str:
    """加载前端 API 封装脚本文本。"""
    return _load_web_file("js/api.js")


@pytest.fixture
def tasks_js_text() -> str:
    """加载任务管理脚本文本。"""
    return _load_web_file("js/tasks.js")


def _get_button_xshow(html_text: str, testid: str) -> str:
    """根据 data-testid 获取按钮的 x-show 属性值（与属性顺序、引号内 > 无关）。"""
    # 引号包裹的属性值可含 >，故按 token 匹配：非引号字符或完整引号串
    attr_or_text = r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*'
    tag_pattern = re.compile(
        rf'<button\b{attr_or_text}\bdata-testid="{re.escape(testid)}"{attr_or_text}>',
        re.DOTALL | re.IGNORECASE,
    )
    match = tag_pattern.search(html_text)
    if match is None:
        raise AssertionError(f"找不到按钮 {testid}")
    tag = match.group(0)
    xshow_match = re.search(r'\bx-show="([^"]*)"', tag)
    if xshow_match is None:
        raise AssertionError(f"找不到按钮 {testid} 的 x-show 属性")
    return xshow_match.group(1).strip()


def _find_elem(html_text: str, testid: str) -> str:
    """校验 HTML 中存在 data-testid 元素，并返回其所在片段。"""
    pattern = re.compile(
        rf'\bdata-testid="{re.escape(testid)}"',
    )
    match = pattern.search(html_text)
    if match is None:
        raise AssertionError(f"找不到 data-testid={testid}")
    start = max(0, match.start() - 200)
    return html_text[start : match.start() + 200]


class TestTasksPageActions:
    """任务列表操作按钮显隐逻辑测试。"""

    def test_start_button_only_for_pending(self, tasks_html_text: str):
        """启动按钮仅对 pending 状态显示，不应包含 failed。"""
        xshow = _get_button_xshow(tasks_html_text, "btn-task-start")
        assert "task.status === 'pending'" in xshow
        assert "task.status === 'failed'" not in xshow, (
            "失败任务不应显示启动按钮，请使用重试按钮"
        )

    def test_retry_button_for_failed(self, tasks_html_text: str):
        """重试按钮对 failed 状态显示。"""
        xshow = _get_button_xshow(tasks_html_text, "btn-task-retry")
        assert "task.status === 'failed'" in xshow

    def test_delete_button_includes_failed(self, tasks_html_text: str):
        """删除按钮对 failed 状态可用。"""
        xshow = _get_button_xshow(tasks_html_text, "btn-task-delete")
        assert "task.status === 'failed'" in xshow


class TestFilesPageDelete:
    """文件管理页手动批量删除模板断言。"""

    def test_delete_selected_button(self, files_html_text: str):
        """删除选中按钮存在，且仅在勾选文件后显示。"""
        xshow = _get_button_xshow(files_html_text, "delete-selected-btn")
        assert "selectedFiles.length > 0" in xshow

    def test_delete_modal_exists(self, files_html_text: str):
        """删除确认弹窗存在。"""
        _find_elem(files_html_text, "delete-modal")

    def test_delete_modal_buttons(self, files_html_text: str):
        """确认/取消按钮存在，确认按钮执行删除。"""
        _find_elem(files_html_text, "delete-confirm-btn")
        _find_elem(files_html_text, "delete-cancel-btn")

    def test_api_delete_files(self, api_js_text: str):
        """api.js 提供 deleteFiles 方法并调用 /api/files/batch。"""
        assert "async deleteFiles(filePaths)" in api_js_text
        assert "DELETE" in api_js_text
        assert "/api/files/batch" in api_js_text

    def test_api_delete_files_in_files_section(self, api_js_text: str):
        """deleteFiles 位于文件相关 API 区段（类方法，无尾逗号语法错误）。"""
        files_section = api_js_text.split("文件相关 API")[1]
        assert "async deleteFiles(filePaths)" in files_section
        assert ",," not in api_js_text


class TestTasksPageCleanup:
    """任务管理页定时清理（cleanup_files）模板断言。"""

    def test_cleanup_type_filter_button(self, tasks_html_text: str):
        """类型筛选包含「定时清理」，data-testid=filter-type-cleanup。"""
        _find_elem(tasks_html_text, "filter-type-cleanup")
        assert "cleanup_files" in tasks_html_text

    def test_cleanup_type_radio(self, tasks_html_text: str):
        """新建任务类型 radio 支持 cleanup_files。"""
        _find_elem(tasks_html_text, "input-task-type-cleanup")
        assert 'value="cleanup_files"' in tasks_html_text

    def test_cleanup_keep_days_form(self, tasks_html_text: str):
        """保留天数预设与自定义输入存在。"""
        _find_elem(tasks_html_text, "input-keep-days-preset-1")
        _find_elem(tasks_html_text, "input-keep-days-preset-3")
        _find_elem(tasks_html_text, "input-keep-days-preset-7")
        _find_elem(tasks_html_text, "input-keep-days-preset-30")
        _find_elem(tasks_html_text, "input-keep-days-preset-custom")
        _find_elem(tasks_html_text, "input-keep-days-custom")

    def test_cleanup_schedule_form(self, tasks_html_text: str):
        """调度模式 radio 与时刻/间隔输入存在。"""
        _find_elem(tasks_html_text, "input-schedule-mode-daily")
        _find_elem(tasks_html_text, "input-schedule-mode-interval")
        _find_elem(tasks_html_text, "input-schedule-time")
        _find_elem(tasks_html_text, "input-schedule-interval-hours")

    def test_cleanup_remove_empty_dirs(self, tasks_html_text: str):
        """空目录清理开关存在。"""
        _find_elem(tasks_html_text, "input-remove-empty-dirs")

    def test_cleanup_detail_drawer(self, tasks_html_text: str):
        """详情抽屉存在保留天数 / 调度 / 下次执行 / 统计区域与操作按钮。"""
        _find_elem(tasks_html_text, "detail-cleanup-keep-days")
        _find_elem(tasks_html_text, "detail-cleanup-schedule")
        _find_elem(tasks_html_text, "detail-cleanup-next-run")
        _find_elem(tasks_html_text, "detail-cleanup-last-run-stats")
        _find_elem(tasks_html_text, "btn-run-cleanup")
        _find_elem(tasks_html_text, "btn-pause-cleanup")
        _find_elem(tasks_html_text, "btn-resume-cleanup")

    def test_cleanup_form_hidden_sections(self, tasks_html_text: str):
        """cleanup 类型下源频道/消息范围/类型过滤与大小过滤均应隐藏。"""
        # 源频道、类型过滤分组需排除 cleanup_files
        assert re.search(
            r'class="form-group"[^>]*x-show="createForm\.taskType !== \'cleanup_files\'"',
            tasks_html_text,
        ), "源频道分组未对 cleanup_files 隐藏"
        assert re.search(
            r'x-show="createForm\.taskType !== \'cleanup_files\'"[^>]*>\s*<label class="form-label">类型过滤',
            tasks_html_text,
        ) or re.search(
            r'class="form-group"[^>]*x-show="createForm\.taskType !== \'cleanup_files\'"',
            tasks_html_text,
        )

    def test_cleanup_url_params_whitelist(self, tasks_html_text: str):
        """URL 参数类型白名单包含 cleanup_files。"""
        assert re.search(
            r"\['download', 'forward', 'listen_download', 'listen_forward', 'cleanup_files'\]",
            tasks_html_text,
        )

    def test_api_task_actions(self, api_js_text: str):
        """api.js 提供 runTask/pauseTask/resumeTask。"""
        assert "async runTask(taskId)" in api_js_text
        assert "async pauseTask(taskId)" in api_js_text
        assert "async resumeTask(taskId)" in api_js_text
        assert "/tasks/${taskId}/run" in api_js_text
        assert "/tasks/${taskId}/pause" in api_js_text
        assert "/tasks/${taskId}/resume" in api_js_text

    def test_tasks_js_type_text(self, tasks_js_text: str):
        """tasks.js getTypeText 映射 cleanup_files 为定时清理。"""
        assert 'cleanup_files: "🧹 定时清理"' in tasks_js_text

    def test_tasks_js_create_payload_cleanup(self, tasks_js_text: str):
        """tasks.js 构建 create payload 含 cleanup_files 分支（keep_days/schedule）。"""
        assert 'this.createForm.taskType === "cleanup_files"' in tasks_js_text
        assert "params.keep_days" in tasks_js_text
        assert "params.schedule" in tasks_js_text
        assert "params.remove_empty_dirs" in tasks_js_text

    def test_tasks_js_validate_cleanup(self, tasks_js_text: str):
        """tasks.js 创建校验含 cleanup 分支（保留天数 1~365 与时刻格式）。"""
        assert 'form.taskType === "cleanup_files"' in tasks_js_text
        assert "保留天数需为 1~365 的整数" in tasks_js_text
        assert "时刻格式不正确" in tasks_js_text


@pytest.fixture
def dashboard_html_text() -> str:
    """加载仪表盘页面 HTML 文本。"""
    return _load_web_file("index.html")


class TestFilesLongFileName:
    """H1: 文件页超长文件名单行截断 + Tooltip 模板断言。"""

    def test_file_name_span_truncate_and_tooltip(self, files_html_text: str):
        """文件（非目录，span 渲染）名称单行截断，并 hover 显示完整文件名。"""
        # 目录用 <a> 渲染，文件用 <span> 渲染；截断只应用于文件 span
        assert re.search(
            r'<span[^>]*data-testid="file-name"[^>]*text-truncate',
            files_html_text,
        ), "文件名称 span 未使用 text-truncate 单行截断"
        assert re.search(
            r'<span[^>]*data-testid="file-name"[^>]*:data-tooltip="file\.name"',
            files_html_text,
        ), "文件名称 span 未绑定完整文件名 Tooltip"
        assert re.search(
            r'<span[^>]*data-testid="file-name"[^>]*tooltip',
            files_html_text,
        ), "文件名称 span 未使用 tooltip 类"


class TestTaskBatchOperations:
    """H2: 任务管理页批量操作模板断言。"""

    def test_select_all_checkbox_exists(self, tasks_html_text: str):
        """表头全选复选框存在。"""
        _find_elem(tasks_html_text, "select-all-tasks")

    def test_task_row_checkbox_exists(self, tasks_html_text: str):
        """任务行复选框存在。"""
        _find_elem(tasks_html_text, "task-checkbox")

    def test_batch_cancel_button_visible_when_selected(self, tasks_html_text: str):
        """批量取消按钮仅在勾选任务后显示。"""
        xshow = _get_button_xshow(tasks_html_text, "btn-batch-cancel")
        assert "selectedTasks.length > 0" in xshow

    def test_batch_delete_button_visible_when_selected(self, tasks_html_text: str):
        """批量删除按钮仅在勾选任务后显示。"""
        xshow = _get_button_xshow(tasks_html_text, "btn-batch-delete")
        assert "selectedTasks.length > 0" in xshow

    def test_batch_selected_count(self, tasks_html_text: str):
        """批量操作栏展示已选任务数量。"""
        _find_elem(tasks_html_text, "batch-selected-count")

    def test_tasks_js_batch_state(self, tasks_js_text: str):
        """tasks.js 提供批量选择与批量操作逻辑。"""
        assert "selectedTasks" in tasks_js_text
        assert "toggleTaskSelection" in tasks_js_text
        assert "selectAllTasks" in tasks_js_text
        assert "batchCancelTasks" in tasks_js_text
        assert "batchDeleteTasks" in tasks_js_text


class TestEmptyStates:
    """H3: 空状态增强模板断言。"""

    def test_tasks_empty_state_cta(self, tasks_html_text: str):
        """任务页无任务空态含「新建任务」CTA。"""
        _find_elem(tasks_html_text, "empty-tasks-cta")

    def test_tasks_empty_filter_result(self, tasks_html_text: str):
        """任务页筛选无结果时展示提示与清除筛选按钮。"""
        _find_elem(tasks_html_text, "empty-filter-result")
        _find_elem(tasks_html_text, "btn-clear-filter")

    def test_files_empty_state_cta(self, files_html_text: str):
        """文件页空目录空态含 CTA。"""
        _find_elem(files_html_text, "empty-files-cta")

    def test_dashboard_empty_recent_tasks(self, dashboard_html_text: str):
        """Dashboard 最近任务为空时展示提示。"""
        _find_elem(dashboard_html_text, "empty-recent-tasks")


@pytest.fixture(scope="module")
def web_html_files():
    """加载 module/web 下所有 HTML 页面文本。"""
    files = {}
    web_dir = Path(__file__).parent.parent.parent.parent / "module" / "web"
    for path in sorted(web_dir.glob("*.html")):
        files[path.name] = path.read_text(encoding="utf-8")
    assert files, "module/web 下未找到任何 HTML 文件"
    return files


@pytest.fixture(scope="module")
def style_css_text() -> str:
    """加载自定义样式文本。"""
    path = (
        Path(__file__).parent.parent.parent.parent
        / "module"
        / "web"
        / "css"
        / "style.css"
    )
    assert path.exists(), f"style.css 不存在: {path}"
    return path.read_text(encoding="utf-8")


class TestOverlayFOUC:
    """弹框/遮罩层 FOUC 防护契约（生产环境弹框闪烁修复）。

    所有 `fixed` 定位且由 `x-show` 控制显隐的覆盖层元素（弹框、抽屉、
    遮罩、底部悬浮栏等），在 Alpine.js 初始化前均默认可见；若生产环境
    静态资源加载慢于浏览器首帧渲染，会出现弹框闪现闪烁。
    契约：此类元素必须携带 `x-cloak`，且 style.css 提供
    `[x-cloak] { display: none !important }` 规则，由 Alpine 初始化
    完成后自动移除 x-cloak、x-show 无缝接管。
    """

    @staticmethod
    def _iter_fixed_xshow_tags(html_text: str):
        """遍历所有 fixed 定位且由 x-show 控制（非常显）的元素开始标签。"""
        attr_or_text = r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*'
        tag_pattern = re.compile(rf"<div\b{attr_or_text}>", re.DOTALL | re.IGNORECASE)
        for match in tag_pattern.finditer(html_text):
            tag = match.group(0)
            xshow_match = re.search(r'\bx-show="([^"]*)"', tag)
            if xshow_match is None:
                continue
            if xshow_match.group(1).strip() == "true":
                continue  # 常显元素无需防闪烁
            if re.search(r"\bfixed\b", tag):
                yield tag

    def test_fixed_xshow_overlays_have_cloak(self, web_html_files):
        """fixed 定位的 x-show 覆盖层必须带 x-cloak，防止 Alpine 初始化前闪现。"""
        for filename, text in web_html_files.items():
            for tag in self._iter_fixed_xshow_tags(text):
                assert "x-cloak" in tag, (
                    f"{filename} 存在未加 x-cloak 的固定定位覆盖层元素（生产环境会闪烁）:\n"
                    f"{tag[:300]}"
                )

    def test_style_css_defines_cloak_rule(self, style_css_text):
        """style.css 必须提供 [x-cloak] 隐藏规则，否则 x-cloak 属性不生效。"""
        assert re.search(
            r"\[x-cloak\][^{]*\{[^}]*display\s*:\s*none\s*!important",
            style_css_text,
        ), "style.css 缺少 [x-cloak] { display: none !important; } 规则"

# coding=UTF-8
"""Dashboard 资源统计契约测试（TDD）。

背景：生产环境 Dashboard 的磁盘/内存/CPU/运行任务全部显示为 0，
根因是前端 `loadStats` 读取 `/api/monitor/resource/status` 的平铺字段
（`disk_total`/`cpu_percent`/`running_tasks`...），而该接口实际返回的是
`disk.total_gb`/`current_running_tasks` 等另一套结构，且不含 CPU/内存实测值，
所有字段映射到 undefined 后兜底为 0。

契约：Dashboard 四个资源卡片的数据必须来自 `GET /api/monitor/stats`，
并按本契约钉住前后端字段名。本测试为静态契约测试（读取源码文本），
不启动应用、不导入 module 包，避免依赖 asyncio/psutil 等运行时环境。
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
WEB_DIR = PROJECT_ROOT / "module" / "web"
MONITOR_PY = PROJECT_ROOT / "module" / "core" / "monitor.py"
MONITOR_ROUTE_PY = PROJECT_ROOT / "module" / "api" / "routes" / "monitor.py"


def _load_web_file(name: str) -> str:
    """加载 module/web 下的静态文件文本。"""
    path = WEB_DIR / name
    assert path.exists(), f"{name} 不存在: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api_js_text() -> str:
    return _load_web_file("js/api.js")


@pytest.fixture(scope="module")
def dashboard_html_text() -> str:
    return _load_web_file("index.html")


@pytest.fixture(scope="module")
def monitor_py_text() -> str:
    path = MONITOR_PY
    assert path.exists(), f"monitor.py 不存在: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def monitor_route_text() -> str:
    path = MONITOR_ROUTE_PY
    assert path.exists(), f"routes/monitor.py 不存在: {path}"
    return path.read_text(encoding="utf-8")


#: `/api/monitor/stats` 契约字段（与 get_system_stats/get_task_stats 构建结构一致）
SYSTEM_FIELDS = ["cpu_percent"]
MEMORY_FIELDS = ["total", "available", "used", "percent"]
DISK_FIELDS = ["total", "used", "free", "percent"]
TASKS_FIELDS = ["total", "running", "queued", "pending", "completed", "failed", "cancelled"]


class TestMonitorStatsBackendContract:
    """后端 `/api/monitor/stats` 契约：必须包含 Dashboard 消费的字段。"""

    def test_system_stats_fields(self, monitor_py_text: str):
        """get_system_stats 返回结构含 cpu_percent/memory/disk 及子字段。"""
        for field in SYSTEM_FIELDS:
            assert f'"{field}"' in monitor_py_text, (
                f"get_system_stats 契约缺少字段 {field}"
            )
        for field in MEMORY_FIELDS + DISK_FIELDS:
            assert (
                f'"{field}"' in monitor_py_text
            ), f"system 统计契约缺少子字段 {field}"

    def test_task_stats_fields(self, monitor_py_text: str):
        """get_task_stats 返回结构含 running/queued/failed 等任务状态字段。"""
        for field in TASKS_FIELDS:
            assert f'"{field}"' in monitor_py_text, (
                f"get_task_stats 契约缺少任务状态字段 {field}"
            )


class TestMonitorDiskPathContract:
    """磁盘统计路径契约：/stats 的磁盘卡片应统计下载保存目录（真实存储卷）。

    Docker 部署下 `psutil.disk_usage("/")` 反映的是容器 overlay 根分区，
    远小于宿主机磁盘；统计应解析配置的 `save_directory`（若为挂载卷则
    反映真实存储容量），目录不可用/含占位符时回退到工作目录。
    """

    def test_system_stats_accepts_disk_path(self, monitor_py_text: str):
        """get_system_stats 必须接受 disk_path 参数并用于磁盘统计。"""
        assert "def get_system_stats(self, disk_path" in monitor_py_text, (
            "get_system_stats 应新增 disk_path 参数"
        )
        assert "disk_path" in monitor_py_text

    def test_monitor_stats_accepts_config_manager(self, monitor_py_text: str):
        """get_monitor_stats 必须接受 config_manager 以解析下载目录。"""
        assert (
            "def get_monitor_stats(self, task_manager=None, config_manager=None)"
            in monitor_py_text
        ), "get_monitor_stats 应新增 config_manager 参数"

    def test_monitor_stats_resolves_save_directory(self, monitor_py_text: str):
        """磁盘统计路径必须来自下载保存目录 save_directory。"""
        assert "save_directory" in monitor_py_text, (
            "磁盘统计路径应解析 config_manager.save_directory"
        )

    def test_stats_route_passes_config_manager(self, monitor_route_text: str):
        """/stats 路由必须把 config_manager 传入 get_monitor_stats。"""
        assert "config_manager" in monitor_route_text
        assert re.search(
            r"get_monitor_stats\(\s*task_manager=task_manager,\s*config_manager=config_manager",
            monitor_route_text,
        ), "/stats 路由未将下载目录配置透传给监控统计"


class TestDashboardFrontendContract:
    """前端 Dashboard 契约：数据取自 /stats 接口，且字段映射与后端一致。"""

    def test_api_js_provides_get_monitor_stats(self, api_js_text: str):
        """api.js 必须提供 getMonitorStats()，指向 /api/monitor/stats。"""
        assert "async getMonitorStats()" in api_js_text
        assert "getResourceStatus" in api_js_text  # client_status 仍由它在提供
        assert "/api/monitor/stats" in api_js_text

    def test_load_stats_uses_stats_endpoint(self, dashboard_html_text: str):
        """loadStats 必须调用 getMonitorStats() 获取统计卡片数据。"""
        assert "api.getMonitorStats()" in dashboard_html_text

    def test_load_stats_system_mapping(self, dashboard_html_text: str):
        """统计卡片字段必须映射 /stats 的 system.* 结构。"""
        assert re.search(
            r"system\.cpu_percent", dashboard_html_text
        ), "CPU 卡片应映射 system.cpu_percent"
        # 前端通过局部变量 system.memory / system.disk 解构子字段
        assert re.search(r"system\.memory", dashboard_html_text), (
            "内存数据应来自 system.memory 结构"
        )
        for field in ("total", "used", "percent"):
            assert re.search(rf"\bmemory\.{field}\b", dashboard_html_text), (
                f"内存卡片应映射 system.memory.{field}"
            )
        assert re.search(r"system\.disk", dashboard_html_text), (
            "磁盘数据应来自 system.disk 结构"
        )
        for field in ("total", "used", "free", "percent"):
            assert re.search(rf"\bdisk\.{field}\b", dashboard_html_text), (
                f"磁盘卡片应映射 system.disk.{field}"
            )

    def test_load_stats_tasks_mapping(self, dashboard_html_text: str):
        """运行任务卡片必须映射 /stats 的 tasks.* 结构。"""
        for field in ("running", "queued", "failed"):
            assert re.search(rf"tasks\.{field}", dashboard_html_text), (
                f"任务卡片应映射 tasks.{field}"
            )

    def test_load_stats_no_old_flat_fields(self, dashboard_html_text: str):
        """不得再使用 /resource/status 的旧平铺字段映射统计卡片。"""
        for old in (
            "status.disk_total",
            "status.disk_used",
            "status.disk_free",
            "status.disk_percent",
            "status.memory_total",
            "status.memory_used",
            "status.memory_percent",
            "status.cpu_percent",
            "status.running_tasks",
            "status.queued_tasks",
            "status.failed_tasks",
        ):
            assert old not in dashboard_html_text, f"残留旧字段映射: {old}"

    def test_load_stats_keeps_client_status(self, dashboard_html_text: str):
        """client_status（顶部连接状态指示器）仍须从 /resource/status 获取。"""
        assert "api.getResourceStatus()" in dashboard_html_text
        assert "client_status" in dashboard_html_text
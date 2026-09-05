# coding=UTF-8
"""路径解析回归测试。

覆盖 Docker 部署暴露的路径推导 bug：
- constants.WORK_DIR 应指向项目根（容器内 /app），而非 module 目录（/app/module）
- integration 的 project_root 推导应同样指向项目根，保证数据目录挂载一致

背景：e42ba64 将 constants.py/integration.py 移入 module/core/ 后，
``dirname(dirname(__file__))`` 公式未同步修正，导致运行时路径整体
"下沉"到 module/ 目录，数据库与日志偏离部署挂载假设。
"""

import os
from unittest.mock import mock_open, patch

import module
from module.core import integration


def _project_root_expected() -> str:
    """期望的项目根：module 包目录的上一级（<repo> 或容器 /app）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(module.__file__)))


class TestConstantsWorkDir:
    """constants.WORK_DIR 应解析为项目根。"""

    def test_work_dir_is_project_root(self):
        """WORK_DIR 应等于项目根，而不是 module 子目录。"""
        from module.core.constants import WORK_DIR

        assert WORK_DIR == _project_root_expected()
        assert os.path.basename(WORK_DIR) != "module"

    def test_log_path_under_work_dir_logs(self):
        """日志应写入项目根/logs 下（Docker 挂载 ./logs:/app/logs）。"""
        from module.core.constants import LOG_PATH, WORK_DIR

        assert LOG_PATH == os.path.join(WORK_DIR, "logs", "trmd.log")

    def test_input_history_under_work_dir(self):
        """输入历史应位于项目根/.history。"""
        from module.core.constants import INPUT_HISTORY_PATH, WORK_DIR

        assert INPUT_HISTORY_PATH == os.path.join(WORK_DIR, ".history")


class TestIntegrationProjectRoot:
    """integration 的项目根推导应指向项目根。"""

    def test_init_context_data_dir_container_layout(self):
        """容器布局下，data_directory 相对值应解析到项目根/.trmd。

        模拟 __file__ 为容器路径 /app/module/core/integration.py，
        config.yaml 配置 data_directory: ./.trmd 时，数据目录应为
        project_root/.trmd（与 compose 挂载 ./data/.trmd:/app/.trmd 一致）。
        """
        fake_file = "/app/module/core/integration.py"
        expected = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(fake_file)))
            ),
            ".trmd",
        )
        with (
            patch.object(integration, "__file__", fake_file),
            patch("builtins.open", mock_open(read_data="data_directory: ./.trmd\n")),
            patch.object(integration, "AppContext") as mock_cls,
        ):
            integration.init_context()
        assert mock_cls.call_args.kwargs["data_dir"] == expected

    def test_init_context_data_dir_local_layout(self):
        """本地布局下，数据目录应解析到项目根/.trmd。"""
        expected = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(integration.__file__)))
            ),
            ".trmd",
        )
        with (
            patch("builtins.open", mock_open(read_data="data_directory: ./.trmd\n")),
            patch.object(integration, "AppContext") as mock_cls,
        ):
            integration.init_context()
        assert mock_cls.call_args.kwargs["data_dir"] == expected

    def test_init_context_data_dir_absolute(self):
        """data_directory 为绝对路径时直接使用，不做拼接。"""
        abs_data = os.path.abspath(os.path.join("custom", "data"))
        with (
            patch.object(integration, "__file__", "/app/module/core/integration.py"),
            patch(
                "builtins.open",
                mock_open(read_data=f"data_directory: {abs_data}\n"),
            ),
            patch.object(integration, "AppContext") as mock_cls,
        ):
            integration.init_context()
        assert mock_cls.call_args.kwargs["data_dir"] == os.path.normpath(abs_data)

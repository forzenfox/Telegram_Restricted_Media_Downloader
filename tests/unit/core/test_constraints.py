# -*- coding: utf-8 -*-
"""系统约束验证测试。

验证系统约束条件的正确性，包括：
- CLI 交互式链接输入功能已移除
- 批量下载入口点限制
- 配置文件路径约定
"""

import os
from pathlib import Path

import pytest


class TestCLIInteractiveRemoved:
    """验证 CLI 交互式链接输入功能已移除。"""

    def test_main_no_config_guide(self):
        """验证 main.py 不包含 config_guide 调用。"""
        from main import __file__ as main_file

        with open(main_file, "r", encoding="utf-8") as f:
            content = f.read()

        assert "config_guide" not in content, "main.py 不应包含 config_guide 调用"

    def test_legacy_config_no_interactive_input(self):
        """验证 legacy_config.py 不包含交互式 input() 逻辑。"""
        from module.core.legacy_config import __file__ as legacy_file

        with open(legacy_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否有用于链接输入的 input() 调用
        # console.input 用于确认操作是允许的，但不应有用于链接输入的 input()
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            # 排除注释行
            if line.strip().startswith("#"):
                continue
            # 检查是否有 input() 调用（排除 console.input）
            if "input(" in line and "console.input" not in line:
                pytest.fail(
                    f"legacy_config.py 第 {i} 行包含 input() 调用: {line.strip()}"
                )

    def test_enums_no_validator_class(self):
        """验证 enums.py 不包含 Validator 类。"""
        from module.core import enums

        assert not hasattr(enums, "Validator"), "enums.py 不应包含 Validator 类"

    def test_enums_no_process_config_class(self):
        """验证 enums.py 不包含 ProcessConfig 类。"""
        from module.core import enums

        assert not hasattr(enums, "ProcessConfig"), "enums.py 不应包含 ProcessConfig 类"

    def test_enums_no_get_stdio_params_class(self):
        """验证 enums.py 不包含 GetStdioParams 类。"""
        from module.core import enums

        assert not hasattr(enums, "GetStdioParams"), (
            "enums.py 不应包含 GetStdioParams 类"
        )

    def test_config_no_task_links(self):
        """验证 config.yaml 不包含 task.links 配置。"""
        config_file = Path(__file__).parent.parent.parent.parent / "config.yaml"

        if not config_file.exists():
            pytest.skip("config.yaml 不存在")

        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否有 task.links 配置
        lines = content.split("\n")
        in_task_section = False
        for line in lines:
            if line.strip().startswith("task:"):
                in_task_section = True
            elif (
                line.strip() and not line.startswith(" ") and not line.startswith("\t")
            ):
                in_task_section = False

            if in_task_section and "links:" in line:
                pytest.fail(f"config.yaml 包含 task.links 配置: {line.strip()}")


class TestBatchEntryPoints:
    """验证批量下载入口点限制。"""

    def test_web_api_supports_batch_task_creation(self):
        """验证 Web API 支持批量任务创建。"""
        from module.api.routes.tasks import router

        # 检查是否有 POST /tasks 端点
        routes = [route for route in router.routes]
        post_tasks_exists = any(
            hasattr(route, "path")
            and route.path == "/tasks"
            and "POST" in getattr(route, "methods", [])
            for route in routes
        )

        assert post_tasks_exists, "Web API 应支持 POST /tasks 端点用于批量任务创建"

    def test_bot_has_batch_command(self):
        """验证 Bot 注册了 /batch 命令。"""
        from module.bot.commands import BotCommands

        # 检查是否有 cmd_batch 方法
        assert hasattr(BotCommands, "cmd_batch"), "BotCommands 应包含 cmd_batch 方法"

        # 检查方法是否可调用
        assert callable(getattr(BotCommands, "cmd_batch")), "cmd_batch 应为可调用方法"

    def test_cli_no_batch_download_entry(self):
        """验证 CLI 不包含批量下载入口点。"""
        from main import __file__ as main_file

        with open(main_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否有批量下载的 CLI 入口
        # 允许 --web 和 --web-only，但不应该有 --batch 或类似的批量下载参数
        assert "--batch" not in content, "main.py 不应包含 --batch 参数"
        assert "batch_download" not in content, (
            "main.py 不应包含 batch_download 函数调用"
        )


class TestConfigPathConvention:
    """验证配置文件路径约定。"""

    def test_default_config_path_format(self):
        """验证默认配置路径格式。"""
        from module.core.legacy_config import UserConfig

        # UserConfig.PATH 应该是相对于工作目录的 config.yaml
        assert UserConfig.FILE_NAME == "config.yaml", "配置文件名应为 config.yaml"
        assert UserConfig.PATH.endswith("config.yaml"), "配置路径应以 config.yaml 结尾"

    def test_parser_has_config_argument(self):
        """验证 parser 包含 --config 参数。"""
        from module.core.parser import TelegramRestrictedMediaDownloaderArgumentParser

        # 检查是否有 --config 参数
        parser = TelegramRestrictedMediaDownloaderArgumentParser(add_help=False)
        actions = {action.dest: action for action in parser._actions}

        assert "config" in actions, "parser 应包含 --config 参数"

        # 检查参数属性
        config_action = actions["config"]
        assert config_action.option_strings == ["-c", "--config"], (
            "--config 参数应有 -c 和 --config 两个选项"
        )
        assert config_action.required is False, "--config 参数应为可选"

    def test_log_file_path_format(self):
        """验证日志文件路径格式。"""
        from module import LOG_PATH

        # 日志文件应该在 %APPDATA%\TRMD 或 ~/.config/TRMD 下
        log_path = Path(LOG_PATH)

        if os.name == "nt":  # Windows
            assert "TRMD" in str(log_path) or "AppData" in str(log_path), (
                "Windows 下日志文件路径应包含 TRMD 或 AppData"
            )
        else:
            assert ".config" in str(log_path) or "TRMD" in str(log_path), (
                "Linux/Mac 下日志文件路径应包含 .config 或 TRMD"
            )

    def test_log_path_under_logs_dir(self):
        """验证日志文件位于 logs 子目录（支持 Docker 目录挂载持久化）。"""
        from module import LOG_PATH

        assert Path(LOG_PATH).parent.name == "logs", (
            "日志文件应位于 logs 子目录，以便 Docker 目录挂载持久化日志与备份"
        )

    def test_history_file_path_format(self):
        """验证输入历史文件路径格式。"""
        from module import INPUT_HISTORY_PATH

        # 历史文件应该在 %APPDATA%\TRMD 或 ~/.config/TRMD 下
        history_path = Path(INPUT_HISTORY_PATH)

        if os.name == "nt":  # Windows
            assert "TRMD" in str(history_path) or "AppData" in str(history_path), (
                "Windows 下历史文件路径应包含 TRMD 或 AppData"
            )
        else:
            assert ".config" in str(history_path) or "TRMD" in str(history_path), (
                "Linux/Mac 下历史文件路径应包含 .config 或 TRMD"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

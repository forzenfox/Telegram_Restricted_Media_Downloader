# coding=UTF-8
"""Application.check_download_type 单元测试。

验证「解除仅读配置环境下启动报错」的修复：
当配置中 download_type 为空时，仅在内存中补全默认值，
不再调用 save_config 写回磁盘（避免生产环境只读挂载 config.yaml 时报错）。
"""

from unittest.mock import Mock

from module.app import Application
from module.core.enums import DownloadType

DEFAULT_TYPES = [_ for _ in DownloadType()]


def _make_app():
    """构造一个隔离的 Application 实例（不触发 __init__ 副作用）。"""
    import module.app as app_module

    app = Application.__new__(Application)
    # 避免 console.log / log 在测试中产生副作用
    app_module.console.log = lambda *args, **kwargs: None
    return app


class TestCheckDownloadTypeDefault:
    """download_type 为空时的默认补全行为。"""

    def test_sets_default_in_memory_when_empty(self):
        app = _make_app()
        app.download_type = []
        app.config = {}
        app.save_config = None  # 占位，用于断言不会被调用

        app.check_download_type()

        assert app.download_type == DEFAULT_TYPES
        assert app.config["download_type"] == DEFAULT_TYPES

    def test_does_not_call_save_config_when_empty(self):
        app = _make_app()
        app.download_type = []
        app.config = {}
        save_config = Mock()
        app.save_config = save_config

        app.check_download_type()

        save_config.assert_not_called()


class TestCheckDownloadTypeExisting:
    """download_type 非空时的行为。"""

    def test_keeps_existing_types(self):
        app = _make_app()
        app.download_type = ["video", "photo"]
        app.config = {}
        app.save_config = None

        app.check_download_type()

        assert app.download_type == ["video", "photo"]

    def test_does_not_call_save_config_when_non_empty(self):
        app = _make_app()
        app.download_type = ["video", "photo"]
        app.config = {}
        save_config = Mock()
        app.save_config = save_config

        app.check_download_type()

        save_config.assert_not_called()

    def test_removes_unsupported_types_in_memory(self):
        app = _make_app()
        app.download_type = ["video", "not_a_type"]
        app.config = {}
        save_config = Mock()
        app.save_config = save_config

        app.check_download_type()

        assert app.download_type == ["video"]
        save_config.assert_not_called()

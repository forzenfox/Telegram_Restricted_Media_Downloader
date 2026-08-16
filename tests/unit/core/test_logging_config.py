"""日志配置模块测试。

验证日志目录自动创建行为，确保 Docker 目录挂载方案下
RotatingFileHandler 不会因日志目录缺失而初始化失败。
"""

import os

from module.core.logging_config import ensure_log_directory


class TestEnsureLogDirectory:
    """验证 ensure_log_directory 能创建缺失的日志目录且对已存在目录幂等。"""

    def test_creates_missing_directory(self, tmp_path):
        target = os.path.join(str(tmp_path), "a", "b", "logs")
        assert not os.path.exists(target)

        result = ensure_log_directory(target)

        assert result == target
        assert os.path.isdir(target)

    def test_noop_when_directory_exists(self, tmp_path):
        target = str(tmp_path)
        assert os.path.isdir(target)

        result = ensure_log_directory(target)

        assert result == target
        assert os.path.isdir(target)

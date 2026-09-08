# coding=UTF-8
"""UserConfig.save_config 写入失败行为契约测试。

背景：Docker 部署将 /app/config.yaml 以 :ro 只读挂载时，
WebUI 保存配置会触发 [Errno 30] Read-only file system。
改造前 save_config 静默吞掉异常，调用方误判保存成功（WebUI 提示"配置已保存成功"
但文件并未落盘）。

新语义（本文件钉住的契约）：
1. 配置文件不可写（只读/目录缺失）时，save_config 必须抛出异常，不再静默吞掉；
2. 只读类错误（EROFS/EPERM/EACCES）的日志中必须包含可操作的提示（:ro 只读挂载）。

TDD 红灯阶段：以下测试在 legacy_config.py 改造前会失败。
"""

import logging
import os
import stat

import pytest

from module.core.legacy_config import UserConfig


def _build_user_config(path: str) -> UserConfig:
    """构造跳过 __init__（避免读盘/建文件副作用）的 UserConfig 实例。"""
    uc = object.__new__(UserConfig)
    uc.config_path = path
    uc._raw_yaml_data = None
    return uc


class TestSaveConfigWriteFailure:
    """save_config 写入失败时不得静默吞掉异常。"""

    def test_save_config_raises_when_file_read_only(self, tmp_path):
        """配置文件为只读时，save_config 应抛出异常而非静默成功。"""
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\n", encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
        uc = _build_user_config(str(path))
        try:
            with pytest.raises(OSError):
                uc.save_config({"a": 2})
        finally:
            # 恢复可写，避免临时目录清理失败
            os.chmod(path, stat.S_IWRITE)

    def test_save_config_raises_on_write_error(self, tmp_path):
        """目标路径不可写（目录不存在）时，save_config 应抛出异常。"""
        uc = _build_user_config(str(tmp_path / "no_such_dir" / "config.yaml"))
        with pytest.raises(OSError):
            uc.save_config({"a": 2})

    def test_save_config_logs_actionable_hint_when_read_only(self, tmp_path, caplog):
        """只读失败时日志应包含可操作的 :ro 只读挂载提示。"""
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\n", encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
        uc = _build_user_config(str(path))
        try:
            with caplog.at_level(logging.ERROR, logger="rich"), pytest.raises(OSError):
                uc.save_config({"a": 2})
        finally:
            os.chmod(path, stat.S_IWRITE)
        assert "只读" in caplog.text
        assert ":ro" in caplog.text

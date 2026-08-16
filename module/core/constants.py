# coding=UTF-8
"""全局常量定义。

从 module/__init__.py 提取的全局常量、路径和版本信息。
"""

import os
import atexit
import platform

from pyrogram.types.messages_and_media import LinkPreviewOptions


def read_input_history(history_path: str, max_record_len: int, **kwargs) -> None:
    if kwargs.get("platform") == "Windows":
        import readline

        readline.backend = "readline"
        try:
            readline.read_history_file(history_path)
        except FileNotFoundError:
            pass
        readline.set_history_length(max_record_len)
        atexit.register(readline.write_history_file, history_path)


# 版本与作者
AUTHOR = "Gentlesprite"
__version__ = "2.0.0"
__license__ = "MIT License"
__update_date__ = "2026/03/30 18:17:53"
__copyright__ = f"Copyright (C) 2024-{__update_date__[:4]} {AUTHOR} <https://github.com/Gentlesprite>"

# 软件名称
SOFTWARE_FULL_NAME = "Telegram Restricted Media Downloader"
SOFTWARE_SHORT_NAME = "TRMD"

# 工作目录（软件所在目录）
WORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 路径常量
PLATFORM = platform.system()
INPUT_HISTORY_PATH = os.path.join(WORK_DIR, ".history")
MAX_RECORD_LENGTH = 1000

# 执行副作用：读取输入历史
read_input_history(
    history_path=INPUT_HISTORY_PATH, max_record_len=MAX_RECORD_LENGTH, platform=PLATFORM
)

# 日志相关常量
# 日志统一放入 logs 子目录，便于 Docker 以目录挂载（./logs:/app/logs）
# 持久化日志与轮转备份文件。
LOG_PATH = os.path.join(WORK_DIR, "logs", "trmd.log")
MAX_LOG_SIZE = 200 * 1024 * 1024  # 200MB
BACKUP_COUNT = 0  # 不保留日志文件
LOG_FORMAT = "%(name)s:%(funcName)s:%(lineno)d - %(message)s"
LOG_TIME_FORMAT = "[%Y-%m-%d %H:%M:%S]"
SLEEP_THRESHOLD = 60
LINK_PREVIEW_OPTIONS = LinkPreviewOptions(is_disabled=True)

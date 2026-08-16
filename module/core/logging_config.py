# coding=UTF-8
"""日志配置模块。

从 module/__init__.py 提取的日志初始化逻辑。
"""

import logging
import os
import shutil
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

import yaml
from rich.console import Console
from rich.logging import RichHandler

from module.core.constants import (
    LOG_PATH,
    MAX_LOG_SIZE,
    BACKUP_COUNT,
    LOG_FORMAT,
    LOG_TIME_FORMAT,
    SOFTWARE_SHORT_NAME,
    __version__,
    __update_date__,
)


def via_log_level(
    log_level: str, param_name: str, default_level: int = logging.INFO
) -> bool:
    """验证日志级别是否有效。"""
    valid_levels = [
        "CRITICAL",
        "FATAL",
        "ERROR",
        "WARN",
        "WARNING",
        "INFO",
        "DEBUG",
        "NOTSET",
    ]
    return log_level in valid_levels


class CustomDumper(yaml.Dumper):
    """自定义 YAML Dumper，将 None 表示为 ~。

    .. deprecated::
        已由 module.yaml_utils 中的 ruamel.yaml 方案替代。
        保留此类仅为向后兼容（通过 module/__init__.py 导出），
        新代码请使用 module.yaml_utils 中的 get_yaml()/dump_yaml()。
    """

    def represent_none(self, data):
        return self.represent_scalar("tag:yaml.org,2002:null", "~")


# 初始化日志级别（从配置文件读取）
FILE_LOG_LEVEL: int = logging.INFO
CONSOLE_LOG_LEVEL: int = logging.WARNING


def _load_log_levels_from_config():
    """从 config.yaml 的 log 分组读取日志级别。

    优先级：
    1. 工作目录下的 config.yaml 的 log 分组
    2. 默认值 INFO / WARNING
    """
    global FILE_LOG_LEVEL, CONSOLE_LOG_LEVEL

    # 尝试从 config.yaml 的 log 分组读取
    # 使用与 UserConfig 一致的路径解析：sys.argv[0] 所在目录下的 config.yaml
    config_yaml_path = os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[0])), "config.yaml"
    )
    config_yaml_path = os.path.normpath(config_yaml_path)
    if os.path.exists(config_yaml_path):
        try:
            with open(file=config_yaml_path, mode="r", encoding="UTF-8") as f:
                config_data = yaml.safe_load(f)
            if config_data and isinstance(config_data, dict):
                log_section = config_data.get("log", {})
                if isinstance(log_section, dict):
                    file_log_level = log_section.get("file_log_level")
                    console_log_level = log_section.get("console_log_level")
                    if file_log_level and via_log_level(
                        file_log_level, "file_log_level", logging.INFO
                    ):
                        FILE_LOG_LEVEL = logging.getLevelName(file_log_level)
                    if console_log_level and via_log_level(
                        console_log_level, "console_log_level", logging.WARNING
                    ):
                        CONSOLE_LOG_LEVEL = logging.getLevelName(console_log_level)
                    return
        except Exception:
            pass


# 加载日志级别
_load_log_levels_from_config()

# 进程内备份状态标志，防止同一进程内重复备份
_log_backup_done = False


def ensure_log_directory(log_dir: str | None = None) -> str:
    """确保日志目录存在，返回日志目录路径。

    RotatingFileHandler 不会自动创建父目录，Docker 采用目录挂载
    （./logs:/app/logs）后，日志目录可能不存在，需在此显式创建。
    """
    log_dir = log_dir or os.path.dirname(LOG_PATH)
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def _has_recent_backup(seconds: int = 10) -> bool:
    """检查是否已经有最近的备份文件。

    用于防止多进程同时启动时重复备份同一个日志文件。
    """
    log_dir = os.path.dirname(LOG_PATH)
    if not os.path.exists(log_dir):
        return False

    now = datetime.now()
    log_base = os.path.splitext(os.path.basename(LOG_PATH))[0]

    for filename in os.listdir(log_dir):
        if not filename.startswith(f"{log_base}_") or not filename.endswith(".log"):
            continue
        try:
            # 从文件名提取时间戳，格式：trmd_YYYYMMDD_HHMMSS.log
            parts = filename[len(log_base) + 1 : -4].split("_")
            timestamp_str = parts[0] + parts[1]
            backup_time = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
            if 0 <= (now - backup_time).total_seconds() < seconds:
                return True
        except (IndexError, ValueError):
            continue
    return False


def _backup_existing_log_file():
    """备份已存在的日志文件。

    如果日志文件存在且大小大于0，则将其重命名为带时间戳的备份文件。
    这样每次服务重启都会创建新的日志文件，同时保留历史日志。

    使用进程内标志防止同一进程内多次导入本模块时重复备份，
    同时检查最近是否已有备份，防止多进程同时启动时重复备份。
    如果文件被占用（例如正在被其他进程使用），则跳过备份。
    这通常发生在热重启或进程还在运行时。
    """
    global _log_backup_done
    if _log_backup_done:
        return

    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 0:
        # 如果最近已经有备份文件，跳过（防止多进程同时启动重复备份）
        if _has_recent_backup(seconds=10):
            _log_backup_done = True
            return

        # 生成备份文件名：trmd_YYYYMMDD_HHMMSS.log
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = os.path.dirname(LOG_PATH)
        log_name = os.path.basename(LOG_PATH)
        log_ext = os.path.splitext(log_name)[1]
        log_base = os.path.splitext(log_name)[0]

        backup_path = os.path.join(log_dir, f"{log_base}_{timestamp}{log_ext}")

        # 如果备份文件已存在（极少情况），添加计数器
        counter = 1
        while os.path.exists(backup_path):
            backup_path = os.path.join(
                log_dir, f"{log_base}_{timestamp}_{counter}{log_ext}"
            )
            counter += 1

        try:
            # 尝试移动旧日志文件到备份位置
            shutil.move(LOG_PATH, backup_path)
            _log_backup_done = True
        except PermissionError:
            # 文件被占用，跳过备份（可能是热重启或进程还在运行）
            # 在 Windows 上，文件可能被其他进程锁定
            pass
    else:
        _log_backup_done = True


# 确保日志目录存在（支持 Docker 目录挂载场景）
ensure_log_directory()

# 备份旧日志文件
_backup_existing_log_file()

# 创建控制台
console = Console(log_path=False, log_time_format=LOG_TIME_FORMAT)

# 配置文件处理器
file_handler = RotatingFileHandler(
    filename=LOG_PATH, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="UTF-8"
)
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)-8s" + " " + LOG_FORMAT, datefmt=LOG_TIME_FORMAT
    )
)
file_handler.setLevel(logging.getLevelName(FILE_LOG_LEVEL))

# 配置控制台处理器
console_handler = RichHandler(
    level=CONSOLE_LOG_LEVEL,
    console=console,
    rich_tracebacks=True,
    show_path=False,
    omit_repeated_times=True,
    log_time_format=LOG_TIME_FORMAT,
)

# 配置根日志记录器
logging.basicConfig(
    level=logging.DEBUG,
    format=LOG_FORMAT,
    datefmt=LOG_TIME_FORMAT,
    handlers=[console_handler, file_handler],
)

# 创建主日志记录器
log = logging.getLogger("rich")
# 抑制 Pyrogram 的 INFO 级别日志
logging.getLogger("pyrogram").setLevel(logging.WARNING)
# 抑制 Pyrogram 连接层频繁的 DEBUG 日志（Sent/Recv MTProto 消息）
logging.getLogger("pyrogram.connection").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)
# 抑制 uvicorn 的 access log（由 RequestLogMiddleware 替代，可按需过滤）
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
log.info(f"{SOFTWARE_SHORT_NAME}:{__version__},更新日期:{__update_date__}。")
log.info(f'文件日志等级:"{logging.getLevelName(FILE_LOG_LEVEL)}"。')
log.info(f'终端日志等级:"{logging.getLevelName(CONSOLE_LOG_LEVEL)}"。')

# 注册自定义 Dumper
CustomDumper.add_representer(type(None), CustomDumper.represent_none)

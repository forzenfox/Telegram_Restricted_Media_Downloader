# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:2025/2/25 1:32
# File:config.py
import copy
import errno
import os
import shutil
import sys
import logging
import datetime

from typing import Union

from module import (
    FILE_LOG_LEVEL,
    CONSOLE_LOG_LEVEL,
    log,
    console,
    PLATFORM,
)
from module.core.language import _t
from module.core.parser import PARSE_ARGS
from module.utils.path_tool import gen_backup_config, safe_scan_directory_file
from module.core.enums import KeyWord
from module.utils.yaml_utils import (
    load_yaml,
    dump_yaml,
    deep_merge,
    init_config_from_template,
)


class BaseConfig:
    FILE_NAME: str = "base_config.yaml"
    PATH: str = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), FILE_NAME)
    TEMPLATE: dict = {}

    def __init__(self):
        self.config: dict = self.TEMPLATE.copy()
        self.config_path: str = self.PATH
        self._raw_yaml_data = None  # 缓存原始 CommentedMap，保留注释

    @staticmethod
    def add_missing_keys(target, template, log_message) -> None:
        """添加缺失的配置文件参数。"""
        for key, value in template.items():
            if key not in target:
                target[key] = value
                console.log(log_message.format(key))

    @staticmethod
    def remove_extra_keys(target, template, log_message) -> None:
        """删除多余的配置文件参数。"""
        keys_to_remove: list = [key for key in target.keys() if key not in template]
        for key in keys_to_remove:
            target.pop(key)
            console.log(log_message.format(key))

    def process_nesting(self, param_name: Union[str, dict], config, template=None):
        param_template = (template or self.TEMPLATE).get(param_name)
        param_length = len(param_template)
        if param_name in config:
            param_template = (template or self.TEMPLATE).get(param_name)
            param_config = config.get(param_name)
            if not isinstance(param_config, dict) or (
                isinstance(param_config, dict) and len(param_config) != param_length
            ):
                param_config: dict = {}
                config[param_name] = param_config
            self.add_missing_keys(
                target=param_config,
                template=param_template,
                log_message=f'"{{}}"不在{param_name}配置文件中,已添加。',
            )
            self.remove_extra_keys(
                target=param_config,
                template=param_template,
                log_message=f'"{{}}"不在{param_name}配置文件中,已删除。',
            )

    def __check_params(self, config: dict) -> None:
        """检查配置文件的参数是否完整。"""
        # 如果 config 为 None，初始化为一个空字典。
        if config is None:
            config = {}

        # 处理父级参数。
        self.add_missing_keys(
            target=config,
            template=self.TEMPLATE,
            log_message='"{}"不在全局配置文件中,已添加。',
        )
        # 删除父级模板中没有的字段。
        self.remove_extra_keys(config, self.TEMPLATE, '"{}"不在模板中,已删除。')

        if config != self.config:
            self.config = config
            self.save_config(self.config)

    def load_config(self) -> None:
        """加载全局配置文件。"""
        try:
            if not os.path.exists(self.PATH):
                init_config_from_template(self.TEMPLATE, self.PATH)
                return
            config = load_yaml(self.PATH)
            self._raw_yaml_data = config
            if config:
                self.config = config
            else:
                raise ValueError("The file is empty or has invalid format.")
        except Exception as e:
            log.error(
                f'检测到无效或损坏的全局配置文件。已生成新的模板文件. . .{_t(KeyWord.REASON)}:"{e}"'
            )
            self.config: dict = self.TEMPLATE.copy()
            self.save_config(self.config)

    def save_config(self, config: dict) -> None:
        """保存配置文件。保留原始文件中的注释。"""
        try:
            if self._raw_yaml_data is not None:
                # 将 config 的变更合并到 _raw_yaml_data（保留注释）
                deep_merge(self._raw_yaml_data, config)
                dump_yaml(self._raw_yaml_data, self.config_path)
                # 重新加载以同步 _raw_yaml_data
                self._raw_yaml_data = load_yaml(self.config_path)
            else:
                dump_yaml(config, self.config_path)
                self._raw_yaml_data = load_yaml(self.config_path)
            log.info("全局配置文件已保存。")
        except Exception as e:
            log.error(f'保存全局配置文件失败,{_t(KeyWord.REASON)}:"{e}"')
        finally:
            self.config = config

    def get_config(self, param, error_param=None) -> Union[str, None]:
        """获取实时的配置文件。"""
        self.load_config()
        return self.config.get(param, error_param)


class UserConfig(BaseConfig):
    DIRECTORY_NAME: str = os.path.dirname(
        os.path.abspath(sys.argv[0])
    )  # 获取软件工作绝对目录。
    FILE_NAME: str = "config.yaml"  # 配置文件名。
    PATH: str = os.path.join(DIRECTORY_NAME, FILE_NAME)
    TEMPLATE: dict = {
        "data_directory": None,
        "credential": {
            "api_id": None,
            "api_hash": None,
            "bot_token": None,
        },
        "proxy": {
            "enable_proxy": None,
            "scheme": None,
            "hostname": None,
            "port": None,
            "username": None,
            "password": None,
        },
        "task": {
            "save_directory": None,  # v1.3.0 将配置文件中save_path的参数名修改为save_directory。
            "temp_directory": None,
            "session_directory": None,
            "download_type": None,
            "is_shutdown": None,
            "max_tasks": {"download": None, "upload": None},
            "max_retries": {"download": None, "upload": None},
        },
        "preference": {
            "notice": True,
            "is_shutdown": False,
            "forward_type": {
                "video": True,
                "photo": True,
                "audio": True,
                "document": True,
                "voice": True,
                "text": True,
                "animation": True,
                "video_note": True,
            },
            "upload": {"download_upload": True, "delete": False},
            "export_table": {"link": False, "count": False, "upload": False},
        },
        "log": {
            "file_log_level": logging.getLevelName(FILE_LOG_LEVEL),
            "console_log_level": logging.getLevelName(CONSOLE_LOG_LEVEL),
        },
        "repository": {
            "enabled": True,
            "chat_id": "",
            "auto_sync_enabled": False,
            "auto_sync_interval_minutes": 60,
        },
        "webui": {
            "base_url": "http://localhost:8000",
        },
    }
    # 旧版扁平 TEMPLATE，用于向后兼容和历史配置迁移。
    LEGACY_FLAT_TEMPLATE: dict = {
        "api_id": None,
        "api_hash": None,
        "bot_token": None,
        "session_directory": None,
        "proxy": {
            "enable_proxy": None,
            "scheme": None,
            "hostname": None,
            "port": None,
            "username": None,
            "password": None,
        },
        "save_directory": None,
        "temp_directory": None,
        "max_tasks": {"download": None, "upload": None},
        "is_shutdown": None,
        "download_type": None,
        "max_retries": {"download": None, "upload": None},
    }
    TEMP_DIRECTORY: str = os.path.join(os.getcwd(), "temp")
    BACKUP_DIRECTORY: str = "ConfigBackup"
    ABSOLUTE_BACKUP_DIRECTORY: str = os.path.join(DIRECTORY_NAME, BACKUP_DIRECTORY)
    WORK_DIRECTORY: str = os.path.join(os.getcwd(), "sessions")
    DATA_DIRECTORY: str = os.path.join(os.getcwd(), ".trmd")

    @staticmethod
    def _migrate_legacy_config(config: dict) -> dict:
        """将旧版扁平配置迁移为分组结构。

        检测到旧版扁平键（如 api_id 在顶层）时，自动迁移到分组结构。
        """
        if config is None:
            return UserConfig.TEMPLATE.copy()
        # 如果已经有分组结构，直接返回
        if "credential" in config and isinstance(config.get("credential"), dict):
            return config
        # 执行迁移：从扁平结构提取到分组
        migrated = copy.deepcopy(UserConfig.TEMPLATE)
        # credential
        for key in ("api_id", "api_hash", "bot_token"):
            if key in config:
                migrated["credential"][key] = config[key]
        # proxy
        if "proxy" in config and isinstance(config["proxy"], dict):
            migrated["proxy"] = config["proxy"]
        # task
        task_keys = (
            "links",
            "save_directory",
            "temp_directory",
            "session_directory",
            "download_type",
            "is_shutdown",
        )
        for key in task_keys:
            if key in config:
                migrated["task"][key] = config[key]
        if "max_tasks" in config and isinstance(config["max_tasks"], dict):
            migrated["task"]["max_tasks"] = config["max_tasks"]
        if "max_retries" in config and isinstance(config["max_retries"], dict):
            migrated["task"]["max_retries"] = config["max_retries"]
        return migrated

    def _init_config_file(self, config_path: str) -> None:
        """当配置文件不存在时，生成初始配置文件。

        优先从 config.example.yaml 复制（保留注释），否则从 TEMPLATE 生成。
        """
        example_path = os.path.join(
            os.path.dirname(config_path), "config.example.yaml"
        )
        if os.path.exists(example_path):
            shutil.copy2(example_path, config_path)
            console.log("未找到配置文件,已从示例文件复制. . .")
        else:
            init_config_from_template(UserConfig.TEMPLATE, config_path)
            console.log("未找到配置文件,已生成新的模板文件. . .")

    def __init__(self):
        super().__init__()
        self.config_path: str = (
            PARSE_ARGS.config
            if PARSE_ARGS.config.endswith(".yaml")
            else UserConfig.PATH
        )
        self.platform: str = PLATFORM
        self.history_timestamp: dict = {}
        self.input_link: list = []
        self.last_record: dict = {}
        self.difference_timestamp: dict = {}
        self.download_type: list = []
        self.record_dtype: set = set()
        self.record_flag: bool = False
        self.modified: bool = False
        self.get_last_history_record()
        self.is_change_account: bool = True
        self.re_config: bool = False
        self.config: dict = self.load_config()  # v1.3.0 修复重复询问重新配置文件。
        # 从分组结构中读取属性
        credential: dict = self.config.get("credential", {})
        self.api_hash = credential.get("api_hash")
        self.api_id = credential.get("api_id")
        self.bot_token = credential.get("bot_token")
        task: dict = self.config.get("task", {})
        self.download_type: list = task.get("download_type")
        self.is_shutdown: bool = task.get("is_shutdown")
        self.max_download_task: int = (task.get("max_tasks") or {"download": 3}).get(
            "download"
        )
        self.max_download_retries: int = (
            task.get("max_retries") or {"download": 5}
        ).get("download")
        self.max_upload_task: int = (task.get("max_tasks") or {}).get("upload", 3) or 3
        self.max_upload_retries: int = (task.get("max_retries") or {}).get(
            "upload", 3
        ) or 3
        self.proxy: dict = self.config.get("proxy", {})
        self.enable_proxy: bool = self.proxy.get("enable_proxy", False)
        self.save_directory: str = task.get("save_directory")
        self.work_directory: str = PARSE_ARGS.session or (
            task.get("session_directory") or UserConfig.WORK_DIRECTORY
        )
        self.temp_directory: str = PARSE_ARGS.temp or (
            task.get("temp_directory") or UserConfig.TEMP_DIRECTORY
        )
        self.data_directory: str = (
            self.config.get("data_directory") or UserConfig.DATA_DIRECTORY
        )
        # 规范化的数据目录绝对路径，与 AppContext.data_dir 解析逻辑一致
        from module.utils.path_tool import resolve_data_directory

        self.resolved_data_directory: str = resolve_data_directory(
            self.config.get("data_directory"), UserConfig.DIRECTORY_NAME
        )

    def get_last_history_record(self) -> None:
        """获取最近一次保存的历史配置文件。"""
        # 首先判断是否存在目录文件。
        try:
            res: list = safe_scan_directory_file(UserConfig.ABSOLUTE_BACKUP_DIRECTORY)
        except FileNotFoundError:
            return
        except Exception as e:
            log.error(f'读取历史文件时发生错误,{_t(KeyWord.REASON)}:"{e}"')
            return
        file_start: str = "history_"
        file_end: str = "_config.yaml"

        now_timestamp: float = datetime.datetime.now().timestamp()  # 获取当前的时间戳。
        if res:
            for i in res:  # 找出离当前时间最近的配置文件。
                try:
                    if i.startswith(file_start) and i.endswith(file_end):
                        format_date_str = (
                            i.replace(file_start, "")
                            .replace(file_end, "")
                            .replace("_", " ")
                        )
                        to_datetime_obj = datetime.datetime.strptime(
                            format_date_str, "%Y-%m-%d %H-%M-%S"
                        )
                        timestamp = to_datetime_obj.timestamp()
                        self.history_timestamp[timestamp] = i
                except ValueError:
                    pass
                except Exception as _:
                    pass
            for i in self.history_timestamp.keys():
                self.difference_timestamp[now_timestamp - i] = i
            if self.history_timestamp:  # 如果有符合条件的历史配置文件。
                self.last_record: dict = self.__find_history_config()

        else:
            return

    def __find_history_config(self) -> dict:
        """找到历史配置文件。"""
        if not self.history_timestamp:
            return {}
        if not self.difference_timestamp:
            return {}
        try:
            min_key: int = min(self.difference_timestamp.keys())
            min_diff_timestamp: str = self.difference_timestamp.get(min_key)
            min_config_file: str = self.history_timestamp.get(min_diff_timestamp)
            if not min_config_file:
                return {}
            last_config_file: str = os.path.join(
                UserConfig.ABSOLUTE_BACKUP_DIRECTORY, min_config_file
            )  # 拼接文件路径。
            config = load_yaml(last_config_file)
            last_record: dict = self.__check_params(
                config, history=True
            )  # v1.1.6修复读取历史如果缺失字段使得flag置True。

            if last_record == UserConfig.TEMPLATE:
                # 从字典中删除当前文件。
                self.history_timestamp.pop(min_diff_timestamp, None)
                self.difference_timestamp.pop(min_key, None)
                # 递归调用。
                return self.__find_history_config()
            else:
                return last_record
        except Exception as _:
            return {}

    def add_missing_keys(self, target, template, log_message, history=False) -> None:
        """添加缺失的配置文件参数。"""
        for key, value in template.items():
            if key not in target:
                target[key] = value
                if not history:
                    console.log(log_message.format(key))
                    self.modified = True
                    self.record_flag = True

    def remove_extra_keys(self, target, template, log_message, history=False) -> None:
        """删除多余的配置文件参数。"""
        keys_to_remove: list = [key for key in target.keys() if key not in template]
        for key in keys_to_remove:
            target.pop(key)
            if not history:
                console.log(log_message.format(key))
                self.record_flag = True

    def __check_params(self, config: dict, history=False) -> dict:
        """检查配置文件的参数是否完整。"""
        # 如果 config 为 None，初始化为一个空字典。
        if config is None:
            config = {}

        # 处理父级参数（分组）。
        self.add_missing_keys(
            target=config,
            template=UserConfig.TEMPLATE,
            log_message='"{}"不在配置文件中,已添加。',
            history=history,
        )

        # 处理各分组内的嵌套参数
        self.process_nesting(param_name="proxy", config=config)
        # credential 分组（无嵌套子分组，只有扁平键）
        self.process_nesting(param_name="credential", config=config)
        # task 分组（含嵌套子分组）
        self.process_nesting(param_name="task", config=config)
        if "task" in config and isinstance(config["task"], dict):
            task_template = UserConfig.TEMPLATE.get("task", {})
            self.process_nesting(
                param_name="max_tasks", config=config["task"], template=task_template
            )
            self.process_nesting(
                param_name="max_retries", config=config["task"], template=task_template
            )
        # preference 分组（含嵌套子分组）
        self.process_nesting(param_name="preference", config=config)
        if "preference" in config and isinstance(config["preference"], dict):
            pref_template = UserConfig.TEMPLATE.get("preference", {})
            self.process_nesting(
                param_name="forward_type",
                config=config["preference"],
                template=pref_template,
            )
            self.process_nesting(
                param_name="upload", config=config["preference"], template=pref_template
            )
            self.process_nesting(
                param_name="export_table",
                config=config["preference"],
                template=pref_template,
            )
        # log 分组
        self.process_nesting(param_name="log", config=config)
        # repository 分组
        self.process_nesting(param_name="repository", config=config)
        # webui 分组
        self.process_nesting(param_name="webui", config=config)

        # 删除父级模板中没有的字段。
        self.remove_extra_keys(
            target=config,
            template=UserConfig.TEMPLATE,
            log_message='"{}"不在模板中,已删除。',
            history=history,
        )

        return config

    def load_config(self) -> dict:
        """加载一次当前的配置文件,并附带合法性验证、缺失参数的检测以及各种异常时的处理措施。"""
        config: dict = copy.deepcopy(UserConfig.TEMPLATE)
        try:
            if not os.path.exists(self.config_path):
                self._init_config_file(self.config_path)
                self.re_config = (
                    True  # v1.3.4 修复配置文件不存在时,无法重新生成配置文件的问题。
                )
            config = load_yaml(self.config_path)
            self._raw_yaml_data = config
            # 迁移旧版扁平配置到分组结构
            config = self._migrate_legacy_config(config)
            compare_config: dict = config.copy() if config else {}
            config: dict = self.__check_params(config) if compare_config else None
            if (
                config != compare_config or config == UserConfig.TEMPLATE
            ):  # v1.3.4 修复配置文件所有参数都为空时报错问题。
                self.re_config = True
        except UnicodeDecodeError as e:  # v1.1.3 加入配置文件路径是中文或特殊字符时的编码错误提示,由于nuitka打包的性质决定,
            # 中文路径无法被打包好的二进制文件识别,故在配置文件时无论是链接路径还是媒体保存路径都请使用英文命名。
            self.re_config = True
            log.error(
                f'读取配置文件遇到编码错误,可能保存路径中包含中文或特殊字符的文件夹。已生成新的模板文件. . .{_t(KeyWord.REASON)}:"{e}"'
            )
            self.backup_config(config, error_config=self.re_config)
        except Exception as e:
            self.re_config = True
            console.print("「注意」链接路径和保存路径不能有引号!", style="#B1DB74")
            log.error(
                f'检测到无效或损坏的配置文件。已生成新的模板文件. . .{_t(KeyWord.REASON)}:"{e}"'
            )
            self.backup_config(config, error_config=self.re_config)
        finally:
            if config is None:
                self.re_config = True
                log.warning("检测到空的配置文件。已生成新的模板文件. . .")
                config: dict = UserConfig.TEMPLATE.copy()
        return config

    def backup_config(
        self, backup_config: dict, error_config: bool = False, force: bool = False
    ) -> None:  # v1.2.9 更正backup_config参数类型。
        """备份当前的配置文件。"""
        if (
            backup_config != UserConfig.TEMPLATE or force
        ):  # v1.2.9 修复比较变量错误的问题。
            backup_path: str = gen_backup_config(
                old_path=self.config_path,
                absolute_backup_dir=UserConfig.ABSOLUTE_BACKUP_DIRECTORY,
                error_config=error_config,
            )
            console.log(f'原来的配置文件已备份至"{backup_path}"', style="#B1DB74")
        else:
            console.log("配置文件与模板文件完全一致,无需备份。")

    def save_config(self, config: dict) -> None:
        """保存配置文件。保留原始文件中的注释。

        写入失败时记录日志并重新抛出异常，由调用方决定如何处理，
        避免静默吞掉错误导致 WebUI 误报"配置已保存成功"。
        """
        try:
            if self._raw_yaml_data is not None:
                # 将 config 的变更合并到 _raw_yaml_data（保留注释）
                deep_merge(self._raw_yaml_data, config)
                dump_yaml(self._raw_yaml_data, self.config_path)
                # 重新加载以同步 _raw_yaml_data
                self._raw_yaml_data = load_yaml(self.config_path)
            else:
                dump_yaml(config, self.config_path)
                self._raw_yaml_data = load_yaml(self.config_path)
            log.info("配置文件已保存。")
        except OSError as e:
            if e.errno in (errno.EROFS, errno.EPERM, errno.EACCES):
                log.error(
                    f'保存配置文件失败,{_t(KeyWord.REASON)}:"{e}"。'
                    "配置文件不可写(可能为只读挂载或权限不足)。"
                    "若使用 Docker 部署,请检查 docker-compose.yml 中 config.yaml "
                    "挂载是否带 :ro 只读标志并移除,或改为在宿主机上直接编辑配置文件。"
                )
            else:
                log.error(f'保存配置文件失败,{_t(KeyWord.REASON)}:"{e}"')
            raise
        except Exception as e:
            log.error(f'保存配置文件失败,{_t(KeyWord.REASON)}:"{e}"')
            raise

    def ctrl_c(self):
        """服务退出时的处理（已移除暂停行为）。"""
        pass

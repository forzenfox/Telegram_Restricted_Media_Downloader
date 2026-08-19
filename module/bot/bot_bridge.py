# coding=UTF-8
"""BotTaskBridge - BOT 命令参数 → TaskManager 任务桥接适配器。

将 BOT 端旧命令（/download 等）解析出的链接/参数转换为
TaskManager.create_task() 的标准参数，统一走新架构调度，
使 BOT 创建的任务在 Web 端可见、可监控、可控制。

设计约束（项目约定）：
- 任务创建与调度必须经过 TaskManager，禁止绕过直接执行；
- BOT 端只负责参数解析与用户交互，执行统一交给 TaskExecutor。
"""

import logging
import os
from typing import Any

from module.utils.helpers import extract_info_from_link

log = logging.getLogger("rich")


class BotTaskBridge:
    """BOT 命令参数到 TaskManager 任务的桥接适配器。"""

    def __init__(
        self,
        task_manager: Any,
        client: Any | None = None,
        config_manager: Any | None = None,
    ):
        """
        Args:
            task_manager: TaskManager 实例（从 AppContext 获取的共享实例）
            client: Pyrogram Client（可选，用于链接解析兜底）
            config_manager: ConfigManager 实例（可选，用于 save_directory 路径转换）
        """
        self._task_manager = task_manager
        self._client = client
        self._config_manager = config_manager or getattr(
            task_manager, "_config_manager", None
        )

    async def create_download_tasks_from_links(self, links: list[str]) -> dict:
        """将链接列表转换为下载任务并启动。

        按来源（频道/群组）分组，同一来源的消息合并为一个下载任务；
        每个任务创建后立即启动（进入 TaskManager 调度）。

        Args:
            links: Telegram 消息链接列表，如 https://t.me/channel/123

        Returns:
            dict: {"created": [task_id, ...], "failed": [(link, reason), ...]}
        """
        # 1. 解析链接并按来源分组
        grouped: dict[str, list[int]] = {}
        failed: list[tuple[str, str]] = []
        for link in links:
            try:
                parsed = extract_info_from_link(link)
                source = parsed.group_id
                msg_id = parsed.post_id
                if not source or msg_id is None:
                    raise ValueError("无效的链接格式")
                grouped.setdefault(str(source), []).append(int(msg_id))
            except Exception as e:
                failed.append((link, str(e)))

        # 2. 每组创建并启动一个下载任务
        created: list[str] = []
        for source, message_ids in grouped.items():
            try:
                task_id = await self._create_and_start_download(source, message_ids)
                created.append(task_id)
            except Exception as e:
                failed.append((str(source), str(e)))

        log.info(
            f"BotTaskBridge: 创建 {len(created)} 个下载任务, 失败 {len(failed)} 个链接"
        )
        return {"created": created, "failed": failed}

    async def _create_and_start_download(
        self, source: str, message_ids: list[int]
    ) -> str:
        """创建并启动单个来源的下载任务，返回任务 ID。

        source 为数字 chat_id 时直接传入 chat_id；否则作为
        source_identifier 交由 IdentifierService 解析。
        """
        from module.core.task.manager import TaskType

        params: dict = {
            "range_mode": "multiple_ids",
            "message_list": message_ids,
        }
        chat_id = None
        if str(source).lstrip("-").isdigit():
            chat_id = int(source)
        else:
            params["source_identifier"] = source

        task = await self._task_manager.create_task(
            task_type=TaskType.DOWNLOAD,
            chat_id=chat_id,
            params=params,
        )
        await self._task_manager.start_task(task.task_id)
        return task.task_id

    def _get_save_root(self) -> str:
        """获取保存根目录（用于文件路径可移植转换）。"""
        if self._config_manager is not None:
            return self._config_manager.save_directory
        return os.path.join(os.getcwd(), "downloads")

    async def _resolve_target_chat_id(self, target_identifier: str) -> int:
        """解析上传目标标识符为 chat_id。

        支持：数字 chat_id、me/self（使用 client 自身 ID）、
        t.me 链接 / @username / 裸 username（经 IdentifierService 解析）。
        """
        text = (target_identifier or "").strip()
        if not text:
            raise ValueError("上传目标为空")

        # 数字 chat_id 直接使用
        if str(text).lstrip("-").isdigit():
            return int(text)

        # me/self：上传到用户自己
        if text in ("me", "self"):
            if self._client is None:
                raise ValueError("上传目标为 me/self 但 client 不可用")
            me = await self._client.get_me()
            return int(me.id)

        # 其他标识符：交由 IdentifierService 解析
        identifier_service = getattr(self._task_manager, "_identifier_service", None)
        if identifier_service is None:
            raise ValueError("无法解析上传目标（IdentifierService 未注入）")
        resolved = await identifier_service.resolve(text)
        return int(resolved.chat_id)

    async def create_upload_task(
        self,
        file_path: str,
        target_identifier: str,
        delete_after_upload: bool = True,
    ) -> str:
        """创建单个文件的上传任务并启动，返回任务 ID。

        文件路径转换为可移植相对路径存储，由 TaskExecutor 执行上传。
        """
        from module.core.task.manager import TaskType
        from module.utils.path_tool import to_portable_path

        save_root = self._get_save_root()
        portable_fp = to_portable_path(file_path, save_root)
        chat_id = await self._resolve_target_chat_id(target_identifier)

        task = await self._task_manager.create_task(
            task_type=TaskType.UPLOAD,
            chat_id=chat_id,
            params={
                "file_paths": [portable_fp],
                "delete_after_upload": delete_after_upload,
            },
        )
        await self._task_manager.start_task(task.task_id)
        return task.task_id

    async def create_forward_task(
        self,
        origin_identifier: str,
        target_identifier: str,
        start_id: int,
        end_id: int,
    ) -> str:
        """创建转发任务并启动，返回任务 ID。

        源频道标识符解析为任务 chat_id，目标频道解析为 target_chat_id，
        消息范围经 id_range 模式交给 TaskExecutor 逐条转发。
        """
        from module.core.task.manager import TaskType

        chat_id = await self._resolve_target_chat_id(origin_identifier)
        target_chat_id = await self._resolve_target_chat_id(target_identifier)

        task = await self._task_manager.create_task(
            task_type=TaskType.FORWARD,
            chat_id=chat_id,
            params={
                "range_mode": "id_range",
                "min_id": int(start_id),
                "max_id": int(end_id),
                "target_chat_id": target_chat_id,
            },
        )
        await self._task_manager.start_task(task.task_id)
        return task.task_id

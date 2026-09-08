# coding=UTF-8
"""TaskExecutor - 任务执行桥接器

桥接 TaskManager 与实际下载/上传逻辑，负责任务的实际执行和进度回调。
"""

import asyncio
import concurrent.futures
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

import pyrogram
from pyrogram.errors.exceptions.bad_request_400 import (
    ChatForwardsRestricted as ChatForwardsRestricted_400,
)
from pyrogram.errors.exceptions.not_acceptable_406 import (
    ChatForwardsRestricted as ChatForwardsRestricted_406,
)
from pyrogram.handlers import MessageHandler

from module.core.config_manager import ConfigManager
from module.core.download.file_manager import FileInfo, FileManager, UploadProgress
from module.core.task.manager import (
    LISTEN_TASK_TYPES,
    RESIDENT_RUNNING_TASK_TYPES,
    ExecutorError,
    ItemStatus,
    Task,
    TaskItem,
    TaskManager,
    TaskStatus,
    TaskType,
)
from module.utils.path_tool import (
    from_portable_path,
    safe_scan_directory_file,
    to_portable_path,
)
from module.utils.timezone import parse_user_date

log = logging.getLogger("rich")


class TaskExecutor:
    """任务执行桥接器，将 TaskManager 的任务分派给实际的执行逻辑。"""

    def __init__(
        self,
        task_manager: TaskManager,
        file_manager: FileManager,
        client: Any,
        downloader: Any = None,
        uploader: Any = None,
        config_manager: Optional[ConfigManager] = None,
        repository_manager: Optional[Any] = None,
    ):
        """
        Args:
            task_manager: TaskManager 实例
            file_manager: FileManager 实例
            client: Pyrogram Client 实例
            downloader: 下载器实例（可选）
            uploader: 上传器实例（可选）
            config_manager: ConfigManager 实例（可选，用于读取并发配置）
            repository_manager: RepositoryManager 实例（可选，用于仓库去重）
        """
        self._task_manager = task_manager
        self._file_manager = file_manager
        self._client = client
        self._downloader = downloader
        self._uploader = uploader
        self._config_manager = config_manager
        self._repository_manager = repository_manager
        self._running_tasks: dict[str, asyncio.Task] = {}

        # TaskExecutor 通常在 Telegram Client 的事件循环中创建，
        # 而 Web API 运行在另一个线程的事件循环。保存创建时的 loop，
        # 以便通过 run_coroutine_threadsafe 将任务提交到正确的 loop。
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

        # 并发控制：从 ConfigManager 读取，默认值与 DEFAULT_RESOURCE_LIMITS 一致
        if config_manager:
            rl = config_manager.resource_limits
            dl_concurrency = rl.get("max_download_concurrency", 3)
            ul_concurrency = rl.get("max_upload_concurrency", 1)
            fwd_concurrency = rl.get("max_forward_concurrency", 1)
        else:
            dl_concurrency = 3
            ul_concurrency = 1
            fwd_concurrency = 1

        self._download_semaphore = asyncio.Semaphore(dl_concurrency)
        self._upload_semaphore = asyncio.Semaphore(ul_concurrency)
        self._forward_semaphore = asyncio.Semaphore(fwd_concurrency)

        # 定时清理任务调度器（周期触发 cleanup_files 任务）。
        from module.core.task.scheduler import CleanupScheduler

        self.cleanup_scheduler = CleanupScheduler(
            task_manager=task_manager, executor=self
        )

    def _should_use_repository(self) -> bool:
        """判断是否启用仓库去重。"""
        result = (
            self._repository_manager is not None
            and self._repository_manager.should_use_repository()
        )
        log.debug(
            f"_should_use_repository: result={result}, "
            f"repo_mgr={'exists' if self._repository_manager else 'None'}"
        )
        return result

    def _get_save_root(self) -> str:
        """获取保存根目录（用于路径转换）。

        Returns:
            配置的 save_directory 路径，默认为 ./downloads
        """
        if self._config_manager:
            return self._config_manager.save_directory
        return os.path.join(os.getcwd(), "downloads")

    async def _update_item_metadata(self, task_id: str, item_id: str, **kwargs) -> None:
        """更新子任务的元数据字段（file_unique_id 等）。"""
        task = self._task_manager._tasks.get(task_id)
        if not task:
            return
        for item in task.items:
            if item.id == item_id:
                for key, value in kwargs.items():
                    if hasattr(item, key):
                        setattr(item, key, value)
                await self._task_manager.update_item_status(
                    task_id, item_id, item.status
                )
                break

    def submit_task(self, task: Task) -> concurrent.futures.Future:
        """将任务提交到 TaskExecutor 创建时所在的事件循环中执行。

        Web API 与 Telegram Client 运行在不同线程的事件循环中，直接 await
        execute_task() 会导致跨 loop 的 RuntimeError。此方法使用
        asyncio.run_coroutine_threadsafe 把 coroutine 投递到正确的 loop。

        Args:
            task: 要执行的任务

        Returns:
            concurrent.futures.Future: 可用于等待结果或检查异常

        Raises:
            RuntimeError: TaskExecutor 未绑定事件循环时抛出
        """
        if self._event_loop is None:
            raise RuntimeError("TaskExecutor 未绑定事件循环，无法提交任务")
        return asyncio.run_coroutine_threadsafe(
            self.execute_task(task), self._event_loop
        )

    async def _execute_cleanup(self, task: Task) -> None:
        """执行一轮定时清理：递归扫描过期文件 → 批量删除 → 更新 last_run。

        与监听任务一致：不调用 complete_task，任务保持 running（周期任务活性态）。
        删除受任务引用保护约束；空目录在清理后移除。
        """
        params = dict(task.params or {})
        keep_days = params.get("keep_days", 7)
        root = self._resolve_save_root(task)
        started_at = datetime.now(timezone.utc)

        # 任务引用保护（跳过被活跃任务引用的文件）。
        referenced = await self._task_manager.build_referenced_paths()
        expired = await self._file_manager.scan_expired_files(
            root,
            keep_days=keep_days,
            referenced_paths=referenced,
        )
        stats = await self._file_manager.delete_many(
            [f.path for f in expired],
            save_root=root,
            referenced_paths=referenced,
        )

        # 计算释放空间（按实际删除的文件大小累加）。
        result_by_path = {r["file_path"]: r for r in stats["results"]}
        freed_bytes = 0
        for fi in expired:
            r = result_by_path.get(fi.path)
            if r and r.get("deleted"):
                freed_bytes += fi.size

        finished_at = datetime.now(timezone.utc)
        params["last_run"] = {
            "scanned": len(expired),
            "deleted": stats["deleted"],
            "skipped": stats["skipped"],
            "failed": stats["failed"],
            "freed_bytes": freed_bytes,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "next_run_at": (params.get("last_run") or {}).get("next_run_at"),
        }
        task.params = params
        await self._task_manager._save_task(task)

        # 空目录清理。
        if params.get("remove_empty_dirs", True):
            self._remove_empty_dirs(root)

        log.info(
            f"定时清理完成: task={task.task_id} 扫描={len(expired)} "
            f"删除={stats['deleted']} 跳过={stats['skipped']} 释放={freed_bytes} 字节"
        )

    def _resolve_save_root(self, task: Task) -> str:
        """解析清理根目录：任务 scan_root 优先，否则取配置的下载根目录。"""
        root = (task.params or {}).get("scan_root")
        if root:
            return os.path.abspath(os.path.normpath(root))
        cm = self._config_manager or getattr(self._task_manager, "config_manager", None)
        save_dir = getattr(cm, "save_directory", None) or "downloads"
        return os.path.abspath(os.path.normpath(save_dir))

    @staticmethod
    def _remove_empty_dirs(root: str) -> None:
        """自底向上移除根目录下的空目录（不删除根目录本身）。"""
        root_abs = os.path.abspath(root)
        for dirpath, _dirnames, _filenames in os.walk(root_abs, topdown=False):
            if os.path.abspath(dirpath) == root_abs:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                continue

    async def execute_task(self, task: Task) -> None:
        """执行一个任务，根据任务类型分派到不同的执行器。

        Args:
            task: 要执行的任务
        """
        try:
            if task.task_type == TaskType.DOWNLOAD:
                await self._execute_download(task)
            elif task.task_type == TaskType.FORWARD:
                await self._execute_forward(task)
            elif task.task_type == TaskType.UPLOAD:
                await self._execute_upload(task)
            elif task.task_type == TaskType.LISTEN_DOWNLOAD:
                await self._execute_listen_download(task)
            elif task.task_type == TaskType.LISTEN_FORWARD:
                await self._execute_listen_forward(task)
            elif task.task_type == TaskType.CLEANUP_FILES:
                await self._execute_cleanup(task)
            else:
                raise ExecutorError(f"未知任务类型: {task.task_type}")

            # 常驻 running 型任务（监听/定时清理）为长期运行任务，不进入 completed 状态
            if task.task_type not in RESIDENT_RUNNING_TASK_TYPES:
                # 检查子任务执行结果：所有子任务都失败/跳过时标记为 FAILED
                # 重新加载任务以获取最新的子任务状态
                updated_task = await self._task_manager.get_task(task.task_id)
                if updated_task and updated_task.items:
                    all_failed_or_skipped = all(
                        item.status in (ItemStatus.FAILED, ItemStatus.SKIPPED)
                        for item in updated_task.items
                    )
                    if all_failed_or_skipped and len(updated_task.items) > 0:
                        await self._task_manager.fail_task(
                            task.task_id,
                            f"所有子任务均未成功（共 {len(updated_task.items)} 项）",
                        )
                        return

                await self._task_manager.complete_task(task.task_id)

        except asyncio.CancelledError:
            log.info(f"任务 {task.task_id} 被取消")
            await self._task_manager.cancel_task(task.task_id)
            raise

        except Exception as e:
            log.error(f"任务 {task.task_id} 执行失败: {e}")
            await self._task_manager.fail_task(task.task_id, str(e))

    async def _execute_listen_download(self, task: Task) -> None:
        """执行监听下载任务。注册 Handler 后保持 running 状态。

        Args:
            task: 监听下载任务
        """
        task_id = task.task_id

        async def _callback(
            client: pyrogram.Client, message: pyrogram.types.Message
        ) -> None:
            await self._handle_listen_download(task_id, client, message)

        await self._start_listener(task, _callback)
        log.info(f"监听下载任务 {task.task_id} Handler 已注册, chat_id={task.chat_id}")

    async def _execute_listen_forward(self, task: Task) -> None:
        """执行监听转发任务。注册 Handler 后保持 running 状态。

        Args:
            task: 监听转发任务
        """
        task_id = task.task_id

        async def _callback(
            client: pyrogram.Client, message: pyrogram.types.Message
        ) -> None:
            await self._handle_listen_forward(task_id, client, message)

        await self._start_listener(task, _callback)
        log.info(f"监听转发任务 {task.task_id} Handler 已注册, chat_id={task.chat_id}")

    async def _start_listener(self, task: Task, callback: Callable) -> None:
        """注册监听 Handler 到 User Client。

        Args:
            task: 监听任务
            callback: 消息回调函数
        """
        chat_id = task.chat_id
        if not chat_id:
            raise ExecutorError(f"监听任务 {task.task_id} 缺少 chat_id")

        # 幂等检查：如果已有 handler，先移除再重新注册
        existing = task.extra.get("_handler")
        if existing is not None:
            try:
                self._client.remove_handler(existing)
            except Exception:
                pass

        handler = MessageHandler(callback, filters=pyrogram.filters.chat(chat_id))
        self._client.add_handler(handler)
        task.extra["_handler"] = handler

    async def _stop_listener(self, task: Task) -> None:
        """移除监听 Handler 并清理引用。

        幂等操作：多次调用不会报错。

        Args:
            task: 监听任务
        """
        handler = task.extra.pop("_handler", None)
        if handler:
            try:
                self._client.remove_handler(handler)
                log.info(
                    f"监听任务 {task.task_id} Handler 已移除, chat_id={task.chat_id}"
                )
            except Exception as e:
                log.warning(f"移除监听任务 {task.task_id} Handler 失败: {e}")

    async def cancel_listen_task(self, task_id: str) -> None:
        """取消监听任务：先移除 Handler，再更新任务状态。

        Args:
            task_id: 任务 ID
        """
        task = await self._task_manager.get_task(task_id)
        if task is None:
            log.warning(f"取消监听任务失败：任务 {task_id} 不存在")
            return

        await self._stop_listener(task)
        await self._task_manager.cancel_task(task_id)

    async def recover_listeners(self) -> None:
        """恢复所有 running 状态的监听任务 Handler。

        应用重启后调用，遍历数据库中所有 running 状态的 LISTEN_* 任务，
        重新注册 MessageHandler。恢复失败的任务标记为 failed。
        """
        # 查询所有 running 状态的监听任务
        all_tasks: list[Task] = []
        for task_type in LISTEN_TASK_TYPES:
            tasks, _ = await self._task_manager.list_tasks(
                task_type=task_type, status=TaskStatus.RUNNING
            )
            all_tasks.extend(tasks)

        if not all_tasks:
            log.info("没有需要恢复的监听任务")
            return

        log.info(f"开始恢复 {len(all_tasks)} 个监听任务")

        for task in all_tasks:
            try:
                if task.task_type == TaskType.LISTEN_DOWNLOAD:
                    task_id = task.task_id

                    async def _dl_callback(
                        client: pyrogram.Client, message: pyrogram.types.Message
                    ) -> None:
                        await self._handle_listen_download(task_id, client, message)

                    await self._start_listener(task, _dl_callback)
                elif task.task_type == TaskType.LISTEN_FORWARD:
                    task_id = task.task_id

                    async def _fw_callback(
                        client: pyrogram.Client, message: pyrogram.types.Message
                    ) -> None:
                        await self._handle_listen_forward(task_id, client, message)

                    await self._start_listener(task, _fw_callback)

                log.info(f"监听任务 {task.task_id} 已恢复, chat_id={task.chat_id}")
            except Exception as e:
                log.error(f"恢复监听任务 {task.task_id} 失败: {e}")
                await self._task_manager.fail_task(task.task_id, f"恢复失败: {e}")

        log.info(f"监听任务恢复完成: 共 {len(all_tasks)} 个")

    async def _handle_listen_download(
        self, task_id: str, client: pyrogram.Client, message: pyrogram.types.Message
    ) -> None:
        """监听下载回调：收到新消息时触发下载。

        执行流程：
        1. 检查消息是否有媒体（无媒体跳过）
        2. 应用 media_types 过滤
        3. 消息去重（source_id == message.id）
        4. 创建 TaskItem 并持久化
        5. 执行下载并更新状态

        Args:
            task_id: 监听任务 ID
            client: Pyrogram Client
            message: 新消息对象
        """
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return

        # 检查媒体
        if not message.media:
            return

        # 媒体类型过滤
        media_types = (
            task.params.get("media_types") or task.params.get("filter_types") or []
        )
        if media_types:
            media_type = self._get_media_type(message)
            if media_type and media_type not in media_types:
                return

        # 去重检查
        source_id = message.id
        for item in task.items:
            if item.source_message_id == source_id:
                return  # 已处理过

        # 创建 TaskItem 并持久化
        item_id = f"{task_id}_msg_{source_id}"
        item = self._create_item(task, item_id, message_id=source_id)
        await self._task_manager.add_items(task_id, [item])

        # 执行下载
        chat_id = task.chat_id
        try:
            await self._task_manager.update_item_status(
                task_id, item_id, ItemStatus.RUNNING
            )

            # 提取元数据
            file_unique_id = self._extract_file_unique_id(message)
            telegram_file_id = self._extract_telegram_file_id(message)

            # L2 去重检查
            if self._should_use_repository() and file_unique_id:
                assert self._repository_manager is not None
                dedup = await self._repository_manager.check_dedup(
                    source_chat_id=chat_id,
                    source_message_id=source_id,
                    file_unique_id=file_unique_id,
                )
                if dedup:
                    await self._task_manager.update_item_status(
                        task_id,
                        item_id,
                        ItemStatus.SKIPPED,
                        error_code="DUPLICATE_IN_REPOSITORY",
                        error_message="文件已在仓库中存在，跳过下载",
                    )
                    return

            # 下载文件
            if self._downloader:
                downloaded = await self._downloader.download_range(
                    chat_id=chat_id,
                    start_id=source_id,
                    end_id=source_id,
                    task_id=task_id,
                    progress_callback=self._on_item_progress,
                )
                if downloaded:
                    await self._task_manager.update_item_status(
                        task_id,
                        item_id,
                        ItemStatus.SUCCESS,
                        file_unique_id=file_unique_id,
                        telegram_file_id=telegram_file_id,
                    )
                    # 下载成功后入库到仓库
                    await self._ingest_downloaded_item(task_id, item, chat_id)
                else:
                    await self._task_manager.update_item_status(
                        task_id,
                        item_id,
                        ItemStatus.FAILED,
                        error_code="DOWNLOAD_FAILED",
                        error_message="下载失败",
                    )
            else:
                # 降级路径：无 downloader 时无法实际下载文件，
                # 标记为 SKIPPED 而非 SUCCESS（避免假成功）
                await self._task_manager.update_item_status(
                    task_id,
                    item_id,
                    ItemStatus.SKIPPED,
                    error_code="NO_DOWNLOADER",
                    error_message="无下载器，无法实际下载文件",
                    file_unique_id=file_unique_id,
                    telegram_file_id=telegram_file_id,
                )
        except Exception as e:
            log.error(f"监听下载任务 {task_id} 处理消息 {source_id} 失败: {e}")
            await self._task_manager.update_item_status(
                task_id,
                item_id,
                ItemStatus.FAILED,
                error_code="EXECUTION_ERROR",
                error_message=str(e),
            )

    async def _handle_listen_forward(
        self, task_id: str, client: pyrogram.Client, message: pyrogram.types.Message
    ) -> None:
        """监听转发回调：收到新消息时触发转发。

        执行流程：
        1. 检查消息是否有媒体（无媒体跳过）
        2. 应用 media_types 过滤
        3. 消息去重（source_id == message.id）
        4. 创建 TaskItem 并持久化
        5. 执行转发并更新状态

        Args:
            task_id: 监听任务 ID
            client: Pyrogram Client
            message: 新消息对象
        """
        task = await self._task_manager.get_task(task_id)
        if task is None:
            return

        # 检查媒体
        if not message.media:
            return

        # 媒体类型过滤
        media_types = (
            task.params.get("media_types") or task.params.get("filter_types") or []
        )
        if media_types:
            media_type = self._get_media_type(message)
            if media_type and media_type not in media_types:
                return

        # 去重检查
        source_id = message.id
        for item in task.items:
            if item.source_message_id == source_id:
                return  # 已处理过

        # 创建 TaskItem 并持久化
        item_id = f"{task_id}_msg_{source_id}"
        item = self._create_item(task, item_id, message_id=source_id)
        await self._task_manager.add_items(task_id, [item])

        # 执行转发
        chat_id = task.chat_id
        target_chat_id = task.params.get("target_chat_id")
        try:
            await self._task_manager.update_item_status(
                task_id, item_id, ItemStatus.RUNNING
            )

            # L2 去重检查（仓库模式）
            if self._should_use_repository() and target_chat_id:
                assert self._repository_manager is not None
                file_unique_id = self._extract_file_unique_id(message)
                if file_unique_id:
                    dedup = await self._repository_manager.check_dedup(
                        source_chat_id=chat_id,
                        source_message_id=source_id,
                        file_unique_id=file_unique_id,
                    )
                    if dedup:
                        # 仓库已有，尝试从仓库分发
                        target_msg_id = (
                            await self._repository_manager.distribute_to_target(
                                client=client,
                                file_unique_id=file_unique_id,
                                target_chat_id=target_chat_id,
                            )
                        )
                        if target_msg_id:
                            await self._task_manager.update_item_status(
                                task_id,
                                item_id,
                                ItemStatus.SUCCESS,
                                target_chat_id=target_chat_id,
                                uploaded_message_id=target_msg_id,
                            )
                            return

            # 仓库中转模式：先复制到仓库频道，再从仓库分发
            if self._should_use_repository():
                repo_chat_id = self._repository_manager.get_repository_chat_id()
                file_unique_id = self._extract_file_unique_id(message)

                if file_unique_id:
                    # 复制到仓库频道
                    repo_msg = await client.copy_message(
                        chat_id=int(repo_chat_id),
                        from_chat_id=chat_id,
                        message_id=source_id,
                    )
                    # 写入仓库记录
                    await self._repository_manager.on_upload_success(
                        message=repo_msg,
                        source_chat_id=chat_id,
                        source_message_id=source_id,
                    )
                    # 从仓库分发到目标频道
                    target_msg_id = await self._repository_manager.distribute_to_target(
                        client=client,
                        file_unique_id=file_unique_id,
                        target_chat_id=target_chat_id,
                    )
                    if target_msg_id:
                        await self._task_manager.update_item_status(
                            task_id,
                            item_id,
                            ItemStatus.SUCCESS,
                            target_chat_id=target_chat_id,
                            uploaded_message_id=target_msg_id,
                        )
                        return
                    # 分发失败，降级直接转发

            # 直接转发到目标频道（非仓库模式或降级）
            result_message = await client.copy_message(
                chat_id=target_chat_id,
                from_chat_id=chat_id,
                message_id=source_id,
            )
            await self._task_manager.update_item_status(
                task_id,
                item_id,
                ItemStatus.SUCCESS,
                target_chat_id=target_chat_id,
                uploaded_message_id=result_message.id,
            )
        except Exception as e:
            log.error(f"监听转发任务 {task_id} 处理消息 {source_id} 失败: {e}")
            await self._task_manager.update_item_status(
                task_id,
                item_id,
                ItemStatus.FAILED,
                error_code="EXECUTION_ERROR",
                error_message=str(e),
            )

    async def _resolve_message_ids(self, task: Task) -> list[int]:
        """根据任务 params 中的 range_mode 解析消息 ID 列表。

        支持的模式：
        - id_range: 根据 min_id/max_id 生成连续 ID 列表
        - multiple_ids: 直接返回 message_list 中的消息 ID
        - date_range: 通过 Telegram API 按日期范围获取消息 ID
        - all: 通过 Telegram API 遍历频道所有消息获取 ID
        - recent: 获取最近 N 条消息 ID
        """
        range_mode = task.params.get("range_mode", "id_range")

        if range_mode == "multiple_ids":
            message_ids = task.params.get("message_list") or task.params.get(
                "message_ids", []
            )
            if not message_ids:
                raise ExecutorError(
                    f"任务 {task.task_id} multiple_ids 模式缺少 message_list 参数"
                )
            # 解析消息 ID（支持纯数字和链接格式）
            return self._parse_message_id_list(message_ids)

        elif range_mode == "date_range":
            return await self._resolve_date_range_ids(task)

        elif range_mode == "all":
            return await self._resolve_all_ids(task)

        elif range_mode == "recent":
            return await self._resolve_recent_ids(task)

        # id_range 模式（默认）
        start = task.params.get("min_id") or task.params.get("message_range_start")
        end = task.params.get("max_id") or task.params.get("message_range_end")
        if start is None:
            raise ExecutorError(
                f"任务 {task.task_id} id_range 模式缺少消息范围参数（min_id/max_id）"
            )
        return list(range(int(start), (int(end) if end else int(start)) + 1))

    async def _resolve_date_range_ids(self, task: Task) -> list[int]:
        """通过 Telegram API 按日期范围获取消息 ID 列表。

        使用 client.get_chat_history() 遍历指定日期范围内的消息，收集其 ID。
        """
        chat_id = task.chat_id
        start_date_str = task.params.get("start_date")
        end_date_str = task.params.get("end_date")

        if not start_date_str or not end_date_str:
            raise ExecutorError(
                f"任务 {task.task_id} date_range 模式缺少 start_date/end_date 参数"
            )

        try:
            start_date = parse_user_date(start_date_str, is_end=False)
            end_date = parse_user_date(end_date_str, is_end=True)
        except ValueError as e:
            raise ExecutorError(f"任务 {task.task_id} 日期格式无效: {e}")

        message_ids = []
        try:
            async for message in self._client.get_chat_history(
                chat_id,
                offset_date=end_date,
            ):
                if message.date:
                    # 统一时区：Pyrogram 部分消息日期可能是 naive，按 UTC 处理
                    msg_date = (
                        message.date.replace(tzinfo=timezone.utc)
                        if message.date.tzinfo is None
                        else message.date.astimezone(timezone.utc)
                    )
                    if msg_date < start_date:
                        break
                message_ids.append(message.id)
        except Exception as e:
            raise ExecutorError(f"任务 {task.task_id} 获取日期范围内消息失败: {e}")

        if not message_ids:
            raise ExecutorError(
                f"任务 {task.task_id}: 日期范围 {start_date_str} ~ {end_date_str} 内未找到消息"
            )

        return message_ids

    async def _resolve_all_ids(self, task: Task) -> list[int]:
        """通过 Telegram API 遍历频道所有消息获取 ID 列表。

        使用 client.get_chat_history() 遍历频道的完整消息历史。
        对于大频道，每10000条消息记录一次进度日志。
        """
        chat_id = task.chat_id
        message_ids = []
        count = 0

        try:
            async for message in self._client.get_chat_history(chat_id):
                message_ids.append(message.id)
                count += 1
                if count % 10000 == 0:
                    log.info(f"任务 {task.task_id}: 已获取 {count} 条消息 ID...")
        except Exception as e:
            raise ExecutorError(f"任务 {task.task_id} 获取频道所有消息失败: {e}")

        if not message_ids:
            raise ExecutorError(f"任务 {task.task_id}: 频道 {chat_id} 内未找到消息")

        log.info(
            f"任务 {task.task_id}: 频道 {chat_id} 共获取 {len(message_ids)} 条消息 ID"
        )

        return message_ids

    async def _resolve_recent_ids(self, task: Task) -> list[int]:
        """获取最近 N 条消息 ID 列表。

        使用 client.get_chat_history(chat_id, limit=recent_count) 获取消息。
        recent_count 已在 TaskManager 中截断至 1000。
        """
        chat_id = task.chat_id
        recent_count = task.params.get("recent_count")
        if not recent_count or recent_count <= 0:
            raise ExecutorError(
                f"任务 {task.task_id} recent 模式缺少有效的 recent_count 参数"
            )

        message_ids = []
        try:
            async for message in self._client.get_chat_history(
                chat_id, limit=int(recent_count)
            ):
                message_ids.append(message.id)
        except Exception as e:
            raise ExecutorError(f"任务 {task.task_id} 获取最近消息失败: {e}")

        if not message_ids:
            raise ExecutorError(f"任务 {task.task_id}: 频道 {chat_id} 内未找到消息")

        return message_ids

    @staticmethod
    def _parse_message_id_list(items: list) -> list[int]:
        """解析消息 ID 列表，支持纯数字和链接格式。

        Args:
            items: 包含消息 ID 或消息链接的列表

        Returns:
            解析后的整数消息 ID 列表
        """
        import re

        ids = []
        for item in items:
            item_str = str(item).strip()
            if not item_str:
                continue
            # 支持格式: https://t.me/channel/123 或 t.me/channel/123 或 纯数字
            match = re.search(r"/(\d+)$", item_str)
            if match:
                ids.append(int(match.group(1)))
            elif item_str.isdigit():
                ids.append(int(item_str))
        return ids

    @staticmethod
    def _get_message_file_size(message) -> Optional[int]:
        """获取消息媒体文件大小（字节），无媒体返回 None。"""
        if not message or not message.media:
            return None
        # 直接访问 message 的属性，而不是 message.media（枚举值）的属性
        for attr in ("video", "document", "audio", "animation", "voice", "video_note"):
            obj = getattr(message, attr, None)
            if obj:
                return getattr(obj, "file_size", None)
        if message.photo:
            # Photo 大小取最大尺寸
            sizes = getattr(message.photo, "sizes", [])
            if sizes:
                return getattr(sizes[-1], "file_size", None)
        return None

    def _filter_media_messages_by_criteria(
        self, task: Task, messages: list
    ) -> list[int]:
        """根据媒体类型与文件大小过滤消息，返回通过过滤的消息 ID 列表。

        过滤条件读取 task.params：
        - media_types: 允许的媒体类型列表，为空时不过滤类型。
        - min_size / max_size: 文件大小字节范围，为 None 时不限制。

        向后兼容：若 params 中仍使用旧字段 filter_types，则作为 media_types 的 fallback。
        """
        params = task.params
        media_types = params.get("media_types") or params.get("filter_types") or []
        min_size = params.get("min_size")
        max_size = params.get("max_size")

        if not media_types and min_size is None and max_size is None:
            return [msg.id for msg in messages if msg]

        result = []
        for message in messages:
            if not message:
                continue
            media_type = self._get_media_type(message)
            if media_types and media_type not in media_types:
                continue
            if message.media:
                file_size = self._get_message_file_size(message)
                if file_size is not None:
                    if min_size is not None and file_size < min_size:
                        continue
                    if max_size is not None and file_size > max_size:
                        continue
            result.append(message.id)
        return result

    async def _apply_media_filter(
        self, task: Task, message_ids: list[int]
    ) -> list[int]:
        """如有过滤条件，获取消息对象并应用媒体/大小过滤。"""
        params = task.params
        media_types = params.get("media_types") or params.get("filter_types")
        min_size = params.get("min_size")
        max_size = params.get("max_size")
        if not media_types and min_size is None and max_size is None:
            return message_ids

        chat_id = task.chat_id
        try:
            messages = await asyncio.gather(
                *[self._client.get_messages(chat_id, msg_id) for msg_id in message_ids]
            )
            return self._filter_media_messages_by_criteria(task, messages)
        except Exception as e:
            log.error(f"获取消息失败: {e}")
            raise

    async def _execute_download(self, task: Task) -> None:
        """执行下载任务。"""
        chat_id = task.chat_id
        message_ids = await self._resolve_message_ids(task)
        message_ids = await self._apply_media_filter(task, message_ids)
        filter_types = task.params.get("filter_types", [])
        downloaded_files: list[str] = []

        if not message_ids:
            raise ExecutorError(
                f"下载任务 {task.task_id} 缺少消息范围参数（message_range）"
            )

        # 如果已有下载器，调用其下载方法
        if self._downloader:
            # 预先创建子任务项并持久化到数据库，确保 progress_callback
            # 能正确更新子任务状态（download_range 内部会调用 _on_item_progress）
            if not task.items:
                new_items = []
                for msg_id in message_ids:
                    item_id = f"{task.task_id}_msg_{msg_id}"
                    new_items.append(
                        self._create_item(task, item_id, message_id=msg_id)
                    )
                await self._task_manager.add_items(task.task_id, new_items)

            (
                downloaded_files,
                processing_results,
            ) = await self._downloader.download_range(
                chat_id=chat_id,
                start_id=message_ids[0],
                end_id=message_ids[-1],
                task_id=task.task_id,
                progress_callback=self._on_item_progress,
                message_ids=message_ids,
            )

            # 修复：根据 processing_results 更新子任务状态
            # 这种情况发生在 progress_callback 执行失败时
            await self._finalize_pending_items(task, processing_results)
        else:
            # 降级方案：手动下载（并发控制）
            if not task.items:
                new_items = []
                for msg_id in message_ids:
                    item_id = f"{task.task_id}_msg_{msg_id}"
                    new_items.append(
                        self._create_item(task, item_id, message_id=msg_id)
                    )
                await self._task_manager.add_items(task.task_id, new_items)

            # 并发下载
            async def _download_one(item):
                if item.status in (
                    ItemStatus.SUCCESS,
                    ItemStatus.SKIPPED,
                    ItemStatus.FAILED,
                ):
                    return
                async with self._download_semaphore:
                    await self._task_manager.update_item_status(
                        task.task_id, item.id, ItemStatus.RUNNING
                    )
                    try:
                        message = await self._client.get_messages(
                            chat_id, item.source_message_id
                        )
                        if message and message.media:
                            # 提取 media_group_id（相册模式分组所需）
                            media_group_id = getattr(message, "media_group_id", None)
                            if media_group_id:
                                await self._task_manager.update_item_status(
                                    task.task_id,
                                    item.id,
                                    item.status,
                                    media_group_id=media_group_id,
                                )

                            if filter_types:
                                media_type = self._get_media_type(message)
                                if media_type and media_type not in filter_types:
                                    await self._task_manager.update_item_status(
                                        task.task_id, item.id, ItemStatus.SKIPPED
                                    )
                                    return

                            # 提取 file_unique_id 和 telegram_file_id
                            file_unique_id = self._extract_file_unique_id(message)
                            telegram_file_id = self._extract_telegram_file_id(message)

                            # L2 去重检查
                            if self._should_use_repository() and file_unique_id:
                                dedup = await self._repository_manager.check_dedup(
                                    source_chat_id=chat_id,
                                    source_message_id=item.source_message_id,
                                    file_unique_id=file_unique_id,
                                )
                                if dedup:
                                    await self._task_manager.update_item_status(
                                        task.task_id,
                                        item.id,
                                        ItemStatus.SKIPPED,
                                        error_code="DUPLICATE_IN_REPOSITORY",
                                        error_message="文件已在仓库中存在，跳过下载",
                                    )
                                    return

                            # 降级路径：无 downloader 时无法实际下载文件，
                            # 标记为 SKIPPED 而非 SUCCESS（避免假成功）
                            await self._task_manager.update_item_status(
                                task.task_id,
                                item.id,
                                ItemStatus.SKIPPED,
                                error_code="NO_DOWNLOADER",
                                error_message="无下载器，无法实际下载文件",
                                file_unique_id=file_unique_id,
                                telegram_file_id=telegram_file_id,
                            )
                        else:
                            await self._task_manager.update_item_status(
                                task.task_id,
                                item.id,
                                ItemStatus.FAILED,
                                error_code="MESSAGE_NOT_FOUND",
                                error_message="消息未找到或不含媒体文件",
                            )
                    except Exception as e:
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item.id,
                            ItemStatus.FAILED,
                            error_code="EXECUTION_ERROR",
                            error_message=str(e),
                        )

            await asyncio.gather(*[_download_one(item) for item in task.items])

        # 仓库入库：上传到仓库频道并写入记录（无论是否有 downloader 都执行）
        await self._ingest_downloaded_files(task)

        # 保存已下载的文件路径到任务（转为可移植格式存储）
        if downloaded_files:
            save_root = self._get_save_root()
            portable_paths = [to_portable_path(f, save_root) for f in downloaded_files]
            await self._task_manager.update_file_paths(task.task_id, portable_paths)

    async def _finalize_pending_items(
        self, task: Task, processing_results: dict[int, dict]
    ) -> None:
        """检查并更新仍处于 PENDING 状态的子任务。

        当 progress_callback 执行失败时，子任务可能仍保持 PENDING 状态。
        此方法根据 processing_results 中的处理结果来更新子任务状态。

        Args:
            task: 任务对象
            processing_results: 消息ID到处理结果的映射字典
                格式: {msg_id: {"status": ItemStatus, "file_path": str|None, "error": str|None}}
        """
        if not processing_results:
            return

        pending_items = [
            item for item in task.items if item.status == ItemStatus.PENDING
        ]
        if not pending_items:
            return

        fixed_count = 0
        for item in pending_items:
            msg_id = item.source_message_id
            if msg_id not in processing_results:
                # 这个消息ID没有被处理，标记为失败
                await self._task_manager.update_item_status(
                    task.task_id,
                    item.id,
                    ItemStatus.FAILED,
                    error_code="NOT_PROCESSED",
                    error_message="消息未被处理",
                )
                fixed_count += 1
                continue

            result = processing_results[msg_id]
            expected_status = result["status"]

            # 根据处理结果更新子任务状态
            if expected_status == ItemStatus.SUCCESS:
                # 传递 file_path 以确保持久化到 TaskItem（转为可移植格式）
                extra_kwargs = {}
                result_file_path = result.get("file_path")
                if result_file_path:
                    extra_kwargs["file_path"] = to_portable_path(
                        result_file_path, self._get_save_root()
                    )
                await self._task_manager.update_item_status(
                    task.task_id, item.id, ItemStatus.SUCCESS, **extra_kwargs
                )
                fixed_count += 1
            elif expected_status == ItemStatus.FAILED:
                error_msg = result.get("error", "下载失败")
                await self._task_manager.update_item_status(
                    task.task_id,
                    item.id,
                    ItemStatus.FAILED,
                    error_code="DOWNLOAD_FAILED",
                    error_message=error_msg,
                )
                fixed_count += 1
            elif expected_status == ItemStatus.SKIPPED:
                error_msg = result.get("error", "跳过")
                await self._task_manager.update_item_status(
                    task.task_id, item.id, ItemStatus.SKIPPED, error_message=error_msg
                )
                fixed_count += 1

        if fixed_count > 0:
            log.info(
                f"任务 {task.task_id}: 修复了 {fixed_count} 个 PENDING 状态的子任务"
            )

    async def _ingest_downloaded_files(self, task: Task) -> None:
        """下载完成后，将文件入库到仓库频道（PRD §2.2.1 步骤7-10）。

        仅在仓库模式启用且 preference.upload.download_upload=True 时执行。
        按 source_message_id 分组，使用相册模式上传以保持源频道消息结构。
        """
        if not self._should_use_repository():
            log.info(f"下载入库: 仓库模式未启用，跳过入库 task={task.task_id}")
            return

        # 读取 download_upload 配置
        repo_config = self._config_manager.load_config() if self._config_manager else {}
        upload_config = repo_config.get("preference", {}).get("upload", {})
        download_upload = upload_config.get("download_upload", True)
        if not download_upload:
            log.info(f"下载入库: download_upload=False，跳过入库 task={task.task_id}")
            return

        log.info(
            f"下载入库: 开始处理 task={task.task_id}, "
            f"items={len(task.items)}, repo_chat_id={self._repository_manager.get_repository_chat_id()}"
        )

        chat_id = task.chat_id
        repo_chat_id = self._repository_manager.get_repository_chat_id()
        delete_after = upload_config.get("delete", False)
        save_root = self._get_save_root()

        # 按 media_group_id 分组（相册）+ 按 source_id 分组（单文件消息）
        groups: dict[str, list[TaskItem]] = {}
        for item in task.items:
            if item.status != ItemStatus.SUCCESS or not item.file_path:
                continue

            mg_id = item.media_group_id
            # 统一转为字符串，空字符串视为 None
            if mg_id is not None:
                mg_id = str(mg_id).strip()
                if mg_id == "":
                    mg_id = None

            if mg_id:
                # 有 media_group_id：按 media_group_id 分组（相册）
                group_key = f"mg:{mg_id}"
            else:
                # 无 media_group_id：按 source_id 分组（单文件消息）
                source_id = item.source_message_id or 0
                group_key = f"sg:{source_id}"

            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(item)

        # 逐组处理
        for group_key, items in groups.items():
            # 从 group_key 提取 media_group_id 和 source_message_id
            if group_key.startswith("mg:"):
                mg_id = group_key[3:]
                source_message_id = items[0].source_message_id or 0
            else:
                mg_id = None
                source_message_id = int(group_key[3:]) if len(group_key) > 3 else 0

            await self._ingest_downloaded_group(
                task=task,
                items=items,
                chat_id=chat_id,
                repo_chat_id=repo_chat_id,
                source_message_id=source_message_id,
                delete_after=delete_after,
                save_root=save_root,
                media_group_id=mg_id,
            )

    async def _ingest_downloaded_group(
        self,
        task: Task,
        items: list[TaskItem],
        chat_id: int,
        repo_chat_id: str,
        source_message_id: int,
        delete_after: bool,
        save_root: str,
        media_group_id: Optional[str] = None,
    ) -> None:
        """处理同一组文件入库（相册按 media_group_id 分组，单文件按 source_id 分组）。

        流程：
        1. 计算每个文件的 SHA256，执行 L3 去重
        2. 构建 FileInfo 列表
        3. 调用 split_media_group 分为 album 组和 single 组
        4. album 组（>1 文件）→ upload_media_group
        5. single 组 → 并发 upload
        6. 对上传成功的消息，调用 on_upload_success_batch 写入仓库记录
        7. 更新各 item 状态
        """
        # 步骤1: 去重 + 构建 FileInfo
        file_infos: list[FileInfo] = []
        item_map: dict[str, tuple[TaskItem, str]] = {}  # file_path -> (item, sha256)
        dedup_items: list[tuple[TaskItem, str]] = []  # (item, sha256) 去重命中的

        for item in items:
            abs_file_path = from_portable_path(item.file_path, save_root)
            file_sha256 = self._repository_manager.compute_content_hash(abs_file_path)
            dedup = await self._repository_manager.check_dedup(
                source_chat_id=chat_id,
                source_message_id=source_message_id,
                content_hash=file_sha256,
            )

            if dedup:
                log.info(
                    f"下载入库: L3去重命中，跳过上传 item={item.id} "
                    f"file_unique_id={dedup.file_unique_id}"
                )
                await self._task_manager.update_item_status(
                    task.task_id, item.id, item.status, file_sha256=file_sha256
                )
                dedup_items.append((item, file_sha256))
                continue

            file_info = await self._file_manager.get_file_info(abs_file_path)
            file_infos.append(file_info)
            item_map[abs_file_path] = (item, file_sha256)

        if not file_infos:
            log.info(
                f"下载入库: 组 source_message_id={source_message_id} 全部去重命中，跳过上传"
            )
            return

        # 步骤2: 拆分为 album 组和 single 组
        groups = await self._file_manager.split_media_group(file_infos)

        for group in groups:
            is_album = group.get("is_album", False)
            group_files = group.get("files", [])

            if is_album and len(group_files) > 1:
                # 相册模式上传
                await self._upload_album_group(
                    task=task,
                    file_infos=group_files,
                    item_map=item_map,
                    chat_id=chat_id,
                    repo_chat_id=repo_chat_id,
                    source_message_id=source_message_id,
                    delete_after=delete_after,
                    media_group_id=media_group_id,
                )
            else:
                # 单文件上传
                await self._upload_single_files(
                    task=task,
                    file_infos=group_files,
                    item_map=item_map,
                    chat_id=chat_id,
                    repo_chat_id=repo_chat_id,
                    source_message_id=source_message_id,
                    delete_after=delete_after,
                    media_group_id=media_group_id,
                )

    async def _upload_album_group(
        self,
        task: Task,
        file_infos: list[FileInfo],
        item_map: dict[str, tuple[TaskItem, str]],
        chat_id: int,
        repo_chat_id: str,
        source_message_id: int,
        delete_after: bool,
        media_group_id: Optional[str] = None,
    ) -> None:
        """相册模式上传一组文件到仓库频道。"""
        log.info(
            f"下载入库: 相册模式上传 {len(file_infos)} 个文件 "
            f"media_group_id={media_group_id} source_message_id={source_message_id}"
        )

        try:
            results = await self._file_manager.upload_media_group(
                file_infos=file_infos,
                chat_id=int(repo_chat_id),
                delete_after=delete_after,
            )

            # 收集成功的消息和哈希
            success_messages = []
            success_hashes = []
            success_source_ids = []
            for res in results:
                if res.success and res.message:
                    item, sha256 = item_map.get(res.file_path, (None, None))
                    if item:
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item.id,
                            item.status,
                            file_sha256=sha256,
                            file_unique_id=res.file_unique_id,
                        )
                        log.info(f"下载入库: 相册上传成功 file_path={res.file_path}")
                        success_messages.append(res.message)
                        success_hashes.append(sha256)
                        # 相册中每个文件记录其独立的 source_message_id
                        success_source_ids.append(item.source_message_id or 0)
                    else:
                        log.warning(
                            f"下载入库: 相册上传成功但找不到对应 item file_path={res.file_path}"
                        )
                else:
                    # 找到对应的 item 标记失败
                    item, _ = item_map.get(res.file_path, (None, None))
                    if item:
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item.id,
                            ItemStatus.FAILED,
                            error_code="UPLOAD_ERROR",
                            error_message=res.error_msg or "UNKNOWN_ERROR",
                        )
                    log.warning(f"下载入库: 相册上传失败 file_path={res.file_path}")

            # 批量写入仓库记录
            if success_messages:
                await self._repository_manager.on_upload_success_batch(
                    messages=success_messages,
                    source_chat_id=chat_id,
                    source_message_ids=success_source_ids,
                    content_hashes=success_hashes,
                )

        except Exception as e:
            log.warning(f"下载入库: 相册上传异常: {e}")
            # 标记所有 item 为失败
            for fi in file_infos:
                item, _ = item_map.get(fi.path, (None, None))
                if item:
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item.id,
                        ItemStatus.FAILED,
                        error_code="UPLOAD_ERROR",
                        error_message=str(e),
                    )

    async def _upload_single_files(
        self,
        task: Task,
        file_infos: list[FileInfo],
        item_map: dict[str, tuple[TaskItem, str]],
        chat_id: int,
        repo_chat_id: str,
        source_message_id: int,
        delete_after: bool,
        media_group_id: Optional[str] = None,
    ) -> None:
        """单文件模式上传文件到仓库频道。"""
        for fi in file_infos:
            item, sha256 = item_map.get(fi.path, (None, None))
            if not item:
                log.warning(f"下载入库: 找不到对应 item file_path={fi.path}")
                continue

            try:
                result = await self._file_manager.upload(
                    file_path=fi.path,
                    chat_id=int(repo_chat_id),
                    source_chat_id=chat_id,
                    source_message_id=item.source_message_id
                    if item.source_message_id
                    else source_message_id,
                    content_hash=sha256,
                    delete_after=delete_after,
                )
                if result.success and result.message:
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item.id,
                        item.status,
                        file_sha256=sha256,
                        file_unique_id=result.file_unique_id,
                    )
                    log.info(f"下载入库: 单文件上传成功 file_path={item.file_path}")
                else:
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item.id,
                        ItemStatus.FAILED,
                        error_code="UPLOAD_ERROR",
                        error_message=result.error_msg or "UNKNOWN_ERROR",
                    )
                    log.warning(f"下载入库: 单文件上传失败 file_path={item.file_path}")
            except Exception as e:
                await self._task_manager.update_item_status(
                    task.task_id,
                    item.id,
                    ItemStatus.FAILED,
                    error_code="UPLOAD_ERROR",
                    error_message=str(e),
                )
                log.warning(f"下载入库: 单文件上传异常: {e}")

    async def _ingest_downloaded_item(
        self, task_id: str, item: TaskItem, chat_id: int
    ) -> None:
        """单条子任务的仓库入库（用于监听下载等逐条处理场景）。"""
        if not self._should_use_repository() or not item.file_path:
            return

        upload_config = (
            (self._config_manager.load_config() or {})
            .get("preference", {})
            .get("upload", {})
        )
        if not upload_config.get("download_upload", True):
            return

        save_root = self._get_save_root()
        abs_file_path = from_portable_path(item.file_path, save_root)
        file_sha256 = self._repository_manager.compute_content_hash(abs_file_path)
        dedup = await self._repository_manager.check_dedup(
            source_chat_id=chat_id,
            source_message_id=item.source_message_id,
            content_hash=file_sha256,
        )

        if dedup:
            await self._task_manager.update_item_status(
                task_id, item.id, item.status, file_sha256=file_sha256
            )
            return

        repo_chat_id = self._repository_manager.get_repository_chat_id()
        delete_after = upload_config.get("delete", False)

        try:
            result = await self._file_manager.upload(
                file_path=abs_file_path,
                chat_id=int(repo_chat_id),
                source_chat_id=chat_id,
                source_message_id=item.source_message_id,
                content_hash=file_sha256,
                delete_after=delete_after,
            )
            if result.success:
                await self._task_manager.update_item_status(
                    task_id,
                    item.id,
                    item.status,
                    file_sha256=file_sha256,
                    file_unique_id=result.file_unique_id,
                )
                if delete_after:
                    self._file_manager.delete_local_file(abs_file_path)
        except Exception as e:
            log.warning(f"监听下载入库失败: {e}")

    async def _execute_forward(self, task: Task) -> None:
        """执行转发任务。"""
        chat_id = task.chat_id
        target_chat_id = task.params.get("target_chat_id")
        filter_types = task.params.get("filter_types", [])

        message_ids = await self._resolve_message_ids(task)
        message_ids = await self._apply_media_filter(task, message_ids)

        if not message_ids:
            raise ExecutorError(
                f"转发任务 {task.task_id} 没有可转发的消息（消息范围无效或全部被过滤）"
            )

        log.info(
            f"转发任务: {task.task_id}, chat_id={chat_id}, "
            f"target={target_chat_id}, filter={filter_types}, "
            f"msg_count={len(message_ids)}, existing_items={len(task.items)}"
        )

        # 创建子任务项并持久化到数据库
        if not task.items:
            new_items = []
            for msg_id in message_ids:
                item_id = f"{task.task_id}_msg_{msg_id}"
                new_items.append(self._create_item(task, item_id, message_id=msg_id))
            await self._task_manager.add_items(task.task_id, new_items)

        # 并发转发
        async def _forward_one(item):
            if item.status in (
                ItemStatus.SUCCESS,
                ItemStatus.SKIPPED,
                ItemStatus.FAILED,
            ):
                return
            async with self._forward_semaphore:
                await self._task_manager.update_item_status(
                    task.task_id, item.id, ItemStatus.RUNNING
                )
                try:
                    message = None
                    if filter_types:
                        message = await self._client.get_messages(
                            chat_id, item.source_message_id
                        )
                        if message and message.media:
                            media_type = self._get_media_type(message)
                            if media_type and media_type not in filter_types:
                                await self._task_manager.update_item_status(
                                    task.task_id, item.id, ItemStatus.SKIPPED
                                )
                                return

                    # 仓库中转模式
                    if self._should_use_repository():
                        repo_chat_id = self._repository_manager.get_repository_chat_id()
                        log.info(
                            f"转发仓库中转: item={item.id}, "
                            f"repo_chat_id={repo_chat_id}, target={target_chat_id}"
                        )
                        file_unique_id = None

                        # 获取消息以提取 file_unique_id
                        if not message:
                            message = await self._client.get_messages(
                                chat_id, item.source_message_id
                            )
                        if message:
                            file_unique_id = self._extract_file_unique_id(message)
                            log.info(
                                f"转发仓库中转: item={item.id}, "
                                f"message_id={item.source_message_id}, "
                                f"file_unique_id={file_unique_id}, "
                                f"has_media={message.media is not None}, "
                                f"media_type={type(message.media).__name__ if message.media else None}"
                            )
                        else:
                            log.warning(
                                f"转发仓库中转: item={item.id}, "
                                f"无法获取消息 message_id={item.source_message_id}"
                            )

                        if file_unique_id:
                            # L2 去重检查
                            dedup = await self._repository_manager.check_dedup(
                                source_chat_id=chat_id,
                                source_message_id=item.source_message_id,
                                file_unique_id=file_unique_id,
                            )
                            if dedup:
                                # 仓库已有，从仓库分发到目标
                                target_msg_id = (
                                    await self._repository_manager.distribute_to_target(
                                        client=self._client,
                                        file_unique_id=file_unique_id,
                                        target_chat_id=target_chat_id,
                                    )
                                )
                                if target_msg_id:
                                    await self._task_manager.update_item_status(
                                        task.task_id,
                                        item.id,
                                        ItemStatus.SUCCESS,
                                        target_chat_id=target_chat_id,
                                        uploaded_message_id=target_msg_id,
                                    )
                                    return
                                # 分发失败，降级到仓库中转

                            # 仓库无记录：先复制到仓库频道
                            repo_msg = await self._client.copy_message(
                                chat_id=int(repo_chat_id),
                                from_chat_id=chat_id,
                                message_id=item.source_message_id,
                            )
                            # 写入仓库记录
                            await self._repository_manager.on_upload_success(
                                message=repo_msg,
                                source_chat_id=chat_id,
                                source_message_id=item.source_message_id,
                            )
                            # 从仓库分发到目标频道
                            target_msg_id = (
                                await self._repository_manager.distribute_to_target(
                                    client=self._client,
                                    file_unique_id=file_unique_id,
                                    target_chat_id=target_chat_id,
                                )
                            )
                            if target_msg_id:
                                await self._task_manager.update_item_status(
                                    task.task_id,
                                    item.id,
                                    ItemStatus.SUCCESS,
                                    target_chat_id=target_chat_id,
                                    uploaded_message_id=target_msg_id,
                                )
                            else:
                                # 分发失败降级：直接转发到目标
                                result_message = await self._client.copy_message(
                                    chat_id=target_chat_id,
                                    from_chat_id=chat_id,
                                    message_id=item.source_message_id,
                                )
                                await self._task_manager.update_item_status(
                                    task.task_id,
                                    item.id,
                                    ItemStatus.SUCCESS,
                                    target_chat_id=target_chat_id,
                                    uploaded_message_id=result_message.id,
                                )
                            return

                    # 非仓库模式：直接转发到目标频道
                    # 受限转发（内容保护频道）时降级为下载后上传
                    try:
                        result_message = await self._client.copy_message(
                            chat_id=target_chat_id,
                            from_chat_id=chat_id,
                            message_id=item.source_message_id,
                        )
                    except (ChatForwardsRestricted_400, ChatForwardsRestricted_406):
                        uploaded_msg_id = await self._download_then_upload_forward(
                            task, item, chat_id, target_chat_id
                        )
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item.id,
                            ItemStatus.SUCCESS,
                            target_chat_id=target_chat_id,
                            uploaded_message_id=uploaded_msg_id,
                        )
                        return
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item.id,
                        ItemStatus.SUCCESS,
                        target_chat_id=target_chat_id,
                        uploaded_message_id=result_message.id,
                    )
                except Exception as e:
                    log.warning(
                        f"转发子任务失败: item={item.id}, "
                        f"source_message_id={item.source_message_id}, error={e}"
                    )
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item.id,
                        ItemStatus.FAILED,
                        error_code="EXECUTION_ERROR",
                        error_message=str(e),
                    )

        await asyncio.gather(*[_forward_one(item) for item in task.items])

    async def _download_then_upload_forward(
        self,
        task: Task,
        item: TaskItem,
        chat_id: int,
        target_chat_id: int,
    ) -> int | None:
        """受限转发降级：下载消息媒体后上传到目标频道。

        内容保护频道禁止 copy_message 转发，改为"下载后上传"，
        与旧架构 BOT /forward 的受限降级行为一致。
        """
        if self._downloader is None:
            raise ExecutorError("受限转发降级需要 downloader 支持")

        # 1. 下载该消息的媒体文件
        downloaded_files, _ = await self._downloader.download_range(
            chat_id=chat_id,
            start_id=item.source_message_id,
            end_id=item.source_message_id,
            task_id=task.task_id,
            progress_callback=self._on_item_progress,
            message_ids=[item.source_message_id],
        )
        if not downloaded_files:
            raise ExecutorError("受限转发降级：未能下载任何媒体文件")

        # 2. 上传到目标频道
        uploaded_msg_id: int | None = None
        for file_path in downloaded_files:
            upload_result = await self._file_manager.upload(
                file_path=file_path,
                chat_id=target_chat_id,
                delete_after=task.params.get("delete_after_upload", False),
                source_chat_id=chat_id,
                source_message_id=item.source_message_id,
            )
            if upload_result and getattr(upload_result, "message", None):
                uploaded_msg_id = upload_result.message.id
        return uploaded_msg_id

    async def _execute_upload(self, task: Task) -> None:
        """执行上传任务。"""
        chat_id = task.chat_id
        file_paths = task.params.get("file_paths", [])

        if not file_paths:
            raise ExecutorError(f"任务 {task.task_id} 没有文件路径")

        # 将可移植路径还原为绝对路径（数据库存储为相对路径）
        save_root = self._get_save_root()
        resolved_paths = [from_portable_path(fp, save_root) for fp in file_paths]

        # 收集所有文件信息
        file_infos = []
        for file_path in resolved_paths:
            if not file_path:
                continue

            if os.path.isdir(file_path):
                # 扫描目录
                upload_files = safe_scan_directory_file(file_path)
                for filename in upload_files:
                    full_path = os.path.join(file_path, filename)
                    try:
                        file_info = await self._file_manager.get_file_info(full_path)
                        file_infos.append(file_info)
                    except Exception as e:
                        log.warning(f"获取文件信息失败: {full_path}, {e}")
            else:
                try:
                    file_info = await self._file_manager.get_file_info(file_path)
                    file_infos.append(file_info)
                except Exception as e:
                    log.warning(f"获取文件信息失败: {file_path}, {e}")

        if not file_infos:
            raise ExecutorError(f"任务 {task.task_id} 没有有效的文件")

        # 拆分为媒体组和单文件
        groups = await self._file_manager.split_media_group(file_infos)

        item_index = 0
        # 收集需要并发上传的单文件
        single_file_uploads: list[tuple] = []

        for group in groups:
            is_album = group.get("is_album", False)
            files = group.get("files", [])

            # 创建子任务项并持久化到数据库（file_path 转为可移植格式存储）
            new_items = []
            for file_info in files:
                item_id = f"{task.task_id}_file_{item_index}"
                portable_fp = to_portable_path(file_info.path, save_root)
                new_items.append(
                    self._create_item(
                        task, item_id, message_id=None, file_path=portable_fp
                    )
                )
                item_index += 1
            await self._task_manager.add_items(task.task_id, new_items)

            if is_album and len(files) > 1:
                # 媒体组：保持顺序整组上传
                group_start_index = item_index - len(files)

                # 更新整组状态为运行中
                for i in range(len(files)):
                    item_id = f"{task.task_id}_file_{group_start_index + i}"
                    await self._task_manager.update_item_status(
                        task.task_id, item_id, ItemStatus.RUNNING
                    )

                try:
                    results = await self._file_manager.upload_media_group(
                        file_infos=files,
                        chat_id=chat_id,
                        progress_callback=self._on_progress,
                        delete_after=task.params.get("delete_after_upload", True),
                    )
                    for i, res in enumerate(results):
                        item_id = f"{task.task_id}_file_{group_start_index + i}"
                        if res.success:
                            await self._task_manager.update_item_status(
                                task.task_id, item_id, ItemStatus.SUCCESS
                            )
                        else:
                            await self._task_manager.update_item_status(
                                task.task_id,
                                item_id,
                                ItemStatus.FAILED,
                                error_code="UPLOAD_ERROR",
                                error_message=res.error_msg or "UNKNOWN_ERROR",
                            )
                except Exception as e:
                    for i in range(len(files)):
                        item_id = f"{task.task_id}_file_{group_start_index + i}"
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item_id,
                            ItemStatus.FAILED,
                            error_code="EXECUTION_ERROR",
                            error_message=str(e),
                        )
            else:
                # 单文件：收集后并发上传
                for file_info in files:
                    item_id = (
                        f"{task.task_id}_file_{item_index - files.index(file_info) - 1}"
                    )
                    single_file_uploads.append((file_info, item_id))

        # 单文件并发上传
        async def _upload_one(file_info, item_id):
            async with self._upload_semaphore:
                await self._task_manager.update_item_status(
                    task.task_id, item_id, ItemStatus.RUNNING
                )
                try:
                    # L3 去重检查
                    if self._should_use_repository():
                        file_sha256 = self._repository_manager.compute_content_hash(
                            file_info.path
                        )
                        dedup = await self._repository_manager.check_dedup(
                            content_hash=file_sha256,
                        )
                        if dedup:
                            target_msg_id = (
                                await self._repository_manager.distribute_to_target(
                                    client=self._client,
                                    file_unique_id=dedup.file_unique_id,
                                    target_chat_id=chat_id,
                                )
                            )
                            if target_msg_id:
                                await self._task_manager.update_item_status(
                                    task.task_id,
                                    item_id,
                                    ItemStatus.SUCCESS,
                                    target_chat_id=chat_id,
                                    uploaded_message_id=target_msg_id,
                                    file_sha256=file_sha256,
                                )
                                return

                    result = await self._file_manager.upload(
                        file_path=file_info.path,
                        chat_id=chat_id,
                        progress_callback=self._on_progress,
                        delete_after=task.params.get("delete_after_upload", True),
                    )
                    if result.success:
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item_id,
                            ItemStatus.SUCCESS,
                            target_chat_id=chat_id,
                        )
                    else:
                        await self._task_manager.update_item_status(
                            task.task_id,
                            item_id,
                            ItemStatus.FAILED,
                            error_code="UPLOAD_ERROR",
                            error_message=result.error_msg or "UNKNOWN_ERROR",
                        )
                except Exception as e:
                    await self._task_manager.update_item_status(
                        task.task_id,
                        item_id,
                        ItemStatus.FAILED,
                        error_code="EXECUTION_ERROR",
                        error_message=str(e),
                    )

        if single_file_uploads:
            await asyncio.gather(*[_upload_one(f, i) for f, i in single_file_uploads])

    async def _on_item_progress(
        self,
        task_id: str,
        item_id: str,
        status: ItemStatus,
        error: Optional[str] = None,
        **kwargs,
    ) -> None:
        """子任务进度回调。

        Args:
            task_id: 任务 ID
            item_id: 子任务项 ID
            status: 新状态
            error: 可选的错误信息
            **kwargs: 额外字段（如 file_path, file_size 等）传递给 update_item_status
        """
        try:
            # 将 file_path 转为可移植格式（相对路径 + / 分隔符）再存储
            if "file_path" in kwargs and kwargs["file_path"]:
                kwargs["file_path"] = to_portable_path(
                    kwargs["file_path"], self._get_save_root()
                )
            await self._task_manager.update_item_status(
                task_id, item_id, status, error, **kwargs
            )
        except Exception as e:
            log.error(f"更新子任务状态失败: {e}")

    async def _on_progress(self, progress: UploadProgress) -> None:
        """上传进度回调（供 FileManager 使用）。"""
        log.debug(
            f"上传进度: {progress.file_path} - "
            f"{progress.percentage:.1f}% ({progress.current}/{progress.total})"
        )

    @staticmethod
    def _create_item(
        task: Task,
        item_id: str,
        message_id: Optional[int] = None,
        file_path: Optional[str] = None,
        media_group_id: Optional[str] = None,
    ) -> TaskItem:
        """创建子任务项。"""
        now = datetime.now(timezone.utc)
        return TaskItem(
            id=item_id,
            task_id=task.task_id,
            source_message_id=message_id,
            source_file_path=file_path,
            file_path=file_path,
            media_group_id=media_group_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _get_media_type(message) -> Optional[str]:
        """获取消息的媒体类型字符串。

        返回: "video", "photo", "document", "audio", "animation", "voice",
              "video_note" 或 None。
        """
        if not message or not message.media:
            return None
        media = message.media

        # 检查是否是 Pyrogram 的 MessageMediaType 枚举
        # Pyrogram 2.x 中，message.media 可能是枚举类型（如 MessageMediaType.PHOTO）
        # 使用 isinstance(media, Enum) 而非 hasattr(media, "name")，
        # 因为 MagicMock 也有 name 属性，会导致误判。
        if isinstance(media, Enum):
            # 枚举类型，通过 name 判断（如 'PHOTO', 'VIDEO', 'DOCUMENT'）
            media_name = media.name
            # 枚举名称映射到标准类型
            name_mapping = {
                "PHOTO": "photo",
                "VIDEO": "video",
                "DOCUMENT": "document",
                "AUDIO": "audio",
                "ANIMATION": "animation",
                "VOICE": "voice",
                "VIDEO_NOTE": "video_note",
                "STICKER": "sticker",
                "CONTACT": "contact",
                "LOCATION": "location",
                "VENUE": "venue",
                "WEB_PAGE": "web_page",
                "GAME": "game",
            }
            return name_mapping.get(media_name.upper(), None)

        # 旧版 Pyrogram：message.media 是 MessageMedia 对象
        if hasattr(media, "video") and media.video:
            return "video"
        if hasattr(media, "photo") and media.photo:
            return "photo"
        if hasattr(media, "document") and media.document:
            return "document"
        if hasattr(media, "audio") and media.audio:
            return "audio"
        if hasattr(media, "animation") and media.animation:
            return "animation"
        if hasattr(media, "voice") and media.voice:
            return "voice"
        if hasattr(media, "video_note") and media.video_note:
            return "video_note"
        return None

    @staticmethod
    def _extract_file_unique_id(message) -> Optional[str]:
        """从消息的媒体对象中提取 file_unique_id。"""
        if not message or not message.media:
            return None
        # 直接访问 message 的属性，而不是 message.media 的属性
        if message.video:
            return getattr(message.video, "file_unique_id", None)
        if message.photo:
            return getattr(message.photo, "file_unique_id", None)
        if message.document:
            return getattr(message.document, "file_unique_id", None)
        if message.audio:
            return getattr(message.audio, "file_unique_id", None)
        if message.animation:
            return getattr(message.animation, "file_unique_id", None)
        if message.voice:
            return getattr(message.voice, "file_unique_id", None)
        if message.video_note:
            return getattr(message.video_note, "file_unique_id", None)
        return None

    @staticmethod
    def _extract_telegram_file_id(message) -> Optional[str]:
        """从消息的媒体对象中提取 file_id。"""
        if not message or not message.media:
            return None
        # 直接访问 message 的属性，而不是 message.media 的属性
        if message.video:
            return getattr(message.video, "file_id", None)
        if message.photo:
            return getattr(message.photo, "file_id", None)
        if message.document:
            return getattr(message.document, "file_id", None)
        if message.audio:
            return getattr(message.audio, "file_id", None)
        if message.animation:
            return getattr(message.animation, "file_id", None)
        if message.voice:
            return getattr(message.voice, "file_id", None)
        if message.video_note:
            return getattr(message.video_note, "file_id", None)
        return None

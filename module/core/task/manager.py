# coding=UTF-8
"""TaskManager - 任务管理器

负责任务全生命周期管理：
- 创建、排队、启动、重试、取消
- 状态流转与持久化
- 资源保护（大小阈值、磁盘空间）
- 子任务管理

数据库访问层基于 SQLModel 异步引擎（module.core.db），业务逻辑层使用
Task / TaskItem dataclass。两者通过 _task_to_record / _record_to_task 等
mapper 方法相互转换。
"""

import asyncio
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import delete, func
from sqlmodel import select

from module.core.db import get_session
from module.core.task.models import TaskItemRecord, TaskRecord

if TYPE_CHECKING:
    from module.core.config_manager import ConfigManager
    from module.core.identifier_service import IdentifierService, ResolvedChat

log = logging.getLogger("rich")


# ============================================================
# 枚举定义
# ============================================================


class TaskType(Enum):
    """任务类型。"""

    DOWNLOAD = "download"
    FORWARD = "forward"
    UPLOAD = "upload"
    LISTEN_DOWNLOAD = "listen_download"
    LISTEN_FORWARD = "listen_forward"
    CLEANUP_FILES = "cleanup_files"


# 常驻 running 型任务：执行完成后不进入 completed，重启后保持 running，
# 由各自组件恢复/续跑（监听任务走 recover_listeners，定时清理走 CleanupScheduler）。
# 新增常驻型任务类型时必须同步加入此集合，并补充重启恢复测试。
RESIDENT_RUNNING_TASK_TYPES: frozenset[TaskType] = frozenset(
    {
        TaskType.LISTEN_DOWNLOAD,
        TaskType.LISTEN_FORWARD,
        TaskType.CLEANUP_FILES,
    }
)

# 其中的监听型任务：重启后由 TaskExecutor.recover_listeners 重新注册 Handler。
LISTEN_TASK_TYPES: frozenset[TaskType] = frozenset(
    {
        TaskType.LISTEN_DOWNLOAD,
        TaskType.LISTEN_FORWARD,
    }
)


class TaskStatus(Enum):
    """任务状态。"""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemStatus(Enum):
    """子任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


# ============================================================
# 数据模型
# ============================================================


@dataclass
class TaskItem:
    """子任务项，对应一条消息或一个本地文件（设计文档 §3.2）。"""

    id: str
    task_id: str
    status: ItemStatus = ItemStatus.PENDING
    source_message_id: int | None = None
    source_file_path: str | None = None
    target_chat_id: int | None = None
    file_path: str | None = None
    file_size: int = 0
    file_sha256: str | None = None
    telegram_file_id: str | None = None
    file_unique_id: str | None = None
    media_group_id: str | None = None
    uploaded_message_id: int | None = None
    retry_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    extra: dict = field(default_factory=dict)

    # ---- 辅助方法 ----

    def mark_success(self):
        """标记为成功。"""
        self.status = ItemStatus.SUCCESS

    def mark_failed(self, reason: str):
        """标记为失败。"""
        self.status = ItemStatus.FAILED
        self.error_message = reason
        self.retry_count += 1

    def mark_skipped(self, reason: str):
        """标记为跳过。"""
        self.status = ItemStatus.SKIPPED
        self.error_message = reason

    def can_retry(self) -> bool:
        """判断是否可重试。"""
        if self.retry_count >= 3:
            return False
        non_retryable = [
            "MESSAGE_ID_INVALID",
            "CHAT_FORBIDDEN",
            "USER_BANNED",
            "CHANNEL_PRIVATE",
        ]
        check_str = self.error_code or self.error_message or ""
        if check_str and any(nr in check_str for nr in non_retryable):
            return False
        return True


@dataclass
class Task:
    """任务，对应一个下载/转发/上传操作（设计文档 §3.1）。"""

    task_id: str
    task_type: TaskType
    chat_id: int
    chat_username: str | None = None
    chat_type: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    items: list[TaskItem] = field(default_factory=list)
    total_size_bytes: int = 0
    retry_count: int = 0
    max_retry_count: int = 5
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    params: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @property
    def total_items(self) -> int:
        """子任务总数（实时计算）。"""
        return len(self.items)

    @property
    def success_count(self) -> int:
        """成功子任务数（纯实时计算）。"""
        return sum(1 for item in self.items if item.status == ItemStatus.SUCCESS)

    @property
    def failed_count(self) -> int:
        """失败子任务数（纯实时计算）。"""
        return sum(1 for item in self.items if item.status == ItemStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        """跳过子任务数（纯实时计算）。"""
        return sum(1 for item in self.items if item.status == ItemStatus.SKIPPED)

    @property
    def pending_count(self) -> int:
        """待处理子任务数。"""
        return sum(1 for item in self.items if item.status == ItemStatus.PENDING)

    @property
    def progress(self) -> float:
        """任务进度百分比。"""
        if not self.items:
            return 0.0
        return (self.success_count / len(self.items)) * 100


# ============================================================
# 异常定义
# ============================================================


class TaskManagerError(Exception):
    """TaskManager 基础异常。"""


class ValidationError(TaskManagerError):
    """参数校验失败。"""


class ResourceLimitError(TaskManagerError):
    """资源限制触发。"""


class TaskNotFoundError(TaskManagerError):
    """任务不存在。"""


class TaskStateError(TaskManagerError):
    """任务状态不允许当前操作。"""


class TaskConflictError(TaskManagerError):
    """任务冲突，例如同一 chat_id 重复创建监听任务。"""


class ExecutorError(TaskManagerError):
    """执行器内部错误。"""


# 向后兼容别名（过渡期保留，后续批次移除）
InvalidStateTransition = TaskStateError


# ============================================================
# TaskManager 类
# ============================================================


class TaskManager:
    """任务管理器。

    负责任务创建、调度、状态流转、持久化与资源保护。

    数据库访问通过 module.core.db 提供的全局异步引擎进行。构造完成后，
    调用方需在 ``db.init_db()`` 之后调用 ``await initialize()`` 加载历史任务。
    """

    # 允许的状态转换
    VALID_TRANSITIONS = {
        TaskStatus.PENDING: {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.FAILED: {TaskStatus.PENDING},  # retry
        TaskStatus.CANCELLED: {TaskStatus.PENDING},  # retry
    }

    def __init__(
        self,
        max_concurrent_tasks: int = 1,
        max_retry_count: int = 5,
        task_size_warning_gb: int = 5,
        task_size_max_gb: int = 10,
        min_disk_space_gb: int = 2,
        identifier_service: Optional["IdentifierService"] = None,
        config_manager: Optional["ConfigManager"] = None,
        max_download_tasks: int | None = None,
        max_upload_tasks: int | None = None,
    ):
        self._max_concurrent_tasks = max_concurrent_tasks
        # 按类型独立并发限制：DOWNLOAD / UPLOAD 各自最多 N 个，互不阻塞；
        # 为 None 时回退全局 max_concurrent_tasks 闸。
        self._max_download_tasks = max_download_tasks
        self._max_upload_tasks = max_upload_tasks
        self._max_retry_count = max_retry_count
        self._task_size_warning_gb = task_size_warning_gb
        self._task_size_max_gb = task_size_max_gb
        self._min_disk_space_gb = min_disk_space_gb
        self._identifier_service = identifier_service
        self._config_manager = config_manager

        self._tasks: dict[str, Task] = {}
        self._task_queue: list[str] = []
        self._lock = asyncio.Lock()
        # 已惰性加载子任务的任务 ID 集合
        self._items_loaded: set[str] = set()
        # 任务执行器（由外部在创建后注入，用于真正触发任务执行）
        self._executor: Any | None = None
        # 任务终态通知器（由外部在创建后注入；未注入时终态通知关闭，行为不变）
        self._notifier: Any | None = None
        # 注意：建表由 module.core.db.init_db 统一处理；
        #       历史任务加载由 initialize() 异步完成。

    def set_executor(self, executor: Any) -> None:
        """注入任务执行器。

        被注入的 executor 必须提供 submit_task(task) 方法，用于将任务
        提交到正确的异步事件循环执行。当任务被调度为 RUNNING 时触发。
        """
        self._executor = executor

    def set_notifier(self, notifier: Any) -> None:
        """注入任务终态通知器。

        被注入的 notifier 必须提供 notify_completed(task) / notify_failed(task)
        异步方法；任务进入终态（completed / failed）时触发（fire-and-forget），
        不阻塞状态机。未注入时通知功能关闭，任务流程不受影响。
        """
        self._notifier = notifier

    def _notify(self, task: "Task", kind: str) -> None:
        """触发终态通知（fire-and-forget，失败由通知器内部吞掉）。"""
        if self._notifier is None:
            return
        method = getattr(self._notifier, kind, None)
        if method is None:
            return
        asyncio.create_task(method(task))

    async def _dispatch(self, task: Task) -> None:
        """将任务提交到 executor 执行（仅当 executor 存在时）。"""
        if self._executor is not None:
            self._executor.submit_task(task)

    async def initialize(self) -> None:
        """初始化 TaskManager：从数据库加载历史任务到内存缓存。

        必须在 ``module.core.db.init_db()`` 调用后、首次使用 TaskManager 前调用一次。
        """
        await self._load_tasks_from_db()

    async def resume_queued_tasks(self) -> None:
        """调度重启后恢复的排队任务。

        启动恢复流程中，排队任务会先由 ``_load_tasks_from_db`` 恢复到
        内存队列，但队列调度依赖 executor 已注入。因此本方法必须在
        ``set_executor()`` 之后调用，否则 ``_dispatch`` 无法提交执行。
        """
        async with self._lock:
            await self._process_queue()
        log.info(f"队列调度已触发，剩余排队任务: {len(self._task_queue)}")

    # ============================================================
    # 数据库访问层（SQLModel 异步）
    # ============================================================

    async def _load_tasks_from_db(self):
        """从数据库加载所有任务到内存缓存（子任务按需惰性加载）。"""
        async with get_session() as session:
            result = await session.execute(select(TaskRecord))
            records = result.scalars().all()

            for record in records:
                task = self._record_to_task(record)
                self._tasks[task.task_id] = task

            # 恢复排队中的任务到队列（防止重启后排队任务丢失）
            queued_ids = [
                t.task_id for t in self._tasks.values() if t.status == TaskStatus.QUEUED
            ]
            if queued_ids:
                self._task_queue.extend(queued_ids)
                log.info(f"已恢复 {len(queued_ids)} 个排队任务: {queued_ids}")

        # 处理崩溃遗留的 running 非监听任务：标记为 failed，释放并发槽位。
        # 常驻 running 型任务（监听/定时清理）保持原状态，交由各自组件恢复续跑。
        # （注意：会话外保存，避免在只读查询会话内执行写操作）
        stale_running = [
            task
            for task in self._tasks.values()
            if task.status == TaskStatus.RUNNING
            and task.task_type not in RESIDENT_RUNNING_TASK_TYPES
        ]
        for task in stale_running:
            task.status = TaskStatus.FAILED
            task.error_message = "程序重启导致任务中断，请手动重试"
            await self._save_task(task)
            log.info(f"启动时标记遗留 running 任务为 failed: {task.task_id}")

    async def _ensure_items(self, task: Task) -> None:
        """惰性加载子任务到 task.items（仅首次从数据库加载，后续跳过）。"""
        if task.task_id in self._items_loaded:
            return
        async with get_session() as session:
            item_result = await session.execute(
                select(TaskItemRecord).where(TaskItemRecord.task_id == task.task_id)
            )
            for item_record in item_result.scalars().all():
                task.items.append(self._record_to_item(item_record))
        self._items_loaded.add(task.task_id)

    # ---- mapper 方法：业务对象 <-> 数据库模型 ----

    def _task_to_record(self, task: Task) -> TaskRecord:
        """业务对象 Task 转数据库模型 TaskRecord。"""
        return TaskRecord(
            id=task.task_id,
            task_type=task.task_type.value,
            status=task.status.value,
            chat_id=task.chat_id,
            chat_username=task.chat_username,
            chat_type=task.chat_type,
            params=task.params,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            total_size_bytes=task.total_size_bytes,
            error_message=task.error_message,
            retry_count=task.retry_count,
            max_retry_count=task.max_retry_count,
            extra=task.extra,
        )

    def _record_to_task(self, record: TaskRecord) -> Task:
        """数据库模型 TaskRecord 转业务对象 Task。"""
        params = record.params or {}
        extra = record.extra or {}
        return Task(
            task_id=record.id,
            task_type=TaskType(record.task_type),
            chat_id=record.chat_id,
            chat_username=record.chat_username,
            chat_type=record.chat_type,
            params=params,
            status=TaskStatus(record.status),
            total_size_bytes=record.total_size_bytes or 0,
            retry_count=record.retry_count or 0,
            max_retry_count=record.max_retry_count or 5,
            error_message=record.error_message,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            extra=extra,
        )

    def _item_to_record(self, item: TaskItem) -> TaskItemRecord:
        """业务对象 TaskItem 转数据库模型 TaskItemRecord。"""
        return TaskItemRecord(
            id=item.id,
            task_id=item.task_id,
            status=item.status.value,
            source_message_id=item.source_message_id,
            source_file_path=item.source_file_path,
            target_chat_id=item.target_chat_id,
            file_path=item.file_path,
            file_size=item.file_size,
            file_sha256=item.file_sha256,
            telegram_file_id=item.telegram_file_id,
            file_unique_id=item.file_unique_id,
            media_group_id=item.media_group_id,
            uploaded_message_id=item.uploaded_message_id,
            retry_count=item.retry_count,
            error_code=item.error_code,
            error_message=item.error_message,
            created_at=item.created_at,
            updated_at=item.updated_at,
            extra=item.extra,
        )

    def _record_to_item(self, record: TaskItemRecord) -> TaskItem:
        """数据库模型 TaskItemRecord 转业务对象 TaskItem。"""
        return TaskItem(
            id=record.id,
            task_id=record.task_id,
            status=ItemStatus(record.status),
            source_message_id=record.source_message_id,
            source_file_path=record.source_file_path,
            target_chat_id=record.target_chat_id,
            file_path=record.file_path,
            file_size=record.file_size or 0,
            file_sha256=record.file_sha256,
            telegram_file_id=record.telegram_file_id,
            file_unique_id=record.file_unique_id,
            media_group_id=record.media_group_id,
            uploaded_message_id=record.uploaded_message_id,
            retry_count=record.retry_count or 0,
            error_code=record.error_code,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            extra=record.extra or {},
        )

    async def _save_task(self, task: Task):
        """保存任务到数据库（tm_tasks 表，upsert 语义）。"""
        record = self._task_to_record(task)
        async with get_session() as session:
            await session.merge(record)
            await session.commit()

    async def _save_item(self, task_id: str, item: TaskItem):
        """保存子任务到数据库（tm_task_items 表，upsert 语义）。"""
        # 保持与原实现一致：以传入的 task_id 为准
        item.task_id = task_id
        record = self._item_to_record(item)
        async with get_session() as session:
            await session.merge(record)
            await session.commit()

    def _validate_transition(self, current: TaskStatus, target: TaskStatus):
        """验证状态转换是否合法。"""
        allowed = self.VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise TaskStateError(f"无效状态转换: {current.value} → {target.value}")

    def _limit_for(self, task_type: TaskType) -> int:
        """返回指定任务类型的并发上限。

        DOWNLOAD / UPLOAD 配置了按类型限制时返回该限制，
        否则（含其它类型）回退全局 ``max_concurrent_tasks`` 闸。
        """
        if task_type == TaskType.DOWNLOAD and self._max_download_tasks is not None:
            return self._max_download_tasks
        if task_type == TaskType.UPLOAD and self._max_upload_tasks is not None:
            return self._max_upload_tasks
        return self._max_concurrent_tasks

    def _get_running_count(self, task_type: TaskType | None = None) -> int:
        """获取当前运行中的一次性任务数（常驻 running 型任务不计入）。

        常驻型任务（RESIDENT_RUNNING_TASK_TYPES，如监听/定时清理）生命周期
        贯穿进程，不应占用一次性任务的并发槽位；否则会永久阻塞其它任务。

        Args:
            task_type: 传入时只统计该类型的运行中任务数（仍排除常驻型）。
        """
        count = 0
        for task in self._tasks.values():
            if task.status != TaskStatus.RUNNING:
                continue
            if task.task_type in RESIDENT_RUNNING_TASK_TYPES:
                continue
            if task_type is not None and task.task_type != task_type:
                continue
            count += 1
        return count

    # ============================================================
    # 公开接口
    # ============================================================

    async def _resolve_chat_id(
        self,
        task_type: TaskType,
        chat_id: int | None,
        params: dict | None,
    ) -> "ResolvedChat":
        """解析并返回标准化对话信息。

        优先级:
        1. 显式传入的 chat_id（若有效）。
        2. params.source_identifier（通过 IdentifierService 解析）。
        3. params.chat_id（向后兼容）。

        :raises ValidationError: 没有任何有效标识符。
        :raises IdentifierServiceError: 解析失败（由上层转换为 HTTP 错误码）。
        """
        from module.core.identifier_service import ResolvedChat

        if chat_id:
            return ResolvedChat(
                chat_id=int(chat_id),
                chat_type="unknown",
                chat_name=f"chat_{chat_id}",
                username=None,
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )

        p = params or {}
        source_identifier = p.get("source_identifier")
        if source_identifier and self._identifier_service:
            return await self._identifier_service.resolve(source_identifier)

        fallback_chat_id = p.get("chat_id")
        if fallback_chat_id:
            return ResolvedChat(
                chat_id=int(fallback_chat_id),
                chat_type="unknown",
                chat_name=f"chat_{fallback_chat_id}",
                username=None,
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )

        raise ValidationError("chat_id 或 source_identifier 必须提供一个")

    @staticmethod
    def _derive_source_type(resolved_chat: "ResolvedChat") -> str:
        """根据 ResolvedChat.chat_type 推导内部 source_type。"""
        if resolved_chat.chat_type in {"channel", "supergroup", "group"}:
            return "channel"
        if resolved_chat.chat_type in {"private", "bot"}:
            return "private"
        return "unknown"

    def _check_listen_conflict(self, chat_id: int, task_type: TaskType) -> None:
        """检查同一 chat_id + task_type 是否已存在进行中的监听任务。"""
        if task_type not in LISTEN_TASK_TYPES:
            return
        for task in self._tasks.values():
            if (
                task.task_type == task_type
                and task.chat_id == chat_id
                and task.status in (TaskStatus.RUNNING, TaskStatus.PENDING)
            ):
                raise TaskConflictError("该聊天已存在进行中的监听任务")

    def _resolve_enable_repository_backup(
        self, task_type: TaskType, params: dict
    ) -> bool | None:
        """解析仓库备份参数：任务级覆盖优先，否则继承全局配置。"""
        if task_type not in (TaskType.DOWNLOAD, TaskType.LISTEN_DOWNLOAD):
            return None

        explicit = params.get("enable_repository_backup")
        if explicit is not None:
            return bool(explicit)

        if self._config_manager:
            return bool(
                self._config_manager.get("repository.auto_backup_downloads", False)
            )
        return False

    @staticmethod
    def _truncate_recent_count(params: dict) -> dict:
        """若 range_mode=recent 且 recent_count > 1000，截断为 1000。"""
        if params.get("range_mode") != "recent":
            return params
        recent_count = params.get("recent_count")
        if isinstance(recent_count, int) and recent_count > 1000:
            log.warning(f"recent_count {recent_count} 超过上限，截断为 1000")
            return {**params, "recent_count": 1000}
        return params

    async def create_task(
        self,
        task_type: TaskType,
        chat_id: int | None = None,
        params: dict | None = None,
        auto_start: bool = False,
    ) -> Task:
        """创建任务。

        内部执行参数校验和强制级资源预检：
        - 无效 task_type → 抛出 ValidationError
        - chat_id / source_identifier 为空 → 抛出 ValidationError
        - 任务大小 > task_size_max_gb → 抛出 ResourceLimitError
        - 磁盘空间不足 → 抛出 ResourceLimitError
        - 同一 chat_id 重复监听任务 → 抛出 TaskConflictError

        警告级检查（5GB~10GB）由 API 层单独处理。
        """
        # 参数校验
        if not isinstance(task_type, TaskType):
            raise ValidationError(f"无效的任务类型: {task_type}")

        params = params or {}

        # 解析源对话标识符（定时清理任务无源对话，使用哨兵 chat_id）
        if task_type == TaskType.CLEANUP_FILES:
            from module.core.identifier_service import ResolvedChat

            resolved_chat = ResolvedChat(
                chat_id=-1,
                chat_type="unknown",
                chat_name="cleanup",
                username=None,
                message_count=-1,
                media_count=-1,
                has_access=True,
                is_private=False,
            )
        else:
            resolved_chat = await self._resolve_chat_id(task_type, chat_id, params)
        resolved_chat_id = resolved_chat.chat_id

        # 监听任务排他性校验
        self._check_listen_conflict(resolved_chat_id, task_type)

        # 消息范围参数校验（UPLOAD / CLEANUP_FILES 任务不需要消息范围）
        if task_type not in (TaskType.UPLOAD, TaskType.CLEANUP_FILES):
            range_mode = params.get("range_mode", "all")
            valid_modes = {"id_range", "multiple_ids", "date_range", "all", "recent"}
            if range_mode not in valid_modes:
                raise ValidationError(f"无效的 range_mode: {range_mode}")

            if range_mode == "id_range":
                if not params.get("min_id") and not params.get("message_range_start"):
                    raise ValidationError("id_range 模式需要提供 min_id")
            elif range_mode == "multiple_ids":
                if not params.get("message_list") and not params.get("message_ids"):
                    raise ValidationError("multiple_ids 模式需要提供 message_list")
            elif range_mode == "date_range":
                if not params.get("start_date") and not params.get("date_start"):
                    raise ValidationError("date_range 模式需要提供 start_date")
            elif range_mode == "recent":
                recent_count = params.get("recent_count")
                if not recent_count or recent_count <= 0:
                    raise ValidationError("recent 模式需要提供 recent_count > 0")
                params = self._truncate_recent_count(params)

        # 仓库备份参数继承/覆盖
        enable_backup = self._resolve_enable_repository_backup(task_type, params)
        if enable_backup is not None:
            params = {**params, "enable_repository_backup": enable_backup}

        # 强制级资源预检
        estimated_size = params.get("estimated_size", 0)
        size_level, size_msg = self.check_size_threshold(estimated_size)
        if size_level == "exceeded":
            raise ResourceLimitError(size_msg or "任务大小超过上限")

        # 磁盘空间预检
        if not self.check_disk_space(estimated_size):
            raise ResourceLimitError(
                f"磁盘剩余空间不足，需至少保留 {self._min_disk_space_gb}GB"
            )

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        source_type = self._derive_source_type(resolved_chat)
        task = Task(
            task_id=task_id,
            task_type=task_type,
            chat_id=resolved_chat_id,
            chat_username=resolved_chat.username,
            chat_type=resolved_chat.chat_type,
            params=params,
            max_retry_count=self._max_retry_count,
            created_at=datetime.now(UTC),
            extra={"source_type": source_type},
        )
        async with self._lock:
            self._tasks[task_id] = task
            await self._save_task(task)
        log.info(f"任务已创建: {task_id} ({task_type.value})")
        if auto_start:
            await self.start_task(task_id)
        return task

    async def start_task(self, task_id: str) -> bool:
        """启动任务。"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            running_count = self._get_running_count(task.task_type)
            limit = self._limit_for(task.task_type)
            if running_count >= limit:
                # 进入队列
                self._validate_transition(task.status, TaskStatus.QUEUED)
                task.status = TaskStatus.QUEUED
                self._task_queue.append(task_id)
                await self._save_task(task)
                log.info(f"任务进入队列: {task_id}")
                return False
            else:
                # 直接运行
                self._validate_transition(task.status, TaskStatus.RUNNING)
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(UTC)
                await self._save_task(task)
                log.info(f"任务开始执行: {task_id}")
                # 任务真正进入 RUNNING 后才提交执行，保证状态机与执行同步
                await self._dispatch(task)
                return True

    async def complete_task(self, task_id: str):
        """完成任务。"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            self._validate_transition(task.status, TaskStatus.COMPLETED)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            await self._save_task(task)
            log.info(f"任务已完成: {task_id}")

            # 尝试启动队列中的下一个任务
            await self._process_queue()

            # 触发完成通知（fire-and-forget，不阻塞状态机与队列调度）
            self._notify(task, "notify_completed")

    async def fail_task(self, task_id: str, reason: str):
        """标记任务失败。"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            self._validate_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            task.error_message = reason
            await self._save_task(task)
            log.warning(f"任务失败: {task_id} - {reason}")

            # 尝试启动队列中的下一个任务
            await self._process_queue()

            # 触发错误通知（fire-and-forget，不阻塞状态机与队列调度）
            self._notify(task, "notify_failed")

    async def cancel_task(self, task_id: str, reason: str | None = None):
        """取消任务。"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            self._validate_transition(task.status, TaskStatus.CANCELLED)
            if reason:
                task.error_message = reason
            task.status = TaskStatus.CANCELLED
            await self._save_task(task)
            log.info(f"任务已取消: {task_id}")

            # 从队列中移除
            if task_id in self._task_queue:
                self._task_queue.remove(task_id)

    async def retry_task(self, task_id: str):
        """重试任务。"""

        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            self._validate_transition(task.status, TaskStatus.PENDING)
            task.status = TaskStatus.PENDING
            task.retry_count += 1
            task.error_message = None
            task.started_at = None
            task.completed_at = None

            # 惰性加载子任务，重置/跳过失败的子任务
            await self._ensure_items(task)
            for item in task.items:
                if item.status == ItemStatus.FAILED:
                    if item.can_retry():
                        # 可重试：重置为 PENDING
                        item.status = ItemStatus.PENDING
                        item.error_message = None
                    else:
                        # 不可重试：标记为 SKIPPED，避免重复执行无效操作
                        item.status = ItemStatus.SKIPPED
                        if not item.error_message:
                            item.error_message = "不可重试，已跳过"
                    await self._save_item(task_id, item)

            await self._save_task(task)
            log.info(f"任务重试: {task_id} (第 {task.retry_count} 次)")

    async def build_referenced_paths(self) -> set[str]:
        """收集当前被活跃任务引用的本地文件路径集合。

        供文件删除/定时清理做"任务引用保护"：状态为 pending/queued/running 的
        非 cleanup 任务，其 params.file_paths 与已落地的子任务 file_path
        全部纳入保护；同时为每个文件补充 ``.temp`` 变体（下载中中间文件）兜底。

        Returns:
            规范化绝对路径集合（含 .temp 变体）
        """
        active_statuses = (
            TaskStatus.PENDING.value,
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
        )
        referenced: set[str] = set()

        async with get_session() as session:
            records = (
                (
                    await session.execute(
                        select(TaskRecord).where(TaskRecord.status.in_(active_statuses))
                    )
                )
                .scalars()
                .all()
            )

            for record in records:
                # 定时清理任务自身不产生文件引用。
                if record.task_type == TaskType.CLEANUP_FILES.value:
                    continue

                # params.file_paths（排队中的上传任务目标文件等）。
                for p in record.params.get("file_paths") or []:
                    if p:
                        referenced.add(os.path.abspath(os.path.normpath(str(p))))

                # 已落地的子任务文件路径（下载中/上传中）。
                item_records = (
                    (
                        await session.execute(
                            select(TaskItemRecord).where(
                                TaskItemRecord.task_id == record.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for item in item_records:
                    if item.file_path:
                        norm = os.path.abspath(os.path.normpath(item.file_path))
                        referenced.add(norm)
                        referenced.add(f"{norm}.temp")

        return referenced

    async def get_task(self, task_id: str, with_items: bool = False) -> Task | None:
        """获取任务。

        Args:
            task_id: 任务 ID
            with_items: 是否同时加载子任务。默认 False（惰性）。
        """
        task = self._tasks.get(task_id)
        if task and with_items:
            await self._ensure_items(task)
        return task

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        task_type: TaskType | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        """获取任务列表，支持状态过滤、类型过滤和分页。

        Returns:
            (tasks, total): tasks 是分页后列表，total 是过滤后总数（分页前）
        """
        if limit is not None:
            # 使用数据库查询（支持 LIMIT/OFFSET/WHERE）
            async with get_session() as session:
                # 构建查询条件
                stmt = select(TaskRecord)
                count_stmt = select(func.count(TaskRecord.id))
                if status:
                    stmt = stmt.where(TaskRecord.status == status.value)
                    count_stmt = count_stmt.where(TaskRecord.status == status.value)
                if task_type:
                    stmt = stmt.where(TaskRecord.task_type == task_type.value)
                    count_stmt = count_stmt.where(
                        TaskRecord.task_type == task_type.value
                    )

                # 总数查询
                total = (await session.execute(count_stmt)).scalar_one()

                # 分页查询（按创建时间倒序）
                stmt = (
                    stmt.order_by(TaskRecord.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()

                tasks = []
                for record in records:
                    existing = self._tasks.get(record.id)
                    if existing:
                        # 更新现有对象的属性（保持引用不变）
                        new_task = self._record_to_task(record)
                        existing.status = new_task.status
                        existing.error_message = new_task.error_message
                        existing.started_at = new_task.started_at
                        existing.completed_at = new_task.completed_at
                        existing.total_size_bytes = new_task.total_size_bytes
                        existing.retry_count = new_task.retry_count
                        existing.extra = new_task.extra
                        # 同步子任务：用数据库数据替换 items 列表内容
                        # 汇总字段（total_items/success_count 等）已改为 property，自动从 items 计算
                        existing.items.clear()
                        item_result = await session.execute(
                            select(TaskItemRecord).where(
                                TaskItemRecord.task_id == record.id
                            )
                        )
                        for item_record in item_result.scalars().all():
                            existing.items.append(self._record_to_item(item_record))
                        self._items_loaded.add(record.id)
                        tasks.append(existing)
                    else:
                        # 新发现的任务（理论上不应发生，但做防御性处理）
                        new_task = self._record_to_task(record)
                        item_result = await session.execute(
                            select(TaskItemRecord).where(
                                TaskItemRecord.task_id == record.id
                            )
                        )
                        for item_record in item_result.scalars().all():
                            new_task.items.append(self._record_to_item(item_record))
                        self._items_loaded.add(record.id)
                        self._tasks[new_task.task_id] = new_task
                        tasks.append(new_task)
                return tasks, total
        else:
            # 无分页时直接过滤内存缓存
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == status]
            if task_type:
                tasks = [t for t in tasks if t.task_type == task_type]
            total = len(tasks)
            return tasks, total

    async def delete_task(self, task_id: str):
        """删除任务及其所有子任务（同时从内存和数据库中删除）。"""
        async with self._lock:
            # 从内存移除
            self._tasks.pop(task_id, None)
            # 从数据库删除（先删子任务，再删主任务）
            async with get_session() as session:
                await session.execute(
                    delete(TaskItemRecord).where(TaskItemRecord.task_id == task_id)
                )
                await session.execute(
                    delete(TaskRecord).where(TaskRecord.id == task_id)
                )
                await session.commit()
            log.info(f"任务已删除: {task_id}")

    async def add_items(self, task_id: str, items: list[TaskItem]):
        """添加子任务项。"""
        now = datetime.now(UTC)
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            # 惰性加载已有子任务，避免覆盖现有记录
            await self._ensure_items(task)
            for item in items:
                item.task_id = task_id
                item.created_at = now
                item.updated_at = now
            task.items.extend(items)
            for item in items:
                await self._save_item(task_id, item)
            # 汇总字段已改为 property，自动从 items 实时计算
            await self._save_task(task)

    async def update_item_status(
        self,
        task_id: str,
        item_id: str,
        status: ItemStatus,
        error_message: str | None = None,
        error_code: str | None = None,
        **kwargs,
    ):
        """更新子任务状态，支持额外字段更新。

        Args:
            task_id: 任务 ID
            item_id: 子任务项 ID
            status: 新状态
            error_message: 可选的错误描述（人类可读）
            error_code: 可选的错误代码（机器可读）
            **kwargs: 额外要更新的字段（如 file_unique_id, file_sha256 等）
        """
        now = datetime.now(UTC)
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")

            # 惰性加载子任务，确保能定位到目标 item
            await self._ensure_items(task)
            for item in task.items:
                if item.id == item_id:
                    item.status = status
                    item.updated_at = now
                    if error_code:
                        item.error_code = error_code
                    if error_message:
                        item.error_message = error_message
                    for key, value in kwargs.items():
                        if hasattr(item, key):
                            setattr(item, key, value)
                    # 汇总字段（success_count/failed_count/total_items 等）已改为
                    # Task dataclass 的 property，自动从 items 实时计算，无需双重写入
                    await self._save_item(task_id, item)
                    await self._save_task(task)
                    break

    async def update_file_paths(self, task_id: str, file_paths: list[str]):
        """更新任务的已下载文件路径列表。

        Args:
            task_id: 任务 ID
            file_paths: 已下载文件的可移植相对路径列表（/ 分隔符）
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            task.params["file_paths"] = file_paths
            await self._save_task(task)
            log.info(f"任务 {task_id} 文件路径已更新: {len(file_paths)} 个文件")

    async def get_failed_items(self, task_id: str) -> list[TaskItem]:
        """获取失败的子任务。"""
        task = self._tasks.get(task_id)
        if not task:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        await self._ensure_items(task)
        return [item for item in task.items if item.status == ItemStatus.FAILED]

    def check_size_threshold(self, size_bytes: int) -> tuple[str, str | None]:
        """检查任务大小阈值。

        返回:
            (level, message) 元组:
            - ("ok", None) - 低于告警阈值
            - ("warning", "当前任务 X.XX GB，超过 NgB 告警阈值") - 超过告警
            - ("exceeded", "单次任务超过 NGB 上限（X.XX GB）") - 超过上限
        """
        warning_bytes = self._task_size_warning_gb * 1024 * 1024 * 1024
        max_bytes = self._task_size_max_gb * 1024 * 1024 * 1024
        gb = size_bytes / (1024**3)

        if size_bytes > max_bytes:
            return (
                "exceeded",
                f"单次任务超过 {self._task_size_max_gb}GB 上限（{gb:.2f}GB）",
            )
        elif size_bytes > warning_bytes:
            return (
                "warning",
                f"当前任务 {gb:.2f}GB，超过 {self._task_size_warning_gb}GB 告警阈值",
            )
        return "ok", None

    def check_disk_space(
        self, estimated_size: int = 0, download_dir: str | None = None
    ) -> bool:
        """检查磁盘空间是否充足。

        Args:
            estimated_size: 预估任务大小（字节）
            download_dir: 下载目录路径，用于检查磁盘空间

        返回 True 表示空间充足，False 表示空间不足。

        Raises:
            ResourceLimitError: 无法获取磁盘使用信息时
        """
        try:
            check_dir = download_dir or os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
            # 如果目录不存在，向上查找最近存在的父目录
            if not os.path.exists(check_dir):
                parent = os.path.dirname(check_dir)
                while parent and not os.path.exists(parent):
                    parent = os.path.dirname(parent)
                check_dir = parent or os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
            usage = shutil.disk_usage(check_dir)
            free_gb = usage.free / (1024 * 1024 * 1024)
            estimated_gb = estimated_size / (1024 * 1024 * 1024)
            return free_gb >= (self._min_disk_space_gb + estimated_gb)
        except OSError:
            raise ResourceLimitError("无法获取磁盘使用信息，拒绝创建任务")

    async def shutdown(self) -> None:
        """优雅关闭：取消运行中/排队中任务、持久化状态。"""
        async with self._lock:
            for task in self._tasks.values():
                if task.status in (TaskStatus.RUNNING, TaskStatus.QUEUED):
                    task.status = TaskStatus.CANCELLED
                    await self._save_task(task)
                    log.info(f"关闭时取消任务: {task.task_id}")
            self._task_queue.clear()
        log.info("TaskManager 已关闭")

    async def _process_queue(self):
        """处理任务队列，尝试启动排队的任务。

        按队头任务类型判断并发槽位：队头类型（DOWNLOAD/UPLOAD）未满则启动，
        否则保持 FIFO 不插队。常驻型任务不占用并发槽位。
        """
        while self._task_queue:
            next_task_id = self._task_queue[0]
            task = self._tasks.get(next_task_id)
            if not task or task.status != TaskStatus.QUEUED:
                self._task_queue.pop(0)
                continue
            limit = self._limit_for(task.task_type)
            if self._get_running_count(task.task_type) >= limit:
                break  # 队头类型槽位满，保持 FIFO 不插队
            self._task_queue.pop(0)
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(UTC)
            await self._save_task(task)
            log.info(f"队列任务已启动: {next_task_id}")
            # 任务正式 RUNNING 后提交执行，保证状态机与执行同步
            await self._dispatch(task)

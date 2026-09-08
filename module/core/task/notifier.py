# coding=UTF-8
"""TaskNotifier - 任务终态通知器

负责在任务进入终态（completed / failed）时通过 Telegram Bot
向唯一授权用户（Bot.root）发送完成/错误通知。

开关联动（见设计文档 docs/superpowers/specs/2026-09-08-task-notification-design.md）：
- 完成通知发送条件：preference.notice（总开关） 且 preference.notification_enabled
- 错误通知发送条件：preference.notice（总开关） 且 preference.error_notification_enabled
- 常驻任务（RESIDENT_RUNNING_TASK_TYPES：监听/定时清理）不发送通知

通知为尽力而为：发送异常仅记录日志，绝不向上抛出，避免影响任务状态流转。
"""

import logging
from datetime import UTC, datetime

from module.core.task.manager import RESIDENT_RUNNING_TASK_TYPES, Task, TaskType

log = logging.getLogger("rich")

# 任务类型中文标签（用于通知消息）
_TASK_TYPE_LABELS: dict[TaskType, str] = {
    TaskType.DOWNLOAD: "下载",
    TaskType.UPLOAD: "上传",
    TaskType.FORWARD: "转发",
    TaskType.LISTEN_DOWNLOAD: "监听下载",
    TaskType.LISTEN_FORWARD: "监听转发",
    TaskType.CLEANUP_FILES: "定时清理",
}

# 布尔真值兼容 YAML 的 true / 字符串 "true"
_TRUTHY = (True, 1, "true", "1")


class TaskNotifier:
    """任务终态通知器：判断开关、构造消息并发送。"""

    def __init__(
        self, client=None, root_ids: list[int] | None = None, config_manager=None
    ):
        """
        Args:
            client: 用于发送通知的 pyrogram Client（实际注入 Bot.bot）
            root_ids: 授权用户 id 列表（Bot.root，单用户通常 1 个）
            config_manager: ConfigManager 实例（读取 preference 开关）
        """
        self._client = client
        self._root_ids = list(root_ids or [])
        self._config_manager = config_manager

    # ---------- 公开接口 ----------

    async def notify_completed(self, task: Task) -> None:
        """任务完成通知（受 notice 与 notification_enabled 控制）。"""
        if not self._should_notify(task, "notification_enabled"):
            return
        label = self._task_label(task)
        text = (
            "✅ 任务完成\n"
            f"任务类型：{label}\n"
            f"任务 ID：{task.task_id}\n"
            f"目标：{self._describe_target(task)}\n"
            f"耗时：{self._format_duration(task)}"
        )
        await self._send(text)

    async def notify_failed(self, task: Task) -> None:
        """任务失败通知（受 notice 与 error_notification_enabled 控制）。"""
        if not self._should_notify(task, "error_notification_enabled"):
            return
        label = self._task_label(task)
        reason = task.error_message or "未知错误"
        text = (
            "❌ 任务失败\n"
            f"任务类型：{label}\n"
            f"任务 ID：{task.task_id}\n"
            f"目标：{self._describe_target(task)}\n"
            f"原因：{reason}"
        )
        await self._send(text)

    # ---------- 内部辅助 ----------

    def _should_notify(self, task: Task, flag_key: str) -> bool:
        """是否满足发送条件：非常驻任务 且 总开关 且 对应子开关。"""
        if task.task_type in RESIDENT_RUNNING_TASK_TYPES:
            return False
        return self._pref_flag("notice") and self._pref_flag(flag_key)

    def _pref_flag(self, key: str) -> bool:
        """读取 preference 布尔开关，缺失/不可读时保守返回 False。"""
        if self._config_manager is None:
            return False
        try:
            value = self._config_manager.get(f"preference.{key}", False)
        except Exception as e:
            log.warning(f"读取通知开关 preference.{key} 失败: {e}")
            return False
        return value in _TRUTHY

    @staticmethod
    def _task_label(task: Task) -> str:
        return _TASK_TYPE_LABELS.get(task.task_type, task.task_type.value)

    @staticmethod
    def _describe_target(task: Task) -> str:
        return task.chat_username or str(task.chat_id)

    @staticmethod
    def _format_duration(task: Task) -> str:
        """格式化执行耗时：优先 started_at，缺失回退 created_at。"""
        start = task.started_at or task.created_at or datetime.now(UTC)
        end = task.completed_at or datetime.now(UTC)
        seconds = max(0, int((end - start).total_seconds()))
        if seconds < 60:
            return f"{seconds} 秒"
        minutes = round(seconds / 60, 1)
        return f"{minutes} 分钟"

    async def _send(self, text: str) -> None:
        """发送通知：异常仅记录日志，不向上抛出。"""
        if self._client is None or not self._root_ids:
            return
        try:
            for user_id in self._root_ids:
                await self._client.send_message(chat_id=user_id, text=text)
        except Exception as e:
            log.error(f"任务通知发送失败: {e}")

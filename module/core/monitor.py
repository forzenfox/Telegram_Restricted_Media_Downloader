# coding=UTF-8
"""Monitor - 监控模块

提供系统资源监控、任务统计、资源状态查询功能。
"""

import logging
import time

log = logging.getLogger(__name__)


class Monitor:
    """监控模块 - 提供系统资源和任务统计。"""

    def __init__(self):
        self._start_time = time.time()

    def get_system_stats(self, disk_path: str | None = None) -> dict:
        """获取系统资源统计。

        Args:
            disk_path: 磁盘统计路径；None 时使用当前工作目录。
                传入配置的下载保存目录（如 Docker 挂载卷）可反映真实存储容量。

        Returns:
            {
                "cpu_percent": float,
                "memory": {"total": int, "available": int, "used": int, "percent": float},
                "disk": {"total": int, "used": int, "free": int, "percent": float},
                "uptime_seconds": float,
            }
        """
        try:
            import os

            import psutil

            cpu_percent = psutil.cpu_percent(interval=0)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(disk_path or os.getcwd())

            return {
                "cpu_percent": cpu_percent,
                "memory": {
                    "total": memory.total,
                    "available": memory.available,
                    "used": memory.used,
                    "percent": memory.percent,
                },
                "disk": {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": disk.percent,
                },
                "uptime_seconds": time.time() - self._start_time,
            }
        except ImportError:
            log.warning("psutil 未安装，无法获取系统资源统计")
            return {
                "cpu_percent": 0,
                "memory": {"total": 0, "available": 0, "used": 0, "percent": 0},
                "disk": {"total": 0, "used": 0, "free": 0, "percent": 0},
                "uptime_seconds": time.time() - self._start_time,
                "error": "psutil not installed",
            }
        except Exception as e:
            log.error(f"获取系统资源统计失败: {e}")
            return {
                "cpu_percent": 0,
                "memory": {"total": 0, "available": 0, "used": 0, "percent": 0},
                "disk": {"total": 0, "used": 0, "free": 0, "percent": 0},
                "uptime_seconds": time.time() - self._start_time,
                "error": str(e),
            }

    def get_task_stats(self, task_manager) -> dict:
        """获取任务统计。

        Args:
            task_manager: TaskManager 实例

        Returns:
            {
                "total": int,
                "running": int,
                "queued": int,
                "pending": int,
                "completed": int,
                "failed": int,
                "cancelled": int,
            }
        """
        if task_manager is None:
            return {
                "total": 0,
                "running": 0,
                "queued": 0,
                "pending": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            }

        try:
            tasks = task_manager.list_tasks()
            stats = {
                "total": len(tasks),
                "running": 0,
                "queued": 0,
                "pending": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            }

            for task in tasks:
                status = (
                    task.status.value
                    if hasattr(task.status, "value")
                    else str(task.status)
                )
                if status in stats:
                    stats[status] += 1

            return stats
        except Exception as e:
            log.error(f"获取任务统计失败: {e}")
            return {
                "total": 0,
                "running": 0,
                "queued": 0,
                "pending": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "error": str(e),
            }

    def get_resource_status(
        self, task_manager=None, file_manager=None, config_manager=None
    ) -> dict:
        """获取资源状态。

        Args:
            task_manager: TaskManager 实例
            file_manager: FileManager 实例
            config_manager: ConfigManager 实例

        Returns:
            {
                "disk": {"total_gb": float, "used_gb": float, "free_gb": float, "percent": float},
                "memory_limit_mb": int,
                "task_size_warning_gb": float,
                "task_size_max_gb": float,
                "min_disk_space_gb": float,
                "max_concurrent_tasks": int,
                "current_running_tasks": int,
                "disk_space_sufficient": bool,
            }
        """
        result = {
            "disk": {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0},
            "memory_limit_mb": 512,
            "task_size_warning_gb": 5,
            "task_size_max_gb": 10,
            "min_disk_space_gb": 2,
            "max_concurrent_tasks": 1,
            "current_running_tasks": 0,
            "disk_space_sufficient": True,
        }

        # 获取磁盘状态
        try:
            import os

            import psutil

            # 获取项目目录或使用根目录
            path = os.getcwd()
            disk = psutil.disk_usage(path)

            result["disk"] = {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "percent": disk.percent,
            }

            # 获取配置中的磁盘空间阈值
            min_disk_gb = 2
            if config_manager:
                min_disk_gb = config_manager.min_disk_space_gb

            result["disk_space_sufficient"] = (disk.free / (1024**3)) >= min_disk_gb

        except ImportError:
            result["disk_space_sufficient"] = True
            log.warning("psutil 未安装，无法获取磁盘状态")
        except Exception as e:
            log.error(f"获取磁盘状态失败: {e}")
            result["disk_space_sufficient"] = True

        # 获取配置中的资源限制
        if config_manager:
            result["memory_limit_mb"] = config_manager.memory_limit_mb
            result["task_size_warning_gb"] = config_manager.task_size_warning_gb
            result["task_size_max_gb"] = config_manager.task_size_max_gb
            result["min_disk_space_gb"] = config_manager.min_disk_space_gb
            result["max_concurrent_tasks"] = config_manager.max_concurrent_tasks

        # 获取当前运行中任务数
        if task_manager:
            try:
                result["current_running_tasks"] = task_manager._get_running_count()
            except Exception:
                pass

        # 获取 Telegram Client 连接状态
        result["client_status"] = self._get_client_status()

        return result

    def _get_client_status(self) -> dict:
        """检查 Telegram Client 的连接状态。"""
        try:
            from module.core.integration import get_context

            ctx = get_context()
            if ctx is None or ctx.client is None:
                return {
                    "connected": False,
                    "status": "not_initialized",
                    "bot_connected": False,
                }
            client = ctx.client
            is_conn = getattr(client, "is_connected", False)
            result = {
                "connected": is_conn,
                "status": "connected" if is_conn else "disconnected",
                "bot_connected": self._check_bot_connected(ctx),
            }

            # 添加重连状态信息
            if hasattr(ctx, "client_manager") and ctx.client_manager:
                result["reconnect_status"] = ctx.client_manager.get_status()

            return result
        except Exception as e:
            log.warning("获取 Client 状态失败: %s", e)
            return {"connected": False, "status": "error", "bot_connected": False}

    @staticmethod
    def _check_bot_connected(ctx) -> bool:
        """检查 Bot Client 是否已启动（通过 TaskExecutor 是否存在间接判断）。"""
        try:
            return ctx.task_executor is not None
        except Exception:
            return False

    def get_monitor_stats(self, task_manager=None, config_manager=None) -> dict:
        """获取完整监控统计（系统资源 + 任务统计）。

        Args:
            task_manager: TaskManager 实例
            config_manager: ConfigManager 实例，用于解析下载保存目录
                作为磁盘统计路径（Docker 挂载卷可反映真实存储容量）

        Returns:
            完整监控统计
        """
        disk_path = self._resolve_monitor_disk_path(config_manager)
        system_stats = self.get_system_stats(disk_path=disk_path)
        task_stats = self.get_task_stats(task_manager)

        return {
            "system": system_stats,
            "tasks": task_stats,
            "timestamp": time.time(),
        }

    @staticmethod
    def _resolve_monitor_disk_path(config_manager) -> str:
        """确定磁盘统计路径：优先配置的下载保存目录，其次当前工作目录。

        目录不存在时向上查找最近存在的父目录（与 TaskManager.check_disk_space
        行为一致）；含占位符（如 {chat_type}）的目录无法在监控维度解析，直接回退。
        """
        import os

        fallback = os.getcwd()
        if config_manager is None:
            return fallback
        try:
            save_dir = getattr(config_manager, "save_directory", None)
        except Exception:
            return fallback
        save_dir = str(save_dir or "").strip()
        if not save_dir or "{" in save_dir or "}" in save_dir:
            return fallback

        path = os.path.abspath(os.path.expanduser(save_dir))
        if os.path.exists(path):
            return path
        parent = os.path.dirname(path)
        while parent and not os.path.exists(parent):
            grand = os.path.dirname(parent)
            if grand == parent:
                break
            parent = grand
        return parent if parent and os.path.exists(parent) else fallback

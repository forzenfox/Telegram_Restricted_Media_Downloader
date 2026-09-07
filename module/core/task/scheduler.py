# coding=UTF-8
"""CleanupScheduler - 定时清理任务调度器

负责 cleanup_files 任务的时钟触发与生命周期管理：
- 周期计算（daily 固定时刻 / interval 每隔 N 小时）
- 到期自动投递给 TaskExecutor 执行一轮清理
- pause / resume / run_now（手动立即执行）
- next_run_at 持久化到任务 params.last_run

归属：TaskExecutor 内部组件，复用其绑定的事件循环（避免跨 loop）。
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta

from module.core.task.manager import TaskNotFoundError

log = logging.getLogger("rich")


class CleanupScheduler:
    """周期任务调度器：仅处理 cleanup_files 任务。"""

    def __init__(self, task_manager, executor):
        """
        Args:
            task_manager: TaskManager 实例
            executor: TaskExecutor 实例（通过 submit_task 投递执行）
        """
        self._tm = task_manager
        self._executor = executor
        self._loop_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._jobs: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """加载 cleanup_files 任务并启动调度主循环。"""
        async with self._lock:
            await self._load_tasks()
        self._stop_event.clear()
        self._loop_task = asyncio.create_task(self._tick_loop())
        log.info(
            f"CleanupScheduler 启动，已注册 {len(self._jobs)} 个定时清理任务"
        )

    async def stop(self) -> None:
        """停止调度主循环。"""
        self._stop_event.set()
        if self._loop_task is not None:
            try:
                self._loop_task.cancel()
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    # ---------- 任务注册 -----------------------

    def register(self, task) -> None:
        """注册/刷新一个 cleanup_files 任务。"""
        params = task.params or {}
        schedule = params.get("schedule") or {}
        # 已执行过则沿用持久化的 next_run_at，否则（新任务）立即排队等待首次调度
        next_run_at = (params.get("last_run") or {}).get("next_run_at")
        if next_run_at:
            try:
                next_run_at = datetime.fromisoformat(next_run_at)
            except (TypeError, ValueError):
                next_run_at = None
        self._jobs[task.task_id] = {
            "task": task,
            "next_run_at": next_run_at,
            "paused": bool(params.get("paused")),
            "schedule": schedule,
        }

    def unregister(self, task_id: str) -> None:
        """注销任务（任务被删除/取消时）。"""
        self._jobs.pop(task_id, None)

    # ---------- 生命周期控制（pause/resume/run_now） ----------

    async def pause(self, task_id: str) -> None:
        """暂停调度：置 paused=True 并清空 next_run_at。"""
        async with self._lock:
            job = self._jobs.get(task_id)
            if job:
                job["paused"] = True
                job["next_run_at"] = None
            task = await self._tm.get_task(task_id)
            if task is None:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            params = dict(task.params or {})
            params["paused"] = True
            last_run = dict(params.get("last_run") or {})
            last_run["next_run_at"] = None
            params["last_run"] = last_run
            task.params = params
            await self._tm._save_task(task)

    async def resume(
        self, task_id: str, now: datetime | None = None
    ) -> None:
        """恢复调度：置 paused=False 并按周期重算 next_run_at。"""
        async with self._lock:
            task = await self._tm.get_task(task_id)
            if task is None:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            params = dict(task.params or {})
            params["paused"] = False
            schedule = params.get("schedule") or {}
            next_run_at = self._next_run_at(schedule, now=now)
            last_run = dict(params.get("last_run") or {})
            last_run["next_run_at"] = next_run_at.isoformat()
            params["last_run"] = last_run
            task.params = params
            await self._tm._save_task(task)
            # 同步内存 job 状态
            job = self._jobs.setdefault(
                task_id,
                {"task": task, "schedule": schedule, "paused": False},
            )
            job["paused"] = False
            job["next_run_at"] = next_run_at
            job["task"] = task

    async def run_now(self, task_id: str) -> None:
        """手动立即执行一轮清理（不改变既有周期）。"""
        task = await self._tm.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        self._executor.submit_task(task)
        log.info(f"定时清理任务手动执行: {task_id}")

    # ---------- 周期计算 ----------

    @staticmethod
    def _next_run_at(schedule: dict, now: datetime | None = None) -> datetime:
        """计算下一次执行时间。

        daily 模式：最近的 HH:MM 时刻（今日未到则今日，否则明日）。
        interval 模式：now + interval_hours。
        """
        now = now or datetime.now(timezone.utc)
        schedule = schedule or {}
        mode = schedule.get("mode", "daily")
        if mode == "interval":
            hours = int(schedule.get("interval_hours", 24))
            return now + timedelta(hours=hours)
        # daily
        raw_time = str((schedule or {}).get("time", "03:00"))
        hh, mm = (int(x) for x in raw_time.split(":"))
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # ---------- 调度主循环 ----------

    async def _tick_loop(self) -> None:
        """轮询到期任务并投递执行，执行后按周期重排（re-arm）。"""
        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            due_jobs = [
                job
                for job in self._jobs.values()
                if not job["paused"]
                and job.get("next_run_at") is not None
                and job["next_run_at"] <= now
            ]
            for job in due_jobs:
                try:
                    self._executor.submit_task(job["task"])
                except Exception as e:
                    log.error(
                        f"定时清理任务投递失败: {job['task'].task_id}, 原因: {e}"
                    )
                    continue
                job["next_run_at"] = self._next_run_at(job["schedule"], now=now)
                await self._persist_next_run(job["task"].task_id, job["next_run_at"])

            # 睡到最近的到期时间（1s~10min 之间）。
            next_times = [
                job["next_run_at"]
                for job in self._jobs.values()
                if not job["paused"] and job.get("next_run_at") is not None
            ]
            if next_times:
                delay = min((n - now).total_seconds() for n in next_times)
                delay = max(1.0, min(delay, 600))
            else:
                delay = 600
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=delay
                )
            except asyncio.TimeoutError:
                pass

    async def _persist_next_run(
        self, task_id: str, next_run_at: datetime
    ) -> None:
        """持久化 next_run_at 到任务 params.last_run。"""
        task = await self._tm.get_task(task_id)
        if task is None:
            return
        params = dict(task.params or {})
        last_run = dict(params.get("last_run") or {})
        last_run["next_run_at"] = next_run_at.isoformat()
        params["last_run"] = last_run
        task.params = params
        await self._tm._save_task(task)

    # ---------- 加载 ----------

    async def _load_tasks(self) -> None:
        """从数据库加载所有 cleanup_files 任务并注册。"""
        from module.core.task.manager import TaskType

        tasks, _total = await self._tm.list_tasks(
            task_type=TaskType.CLEANUP_FILES, limit=1000
        )
        for task in tasks:
            self.register(task)
# coding=UTF-8
"""任务管理路由。

提供任务 CRUD、开始/取消/重试等操作。
"""

import logging
from typing import cast

from fastapi import APIRouter, Depends, Query, Request

from module.api.dependencies import (
    get_identifier_service,
    get_task_manager,
    require_token,
)
from module.api.exceptions import (
    InsufficientDiskSpace,
    TaskConflictError,
    TaskNotFoundError,
    TaskSizeWarning,
)
from module.api.models.task import TaskCreate, TaskOut
from module.api.responses import error_json_response, json_response
from module.core.identifier_service import IdentifierService
from module.core.task.manager import (
    Task,
    TaskManager,
    TaskStateError,
    TaskStatus,
    TaskType,
)
from module.core.task.manager import (
    TaskConflictError as CoreTaskConflictError,
)

router = APIRouter(prefix="/tasks", tags=["任务"])
logger = logging.getLogger(__name__)


def _get_client(request: Request):
    """获取 Telegram Client 实例（从 AppContext 单例读取）。"""
    try:
        from module.core.integration import get_context

        ctx = get_context()
        return ctx.client if ctx else None
    except Exception:
        return None


def _task_to_out(task: Task) -> TaskOut:
    """将 Task 转换为 TaskOut 响应模型。"""
    return TaskOut(
        id=task.task_id,
        task_type=task.task_type.value,
        status=task.status.value,
        progress=round(task.progress, 2),
        created_at=task.created_at,
        updated_at=task.started_at or task.completed_at or task.created_at,
        message=task.error_message,
        success_count=task.success_count,
        failed_count=task.failed_count,
        total_count=len(task.items),
        file_paths=task.params.get("file_paths", []),
        params=task.params,
    )


@router.get("")
async def list_tasks(
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
    status_filter: str | None = Query(None, alias="status"),
    task_type: str | None = Query(None, alias="task_type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取任务列表，支持分页和过滤。"""
    # 参数校验
    status_enum: TaskStatus | None = None
    if status_filter:
        try:
            status_enum = TaskStatus(status_filter)
        except ValueError:
            return error_json_response(
                code=400, message=f"无效的状态: {status_filter}", status_code=400
            )

    type_enum: TaskType | None = None
    if task_type:
        try:
            type_enum = TaskType(task_type)
        except ValueError:
            return error_json_response(
                code=400, message=f"无效的类型: {task_type}", status_code=400
            )

    # 下沉到 TaskManager 进行过滤和分页
    filtered_items, total = await task_manager.list_tasks(
        status=status_enum,
        task_type=type_enum,
        limit=limit,
        offset=offset,
    )

    data = {
        "items": [_task_to_out(t).model_dump(mode="json") for t in filtered_items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
    return json_response(data=data)


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """获取任务详情。"""
    task = await task_manager.get_task(task_id, with_items=True)
    if not task:
        raise TaskNotFoundError(task_id)
    return json_response(data=_task_to_out(task).model_dump(mode="json"))


@router.post("")
async def create_task(
    request: Request,
    body: TaskCreate,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
    identifier_service: IdentifierService = Depends(get_identifier_service),
):
    """创建任务。"""
    # TaskCreate 的 model_validator 已将 params 规范化为 dict
    params = cast(dict, body.params)

    # 检查任务大小 - 处理警告级（强制级已由 TaskManager.create_task 内部处理）
    estimated_size = params.get("estimated_size", 0)

    size_level, size_msg = task_manager.check_size_threshold(estimated_size)
    if size_level == "warning":
        size_human = params.get("size_human", size_msg or "")
        raise TaskSizeWarning(size_human)

    # 磁盘空间 - 在 API 层提前检查以提供人类可读的错误码
    # （强制级检查也在 TaskManager.create_task 中作为兜底）
    from module.core.integration import get_context

    ctx = get_context()
    download_dir = ctx.config_manager.save_directory if ctx else None
    if not task_manager.check_disk_space(estimated_size, download_dir=download_dir):
        raise InsufficientDiskSpace()

    # 映射任务类型（阶段 2 已支持 listen_*）
    type_map = {
        "download": TaskType.DOWNLOAD,
        "forward": TaskType.FORWARD,
        "upload": TaskType.UPLOAD,
        "listen_download": TaskType.LISTEN_DOWNLOAD,
        "listen_forward": TaskType.LISTEN_FORWARD,
    }
    task_type = type_map.get(body.task_type)
    if task_type is None:
        return error_json_response(
            code=400, message=f"无效的任务类型: {body.task_type}", status_code=400
        )

    # 源端标识：优先 source_identifier，由 TaskManager 内部解析；否则回退到 chat_id
    source_identifier = params.get("source_identifier")
    chat_id = None
    if not source_identifier:
        raw_chat_id = params.get("chat_id")
        try:
            chat_id = int(raw_chat_id) if raw_chat_id is not None else None
        except (ValueError, TypeError):
            return error_json_response(
                code=400, message="chat_id 格式无效", status_code=400
            )

    # 解析目标频道（转发/监听转发任务需要）：优先 target_identifier，回退 forward_target
    target_input = params.get("target_identifier") or params.get("forward_target")
    target_chat_id = None
    if target_input:
        resolved_target = await identifier_service.resolve(target_input)
        target_chat_id = resolved_target.chat_id

    # forward / listen_forward 任务必须提供有效目标频道
    if task_type in (TaskType.FORWARD, TaskType.LISTEN_FORWARD) and (
        target_chat_id is None or target_chat_id == 0
    ):
        return error_json_response(
            code=400, message="转发任务需要有效的目标频道", status_code=400
        )

    # 范围模式处理
    range_mode = params.get("range_mode")
    message_range = None
    if range_mode == "id_range":
        min_id = params.get("min_id")
        max_id = params.get("max_id")
        if min_id is not None and max_id is not None:
            message_range = (min_id, max_id)

    file_paths = params.get("file_paths", [])
    delete_after = params.get("delete_after_upload", True)

    # 构建任务扩展参数（用于持久化存储，供 UI 展示和执行参考）
    task_params: dict = {
        "target_chat_id": target_chat_id,
        "message_range_start": message_range[0] if message_range else None,
        "message_range_end": message_range[1] if message_range else None,
        "file_paths": file_paths,
        "delete_after_upload": delete_after,
        "estimated_size": estimated_size,
        "range_mode": range_mode,
        "filter_types": params.get("filter_types", []),
        "min_id": params.get("min_id"),
        "max_id": params.get("max_id"),
        "start_date": params.get("start_date"),
        "end_date": params.get("end_date"),
        "message_list": params.get("message_list", []),
        "source_identifier": source_identifier,
        "target_identifier": target_input or params.get("target_identifier"),
        "recent_count": params.get("recent_count"),
        "media_types": params.get("media_types"),
        "min_size": params.get("min_size"),
        "max_size": params.get("max_size"),
        "enable_repository_backup": params.get("enable_repository_backup"),
    }
    # source_identifier 存在时交给 TaskManager 解析，不要设置 chat_id；否则使用 chat_id 回退
    if source_identifier:
        task_params["source_identifier"] = source_identifier
    elif chat_id is not None:
        task_params["chat_id"] = chat_id

    # 移除 None 值，保持干净（保留空列表，供前端识别字段存在）
    task_params = {k: v for k, v in task_params.items() if v is not None}

    # 创建任务（源端解析下沉到 TaskManager，由其统一推导 source_type）
    try:
        task = await task_manager.create_task(
            task_type=task_type,
            params=task_params,
        )
    except CoreTaskConflictError as e:
        raise TaskConflictError("LISTEN_ALREADY_EXISTS") from e

    return json_response(
        data=_task_to_out(task).model_dump(mode="json"), status_code=201
    )


@router.post("/{task_id}/start")
async def start_task(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """开始/排队任务。

    任务执行由 TaskManager 统一调度：直接运行的任务在进入 RUNNING 时
    立即提交执行；并发已满入队的任务在队列中被调度时才会执行。
    """
    try:
        started = await task_manager.start_task(task_id)
        task = await task_manager.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        return json_response(
            data=_task_to_out(task).model_dump(mode="json"),
            message="任务已开始" if started else "任务已加入队列",
        )
    except TaskNotFoundError:
        raise TaskNotFoundError(task_id)
    except TaskStateError:
        raise TaskConflictError("任务状态不允许启动")


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """取消任务。"""
    try:
        body = await request.json() if request.method == "POST" else {}
        reason = body.get("reason") if isinstance(body, dict) else None
    except Exception:
        reason = None

    try:
        await task_manager.cancel_task(task_id, reason=reason)
        task = await task_manager.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return json_response(
            data=_task_to_out(task).model_dump(mode="json"), message="任务已取消"
        )
    except TaskNotFoundError:
        raise TaskNotFoundError(task_id)
    except TaskStateError:
        raise TaskConflictError("任务状态不允许取消")


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """重试任务，重置状态后由 TaskManager 统一调度执行。"""
    try:
        # 1. 重置任务状态为 pending（重试子任务、清空错误信息）
        await task_manager.retry_task(task_id)

        # 2. 自动启动任务（PENDING → RUNNING/QUEUED，内部触发执行）
        started = await task_manager.start_task(task_id)

        task = await task_manager.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        return json_response(
            data=_task_to_out(task).model_dump(mode="json"),
            message="任务已重试并开始执行" if started else "任务已重试并加入队列",
        )
    except TaskNotFoundError:
        raise TaskNotFoundError(task_id)
    except TaskStateError:
        raise TaskConflictError("任务状态不允许重试")


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """删除已完成或失败的任务记录。"""
    task = await task_manager.get_task(task_id)
    if not task:
        raise TaskNotFoundError(task_id)

    if task.status not in (
        TaskStatus.PENDING,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        raise TaskConflictError("只能删除等待中、已完成、失败或已取消的任务")

    # 从内存和数据库中永久删除
    await task_manager.delete_task(task_id)
    return json_response(data=None, message="任务记录已删除")


@router.get("/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    request: Request,
    token: str = Depends(require_token),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """获取任务执行日志。

    返回任务的执行日志列表和错误信息，用于调试和监控。
    """
    task = await task_manager.get_task(task_id)
    if not task:
        raise TaskNotFoundError(task_id)

    # 获取日志数据（Task 类当前无 logs 字段，返回空列表）
    logs = getattr(task, "logs", [])

    # 收集子任务的错误信息作为补充日志
    item_logs = []
    for item in task.items:
        if item.error_message:
            item_logs.append(
                {
                    "item_id": item.id,
                    "status": item.status.value,
                    "error": item.error_message,
                }
            )

    return json_response(
        data={
            "task_id": task_id,
            "logs": logs,
            "item_logs": item_logs,
            "error_message": task.error_message,
            "status": task.status.value,
            "total_logs": len(logs),
            "total_item_errors": len(item_logs),
        }
    )

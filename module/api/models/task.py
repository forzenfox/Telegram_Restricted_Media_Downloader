# coding=UTF-8
"""任务相关 Pydantic 数据模型。"""

from datetime import datetime
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, ConfigDict, model_validator

TaskType = Literal["download", "forward", "upload", "listen_download", "listen_forward", "cleanup_files"]
TaskStatus = Literal["pending", "queued", "running", "completed", "failed", "cancelled"]
RangeMode = Literal["date_range", "id_range", "multiple_ids", "all", "recent"]


class TaskParams(BaseModel):
    """任务创建参数模型。

    对 TaskCreate.params 做可选约束，同时允许额外字段以保持向后兼容。
    """

    source_identifier: Optional[str] = None
    target_identifier: Optional[str] = None
    chat_id: Optional[Union[int, str]] = None
    range_mode: Optional[RangeMode] = None
    recent_count: Optional[int] = None
    media_types: Optional[list[str]] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    enable_repository_backup: Optional[bool] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def check_recent_count(self):
        """recent 模式必须提供 recent_count，其他模式不允许携带。"""
        if self.range_mode == "recent":
            if self.recent_count is None or self.recent_count <= 0:
                raise ValueError("range_mode='recent' 时 recent_count 必须大于 0")
        elif self.recent_count is not None:
            raise ValueError("range_mode 不是 recent 时，recent_count 必须为 None")
        return self


class TaskBase(BaseModel):
    """任务基础模型。"""

    task_type: TaskType


class TaskCreate(BaseModel):
    """创建任务请求体。"""

    task_type: TaskType
    params: Union[TaskParams, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_params(self):
        """统一将 params 转为 dict，同时利用 TaskParams 完成校验。"""
        if isinstance(self.params, TaskParams):
            self.params = self.params.model_dump()
        elif isinstance(self.params, dict):
            # 通过 TaskParams 校验，非法值会抛出 ValidationError
            self.params = TaskParams(**self.params).model_dump()
        return self


class TaskOut(BaseModel):
    """任务响应数据。"""

    id: str
    task_type: TaskType
    status: TaskStatus
    progress: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    message: Optional[str] = None
    success_count: int = 0
    failed_count: int = 0
    total_count: int = 0
    file_paths: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)

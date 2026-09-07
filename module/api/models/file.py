# coding=UTF-8
"""文件相关 Pydantic 数据模型。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    """单个文件/目录信息。"""

    name: str
    path: str
    type: Literal["file", "directory"]
    size: Optional[int] = None
    modified_at: Optional[str] = None
    telegram_type: Optional[str] = None  # Telegram 语义分类


class FileListOut(BaseModel):
    """文件列表响应数据。"""

    path: str
    items: list[FileInfo]


class FileUploadRequest(BaseModel):
    """文件上传请求体。"""

    chat_id: int
    file_paths: list[str]
    caption: str = ""
    delete_after: bool = False
    as_media_group: bool = False


class FileBatchDeleteRequest(BaseModel):
    """文件批量删除请求体。"""

    file_paths: list[str] = Field(min_length=1, max_length=500)  # 1~500 条绝对路径，须位于下载根目录内

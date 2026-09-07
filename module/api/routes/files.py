# coding=UTF-8
"""文件管理路由。

提供文件列表浏览功能，集成 FileManager 核心能力。
"""

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query

from module.api.dependencies import require_token, get_file_manager, get_config_manager
from module.api.responses import json_response, error_json_response
from module.api.models.file import FileInfo, FileUploadRequest

router = APIRouter(prefix="/files", tags=["文件"])


@router.get("")
async def list_files(
    request: Request,
    token: str = Depends(require_token),
    path: Optional[str] = Query(None, description="目录路径，默认为下载根目录"),
    recursive: bool = Query(False, description="是否递归"),
    include_hidden: bool = Query(False, description="是否包含隐藏文件"),
):
    """获取文件列表。

    集成 FileManager.list_files，支持文件分类（photo/video/audio 等）。

    :param request: FastAPI 请求对象
    :param token: 认证 Token
    :param path: 目录路径
    :param recursive: 是否递归
    :param include_hidden: 是否包含隐藏文件
    :return: 文件列表
    """
    # 获取下载根目录
    config_manager = get_config_manager(request)
    try:
        base_dir = os.path.abspath(config_manager.save_directory or "downloads")
    except AttributeError:
        base_dir = os.path.abspath("downloads")

    # 前端路径格式为虚拟路径（'/' 表示根目录，'/subdir' 表示子目录）
    # 需要映射到实际的下载根目录下；同时支持绝对路径直接访问
    if not path or path == "/":
        # 根路径：使用下载根目录
        target_path = base_dir
    elif os.path.isabs(path):
        # 绝对路径：直接使用（如按绝对路径浏览下载目录）
        target_path = os.path.abspath(path)
    else:
        # 子路径：基于下载根目录拼接
        # 去掉开头的 '/'，然后拼接
        relative_path = path.lstrip("/")
        target_path = os.path.abspath(os.path.join(base_dir, relative_path))

    # 安全检查：确保目标路径在下载根目录下，防止路径穿越攻击
    if not target_path.startswith(base_dir):
        return json_response(
            data={"path": target_path, "items": [], "error": "非法路径"}
        )

    if not os.path.exists(target_path):
        return json_response(data={"path": target_path, "items": []})

    if not os.path.isdir(target_path):
        return json_response(data={"path": target_path, "items": []})

    # 尝试使用 FileManager 获取文件列表
    file_manager = get_file_manager(request)
    if file_manager:
        try:
            files = await file_manager.list_files(
                path=target_path,
                recursive=recursive,
                include_hidden=include_hidden,
            )
            items = []
            for f in files:
                items.append(
                    FileInfo(
                        name=f.name,
                        path=f.path,
                        type="directory" if f.is_directory else "file",
                        size=f.size if not f.is_directory else 0,
                        modified_at=datetime.fromtimestamp(f.modified_time).isoformat(),
                        telegram_type=f.telegram_type,
                    )
                )
            # 按类型和名称排序
            items.sort(key=lambda x: (x.type == "file", x.name.lower()))
            return json_response(
                data={"path": target_path, "items": [i.model_dump(mode="json") for i in items]}
            )
        except (PermissionError, FileNotFoundError, NotADirectoryError) as e:
            return json_response(
                data={"path": target_path, "items": [], "error": str(e)}
            )

    # 降级方案：原生 os.scandir
    items = []
    try:
        with os.scandir(target_path) as entries:
            for entry in entries:
                try:
                    stat = entry.stat()
                    if entry.is_dir():
                        items.append(
                            FileInfo(
                                name=entry.name,
                                path=entry.path,
                                type="directory",
                                size=0,
                                modified_at=datetime.fromtimestamp(
                                    stat.st_mtime
                                ).isoformat(),
                            )
                        )
                    else:
                        items.append(
                            FileInfo(
                                name=entry.name,
                                path=entry.path,
                                type="file",
                                size=stat.st_size,
                                modified_at=datetime.fromtimestamp(
                                    stat.st_mtime
                                ).isoformat(),
                            )
                        )
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        return json_response(data={"path": target_path, "items": []})

    # 按名称排序
    items.sort(key=lambda x: (x.type == "file", x.name.lower()))

    return json_response(
        data={"path": target_path, "items": [i.model_dump(mode="json") for i in items]}
    )


@router.get("/info")
async def get_file_info(
    request: Request,
    token: str = Depends(require_token),
    path: str = Query(..., description="文件/目录路径"),
):
    """获取单个文件/目录详情。

    :param request: FastAPI 请求对象
    :param token: 认证 Token
    :param path: 文件/目录绝对路径
    :return: 文件详情
    """
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return json_response(data=None, error="文件不存在")

    file_manager = get_file_manager(request)
    if file_manager:
        try:
            info = await file_manager.get_file_info(abs_path)
            return json_response(
                data={
                    "name": info.name,
                    "path": info.path,
                    "type": "directory" if info.is_directory else "file",
                    "size": info.size,
                    "mime_type": info.mime_type,
                    "extension": info.extension,
                    "modified_at": datetime.fromtimestamp(
                        info.modified_time
                    ).isoformat(),
                    "telegram_type": info.telegram_type,
                }
            )
        except Exception as e:
            return json_response(data=None, error=str(e))

    # 降级方案
    stat = os.stat(abs_path)
    return json_response(
        data={
            "name": os.path.basename(abs_path),
            "path": abs_path,
            "type": "directory" if os.path.isdir(abs_path) else "file",
            "size": stat.st_size if os.path.isfile(abs_path) else 0,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    )


@router.post("/upload")
async def upload_files(
    request: Request,
    body: FileUploadRequest,
    token: str = Depends(require_token),
):
    """上传文件到 Telegram 频道。

    支持单文件上传和媒体组上传。
    根据文件类型自动选择合适的发送方式。

    :param request: FastAPI 请求对象
    :param body: 上传请求体（chat_id、file_paths、caption、delete_after、as_media_group）
    :param token: 认证 Token
    :return: 上传结果列表
    """
    file_manager = get_file_manager(request)
    if not file_manager:
        return error_json_response("文件管理服务不可用")

    # 获取 Telegram Client
    client = getattr(request.app.state, "client", None)
    if not client:
        return error_json_response("Telegram 客户端未连接")

    results = []

    try:
        if body.as_media_group and len(body.file_paths) > 1:
            # 媒体组上传
            file_infos = []
            for fp in body.file_paths:
                try:
                    fi = await file_manager.get_file_info(fp)
                    file_infos.append(fi)
                except Exception as e:
                    results.append(
                        {
                            "file_path": fp,
                            "success": False,
                            "error": str(e),
                        }
                    )

            if file_infos:
                upload_results = await file_manager.upload_media_group(
                    file_infos=file_infos,
                    chat_id=body.chat_id,
                    delete_after=body.delete_after,
                )
                for res in upload_results:
                    results.append(
                        {
                            "file_path": res.file_path,
                            "success": res.success,
                            "message_id": getattr(res.message, "id", None)
                            if res.message
                            else None,
                            "error": res.error_msg,
                        }
                    )
        else:
            # 单文件上传
            for fp in body.file_paths:
                res = await file_manager.upload(
                    file_path=fp,
                    chat_id=body.chat_id,
                    delete_after=body.delete_after,
                    caption=body.caption,
                )
                results.append(
                    {
                        "file_path": res.file_path,
                        "success": res.success,
                        "message_id": getattr(res.message, "id", None)
                        if res.message
                        else None,
                        "error": res.error_msg,
                        "deleted": res.deleted,
                    }
                )

        success_count = sum(1 for r in results if r.get("success"))
        return json_response(
            data={
                "total": len(results),
                "success": success_count,
                "failed": len(results) - success_count,
                "results": results,
            }
        )

    except Exception as e:
        return error_json_response("上传失败", str(e))

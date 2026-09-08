# coding=UTF-8
"""配置管理路由。

提供配置读取与更新功能，集成 ConfigManager，敏感字段脱敏。
"""

import logging

from fastapi import APIRouter, Depends, Request

from module.api.dependencies import get_config_manager, require_token
from module.api.models.config import (
    ConfigOut,
    ConfigUpdate,
    ProxyConfig,
    ResourceLimits,
)
from module.api.responses import error_json_response, json_response

router = APIRouter(prefix="/config", tags=["配置"])
logger = logging.getLogger(__name__)


@router.get("")
async def get_config(
    request: Request,
    token: str = Depends(require_token),
):
    """获取当前配置。

    敏感字段（api_id、api_hash、bot_token）自动脱敏为 '***'。
    包含 resource_limits 和 upload 配置。
    """
    config_manager = get_config_manager(request)

    try:
        # 从 ConfigManager 读取配置（已自动脱敏）
        config = config_manager.load_config(mask_sensitive=True)

        # 构建资源限制模型
        rl_data = config.get("resource_limits", {})
        resource_limits = ResourceLimits(
            task_size_warning_gb=rl_data.get("task_size_warning_gb", 5),
            task_size_max_gb=rl_data.get("task_size_max_gb", 10),
            min_disk_space_gb=rl_data.get("min_disk_space_gb", 2),
            memory_limit_mb=rl_data.get("memory_limit_mb", 512),
            max_concurrent_tasks=rl_data.get("max_concurrent_tasks", 1),
            max_download_concurrency=rl_data.get("max_download_concurrency", 3),
            max_upload_concurrency=rl_data.get("max_upload_concurrency", 1),
            max_forward_concurrency=rl_data.get("max_forward_concurrency", 1),
        )

        # 构建代理配置模型
        # 注意：config.yaml 中 enable_proxy 可能为 null（YAML 的 ~），
        # dict.get() 在 key 存在但值为 None 时返回 None 而非默认值。
        # 使用 `or False` 确保 None → False，满足 Pydantic bool 类型约束。
        proxy_data = config.get("proxy", {})
        enable_proxy = proxy_data.get("enable", False) or proxy_data.get(
            "enable_proxy", False
        )
        proxy_config = ProxyConfig(
            enable_proxy=enable_proxy if enable_proxy is not None else False,
            scheme=proxy_data.get("scheme"),
            hostname=proxy_data.get("hostname"),
            port=proxy_data.get("port"),
            username=proxy_data.get("username"),
            password=proxy_data.get("password"),
        )

        # 构建上传配置
        upload_data = config.get("upload", {})

        # 通知配置：读取 preference 区块
        pref_data = config.get("preference", {}) or {}

        result = ConfigOut(
            api_id=config.get("api_id", "***"),
            api_hash=config.get("api_hash", "***"),
            bot_token=config.get("bot_token", "***"),
            resource_limits=resource_limits,
            proxy=proxy_config,
            download_type=config.get("download_type", ["video", "photo"]),
            max_retry_count=config.get("max_retries", {}).get("download", 3),
            upload_delete_after=upload_data.get("delete_after_upload", False),
            upload_max_group_size=upload_data.get("max_group_size", 10),
            save_directory=config.get("save_directory", "downloads"),
            temp_directory=config.get("temp_directory", "temp"),
            notification_enabled=bool(pref_data.get("notification_enabled", False)),
            error_notification_enabled=bool(
                pref_data.get("error_notification_enabled", False)
            ),
        )

        return json_response(data=result.model_dump(mode="json"))
    except Exception as e:
        logger.error(f"读取配置失败: {e}")
        return error_json_response("读取配置失败", str(e))


@router.put("")
async def update_config(
    request: Request,
    body: ConfigUpdate,
    token: str = Depends(require_token),
):
    """更新配置。

    敏感字段（api_id、api_hash、bot_token）忽略更新。
    支持 resource_limits 和 upload 配置更新。
    """
    config_manager = get_config_manager(request)

    try:
        # 构建要保存的配置字典
        update_data = {}

        # 非敏感基础配置
        if body.download_type is not None:
            update_data["download_type"] = body.download_type
        if body.max_retry_count is not None:
            update_data.setdefault("max_retries", {})["download"] = body.max_retry_count
        if body.save_directory is not None:
            update_data["save_directory"] = body.save_directory
        if body.temp_directory is not None:
            update_data["temp_directory"] = body.temp_directory

        # 资源限制配置
        if body.resource_limits:
            rl = body.resource_limits
            update_data["resource_limits"] = {
                "task_size_warning_gb": rl.task_size_warning_gb,
                "task_size_max_gb": rl.task_size_max_gb,
                "min_disk_space_gb": rl.min_disk_space_gb,
                "memory_limit_mb": rl.memory_limit_mb,
                "max_concurrent_tasks": rl.max_concurrent_tasks,
                "max_download_concurrency": rl.max_download_concurrency,
                "max_upload_concurrency": rl.max_upload_concurrency,
                "max_forward_concurrency": rl.max_forward_concurrency,
            }

        # 代理配置
        if body.proxy:
            proxy = body.proxy
            update_data["proxy"] = {
                "enable": proxy.enable_proxy,
                "enable_proxy": proxy.enable_proxy,
            }
            if proxy.scheme:
                update_data["proxy"]["scheme"] = proxy.scheme
            if proxy.hostname:
                update_data["proxy"]["hostname"] = proxy.hostname
            if proxy.port:
                update_data["proxy"]["port"] = proxy.port
            if proxy.username:
                update_data["proxy"]["username"] = proxy.username
            if proxy.password:
                update_data["proxy"]["password"] = proxy.password

        # 上传配置
        upload_data = {}
        if body.upload_delete_after is not None:
            upload_data["delete_after_upload"] = body.upload_delete_after
        if body.upload_max_group_size is not None:
            upload_data["max_group_size"] = body.upload_max_group_size
        if upload_data:
            update_data["upload"] = upload_data

        # 通知配置：合并到 preference 区块，避免覆盖其它偏好字段
        if (
            body.notification_enabled is not None
            or body.error_notification_enabled is not None
        ):
            current = config_manager.load_config(mask_sensitive=False) or {}
            pref_data = dict(current.get("preference", {}) or {})
            if body.notification_enabled is not None:
                pref_data["notification_enabled"] = body.notification_enabled
            if body.error_notification_enabled is not None:
                pref_data["error_notification_enabled"] = (
                    body.error_notification_enabled
                )
            update_data["preference"] = pref_data

        # 验证并保存配置
        if update_data:
            save_ok = config_manager.save_config(update_data)
            if not save_ok:
                return error_json_response(
                    code=1,
                    message=(
                        "配置文件保存失败：配置文件不可写（可能为只读挂载或权限不足）。"
                        "若使用 Docker 部署，请检查 docker-compose.yml 中 config.yaml "
                        "挂载是否带 :ro 只读标志并移除；详情请查看服务端日志。"
                    ),
                )

        return json_response(data={"message": "配置已更新"})
    except Exception as e:
        logger.error(f"更新配置失败: {e}")
        return error_json_response("更新配置失败", str(e))

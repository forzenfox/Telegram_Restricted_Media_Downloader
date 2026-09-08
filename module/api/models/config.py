# coding=UTF-8
"""配置相关 Pydantic 数据模型。"""

from pydantic import BaseModel, Field


class ResourceLimits(BaseModel):
    """资源限制配置。"""

    max_concurrent_tasks: int = 1
    max_download_concurrency: int = 3
    max_upload_concurrency: int = 1
    max_forward_concurrency: int = 1
    min_disk_space_gb: int = 2
    memory_limit_mb: int = 512
    task_size_warning_gb: int = 5
    task_size_max_gb: int = 10


class ProxyConfig(BaseModel):
    """代理配置。"""

    enable_proxy: bool = False
    scheme: str | None = None
    hostname: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None


class ConfigOut(BaseModel):
    """配置响应数据。"""

    api_id: str | None = None
    api_hash: str | None = None
    bot_token: str | None = None
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    download_type: list[str] = Field(default_factory=lambda: ["video", "photo"])
    max_retry_count: int = 3
    save_directory: str | None = None
    temp_directory: str | None = None
    upload_delete_after: bool = False
    upload_max_group_size: int = 10
    # 通知配置（持久化于 config.yaml 的 preference 区块）
    notification_enabled: bool = False
    error_notification_enabled: bool = False


class ConfigUpdate(BaseModel):
    """配置更新请求体。"""

    resource_limits: ResourceLimits | None = None
    proxy: ProxyConfig | None = None
    download_type: list[str] | None = None
    max_retry_count: int | None = None
    save_directory: str | None = None
    temp_directory: str | None = None
    upload_delete_after: bool | None = None
    upload_max_group_size: int | None = None
    # 通知配置（持久化于 config.yaml 的 preference 区块）
    notification_enabled: bool | None = None
    error_notification_enabled: bool | None = None

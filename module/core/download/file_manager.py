# coding=UTF-8
# FileManager 模块：核心文件管理层，为 Bot 与 WebUI 提供统一的本地文件操作能力。
import os
import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Callable, Awaitable

from module import log
from module.utils.path_tool import safe_delete

# ============================================================
# 常量定义
# ============================================================


class FileManagerConstants:
    """FileManager 模块常量。"""

    MAX_MEDIA_GROUP_SIZE: int = 10
    DEFAULT_MEMORY_LIMIT_MB: int = 512
    DEFAULT_DELETE_AFTER_UPLOAD: bool = False
    FORWARD_DELETE_AFTER_UPLOAD: bool = True

    SUPPORTED_ALBUM_TYPES: set = {"photo", "video", "audio"}
    UNSUPPORTED_ALBUM_TYPES: set = {"document", "sticker", "animation"}


# ============================================================
# 数据模型
# ============================================================


@dataclass
class FileInfo:
    """描述一个本地文件或目录的元数据。"""

    path: str  # 绝对路径
    name: str  # 文件/目录名
    is_directory: bool  # 是否为目录
    size: int  # 文件大小（字节），目录为 0
    mime_type: str | None  # MIME 类型，目录为 None
    extension: str | None  # 扩展名（小写，不含点），目录为 None
    modified_time: float  # 最后修改时间戳
    sha256: str | None = None  # 文件 SHA256（上传前按需计算）
    is_selected: bool = False  # 是否被用户/WebUI 选中
    telegram_type: (
        Literal[
            "photo",
            "video",
            "audio",
            "voice",
            "document",
            "animation",
            "sticker",
            "unsupported",
        ]
        | None
    ) = None  # 按 Telegram 语义分类


@dataclass
class UploadResult:
    """描述一次上传任务的最终结果。"""

    success: bool  # 是否成功
    file_path: str | None = None  # 本地文件路径
    message: object | None = None  # Pyrogram 返回的 Message 对象（成功时）
    error_code: str | None = None  # 错误码（失败时）
    error_msg: str | None = None  # 可读错误信息
    deleted: bool = False  # 本地文件是否已清理
    file_unique_id: str | None = None  # 文件唯一标识（成功时从 Pyrogram Message 提取）


@dataclass
class MediaGroupConfig:
    """媒体组上传配置。"""

    max_group_size: int = 10  # 每组最大文件数，默认且最大为 10
    sort_by: str = "name"  # 排序字段：name / time / size / none
    sort_order: str = "asc"  # 排序方向：asc / desc
    send_as_album: bool = True  # 是否尝试以媒体组发送
    fallback_to_single: bool = True  # 媒体组失败时是否降级为单文件发送


@dataclass
class UploadProgress:
    """上传进度回调数据结构。"""

    task_id: str  # 任务/文件唯一标识
    file_path: str  # 当前文件路径
    current: int  # 当前已上传字节
    total: int  # 文件总字节
    percentage: float  # 上传百分比
    status: str  # pending / uploading / success / failed


# ============================================================
# 异常体系
# ============================================================


@dataclass
class DeleteCheckResult:
    """描述一次删除路径预检的结论。"""

    path: str  # 规范化绝对路径
    ok: bool  # 是否允许删除
    reason: str | None = None  # 拒绝原因：OUT_OF_BOUNDS / SYSTEM_PATH / IS_DIRECTORY


class FileManagerError(Exception):
    """FileManager 基础异常类。"""

    def __init__(self, code: str, message: str, file_path: str | None = None):
        self.code = code
        self.message = message
        self.file_path = file_path
        super().__init__(message)


class FileNotFound(FileManagerError):
    """文件不存在异常。"""

    pass


class UploadSizeLimit(FileManagerError):
    """上传大小超限异常。"""

    pass


class MediaGroupInvalid(FileManagerError):
    """媒体组无效异常。"""

    pass


# ============================================================
# 核心类
# ============================================================


class FileManager:
    """核心文件管理器，提供文件浏览、选择、上传、清理等操作。"""

    # Windows 系统关键目录黑名单。
    _SYSTEM_PATH_BLACKLIST = (
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
    )

    def __init__(
        self,
        config: dict,
        client: object,
        progress_callback: Callable[[UploadProgress], Awaitable[None]] | None = None,
    ):
        """
        初始化 FileManager。

        Args:
            config: 配置字典，至少包含 resource_limits.memory_limit_mb、
                    upload.max_group_size、upload.delete_after_upload 等键。
            client: 已授权的 Pyrogram Client 实例。
            progress_callback: 可选的全局上传进度回调。
        """
        self._config = config
        self._client = client
        self._progress_callback = progress_callback
        self.repository_manager = None  # 可选的 RepositoryManager，由外部设置

        # 读取配置。
        resource_limits = config.get("resource_limits", {})
        self._memory_limit_mb = resource_limits.get(
            "memory_limit_mb", FileManagerConstants.DEFAULT_MEMORY_LIMIT_MB
        )

        upload_config = config.get("upload", {})
        self._max_group_size = min(
            upload_config.get(
                "max_group_size", FileManagerConstants.MAX_MEDIA_GROUP_SIZE
            ),
            FileManagerConstants.MAX_MEDIA_GROUP_SIZE,
        )
        self._delete_after_upload = upload_config.get(
            "delete_after_upload", FileManagerConstants.DEFAULT_DELETE_AFTER_UPLOAD
        )

    # ---------- 文件浏览与选择 ----------

    async def list_files(
        self,
        path: str,
        recursive: bool = False,
        include_hidden: bool = False,
    ) -> list[FileInfo]:
        """列出指定路径下的文件与目录。"""
        abs_path = os.path.abspath(os.path.normpath(path))

        # 路径校验。
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"路径不存在: {abs_path}")
        if not os.path.isdir(abs_path):
            raise NotADirectoryError(f"路径不是目录: {abs_path}")

        # 系统关键目录黑名单检查。
        self._check_system_path(abs_path)

        result: list[FileInfo] = []
        self._scan_directory(abs_path, recursive, include_hidden, result)
        return result

    def _scan_directory(
        self,
        directory: str,
        recursive: bool,
        include_hidden: bool,
        result: list[FileInfo],
    ) -> None:
        """递归扫描目录。"""
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    # 隐藏文件过滤。
                    if not include_hidden and self._is_hidden(entry.name):
                        continue

                    if entry.is_dir():
                        info = self._build_dir_info(
                            entry.path, entry.name, entry.stat().st_mtime
                        )
                        result.append(info)
                        if recursive:
                            self._scan_directory(
                                entry.path, recursive, include_hidden, result
                            )
                    elif entry.is_file():
                        info = self._build_file_info(
                            entry.path, entry.name, entry.stat()
                        )
                        result.append(info)
        except PermissionError as e:
            log.warning(f'权限不足，无法扫描目录 "{directory}": {e}')

    async def get_file_info(self, path: str) -> FileInfo:
        """获取单个文件或目录的详细信息。"""
        abs_path = os.path.abspath(os.path.normpath(path))

        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"路径不存在: {abs_path}")

        stat = os.stat(abs_path)

        if os.path.isdir(abs_path):
            return self._build_dir_info(
                abs_path, os.path.basename(abs_path), stat.st_mtime
            )
        else:
            return self._build_file_info(abs_path, os.path.basename(abs_path), stat)

    async def select_files(
        self,
        paths: list[str],
        allowed_extensions: list[str] | None = None,
    ) -> list[FileInfo]:
        """将一组路径转换为 FileInfo 列表，过滤不存在/不可读的文件。"""
        # 去重并保持顺序。
        seen = set()
        unique_paths = []
        for p in paths:
            abs_p = os.path.abspath(os.path.normpath(p))
            if abs_p not in seen:
                seen.add(abs_p)
                unique_paths.append(abs_p)

        result: list[FileInfo] = []
        for abs_path in unique_paths:
            if not os.path.exists(abs_path):
                log.warning(f"路径不存在，已跳过: {abs_path}")
                continue

            try:
                if os.path.isdir(abs_path):
                    # 目录递归收集其下所有非隐藏文件。
                    dir_files: list[FileInfo] = []
                    self._scan_directory(
                        abs_path,
                        recursive=False,
                        include_hidden=False,
                        result=dir_files,
                    )
                    # 只取文件，不取子目录。
                    files_only = [f for f in dir_files if not f.is_directory]
                    result.extend(files_only)
                else:
                    info = self._build_file_info(
                        abs_path, os.path.basename(abs_path), os.stat(abs_path)
                    )
                    result.append(info)
            except PermissionError as e:
                log.warning(f"权限不足，已跳过: {abs_path}, 原因: {e}")
                continue

        # 按扩展名过滤。
        if allowed_extensions:
            allowed_lower = {ext.lower() for ext in allowed_extensions}
            result = [
                f
                for f in result
                if f.extension and f".{f.extension}".lower() in allowed_lower
            ]

        return result

    async def get_directory_size(self, path: str) -> int:
        """递归计算目录总大小（字节）。"""
        abs_path = os.path.abspath(os.path.normpath(path))
        if not os.path.exists(abs_path) or not os.path.isdir(abs_path):
            return 0

        total_size = 0
        for dirpath, dirnames, filenames in os.walk(abs_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(file_path)
                except (OSError, PermissionError):
                    pass
        return total_size

    # ---------- 媒体组拆分 ----------

    async def split_media_group(
        self,
        file_infos: list[FileInfo],
        config: MediaGroupConfig | None = None,
    ) -> list[list[FileInfo]]:
        """将文件列表按类型拆分：媒体组（album_compatible）每 10 个一组，不支持的类型走单文件。

        返回格式为 list[dict]，每个 dict 包含：
            - 'is_album': bool - 是否为媒体组
            - 'files': list[FileInfo] - 文件列表
        """
        if not file_infos:
            return []

        if config is None:
            config = MediaGroupConfig()

        # 确保 max_group_size 不超过上限。
        max_size = min(config.max_group_size, FileManagerConstants.MAX_MEDIA_GROUP_SIZE)

        album_compatible, single_only = await self._classify_files(file_infos)

        groups: list[dict] = []

        # 媒体组文件按 max_size 切块。
        if config.send_as_album and album_compatible:
            for i in range(0, len(album_compatible), max_size):
                chunk = album_compatible[i : i + max_size]
                groups.append({"is_album": True, "files": chunk})
        else:
            # 如果不以媒体组发送，全部走单文件。
            single_only.extend(album_compatible)

        # 不支持的文件走单文件。
        for f in single_only:
            groups.append({"is_album": False, "files": [f]})

        return groups

    # ---------- 上传方法 ----------

    async def upload(
        self,
        file_path: str,
        chat_id: int,
        progress_callback: Callable[[UploadProgress], Awaitable[None]] | None = None,
        delete_after: bool = False,
        caption: str = "",
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
        content_hash: str | None = None,
    ) -> UploadResult:
        """上传单个文件到 Telegram 频道/群组。

        根据文件类型自动选择合适的发送方法：
            - 图片：send_photo
            - 视频：send_video
            - 音频：send_audio
            - 动画(GIF)：send_animation
            - 其他：send_document

        当仓库模式启用且提供了 source_chat_id/source_message_id 时：
            - 如果上传目标是仓库频道，上传成功后调用 on_upload_success 写入仓库记录
            - 如果上传目标不是仓库频道，先上传到仓库频道，再分发到目标频道

        Args:
            file_path: 本地文件绝对路径
            chat_id: 目标频道/群组的 chat_id
            progress_callback: 上传进度回调函数
            delete_after: 上传成功后是否删除本地文件
            caption: 文件说明文字
            source_chat_id: 源频道 ID（仓库模式使用）
            source_message_id: 源消息 ID（仓库模式使用）

        Returns:
            UploadResult: 上传结果
        """
        abs_path = os.path.abspath(os.path.normpath(file_path))

        # 文件存在性检查
        if not os.path.exists(abs_path):
            return UploadResult(
                success=False,
                file_path=abs_path,
                error_code="FILE_NOT_FOUND",
                error_msg=f"文件不存在: {abs_path}",
            )

        # 文件大小检查
        try:
            file_size = os.path.getsize(abs_path)
        except OSError as e:
            return UploadResult(
                success=False,
                file_path=abs_path,
                error_code="FILE_SIZE_ERROR",
                error_msg=f"无法获取文件大小: {e}",
            )

        if file_size == 0:
            return UploadResult(
                success=False,
                file_path=abs_path,
                error_code="EMPTY_FILE",
                error_msg="文件大小为0，无法上传",
            )

        # 上传大小限制检查
        max_upload = self._memory_limit_mb * 1024 * 1024
        if file_size > max_upload:
            return UploadResult(
                success=False,
                file_path=abs_path,
                error_code="SIZE_LIMIT_EXCEEDED",
                error_msg=f"文件大小 {file_size} 超过限制 {max_upload} 字节",
            )

        # 安全检查
        self._check_system_path(abs_path)

        # 获取文件类型
        file_info = self._build_file_info(
            abs_path, os.path.basename(abs_path), os.stat(abs_path)
        )
        telegram_type = file_info.telegram_type or "document"

        # 构建进度回调
        task_id = hashlib.md5(abs_path.encode()).hexdigest()[:12]

        async def _progress(current, total):
            await self._progress_wrapper(
                task_id, abs_path, current, total, progress_callback
            )

        # 根据类型调用不同的发送方法
        try:
            if telegram_type == "photo":
                message = await self._client.send_photo(
                    chat_id=chat_id,
                    photo=abs_path,
                    caption=caption,
                    progress=_progress,
                )
            elif telegram_type == "video":
                message = await self._client.send_video(
                    chat_id=chat_id,
                    video=abs_path,
                    caption=caption,
                    progress=_progress,
                )
            elif telegram_type == "audio":
                message = await self._client.send_audio(
                    chat_id=chat_id,
                    audio=abs_path,
                    caption=caption,
                    progress=_progress,
                )
            elif telegram_type == "animation":
                message = await self._client.send_animation(
                    chat_id=chat_id,
                    animation=abs_path,
                    caption=caption,
                    progress=_progress,
                )
            else:
                # document 类型
                message = await self._client.send_document(
                    chat_id=chat_id,
                    document=abs_path,
                    caption=caption,
                    progress=_progress,
                )

            result = UploadResult(
                success=True,
                file_path=abs_path,
                message=message,
                file_unique_id=self._extract_file_unique_id(message),
            )

            # 仓库模式：上传到仓库频道时写入仓库记录
            if (
                self.repository_manager is not None
                and self.repository_manager.should_use_repository()
                and source_chat_id is not None
                and source_message_id is not None
                and str(chat_id) == self.repository_manager.get_repository_chat_id()
            ):
                try:
                    await self.repository_manager.on_upload_success(
                        message=message,
                        source_chat_id=source_chat_id,
                        source_message_id=source_message_id,
                        content_hash=content_hash,
                    )
                except Exception as e:
                    log.warning(f"仓库记录写入失败: {e}")

            # 上传后清理
            if delete_after:
                result.deleted = await self.delete_local_file(abs_path)
                if not result.deleted:
                    log.warning(f"上传后清理失败: {abs_path}")

            return result

        except Exception as e:
            log.error(f"上传失败: {abs_path}, 原因: {e}")
            return UploadResult(
                success=False,
                file_path=abs_path,
                error_code="UPLOAD_FAILED",
                error_msg=str(e),
            )

    async def upload_media_group(
        self,
        file_infos: list[FileInfo],
        chat_id: int,
        progress_callback: Callable[[UploadProgress], Awaitable[None]] | None = None,
        delete_after: bool = False,
        config: MediaGroupConfig | None = None,
    ) -> list[UploadResult]:
        """上传媒体组（多个文件组合为一个消息）。

        支持的类型：photo, video, audio（需在同一个媒体组中）。

        Args:
            file_infos: 文件信息列表（必须是同一种媒体组兼容类型）
            chat_id: 目标频道/群组的 chat_id
            progress_callback: 上传进度回调函数
            delete_after: 上传成功后是否删除本地文件
            config: 媒体组配置

        Returns:
            list[UploadResult]: 上传结果列表
        """
        if not file_infos:
            return []

        if config is None:
            config = MediaGroupConfig()

        results: list[UploadResult] = []

        # 检查所有文件是否为媒体组兼容类型
        for fi in file_infos:
            if fi.telegram_type not in FileManagerConstants.SUPPORTED_ALBUM_TYPES:
                # 降级为单文件上传
                log.warning(f"文件 {fi.path} 不支持媒体组，降级为单文件上传")
                result = await self.upload(
                    file_path=fi.path,
                    chat_id=chat_id,
                    progress_callback=progress_callback,
                    delete_after=delete_after,
                )
                results.append(result)
                return results

        # 构建媒体组 InputMedia 列表
        from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio

        media_list = []
        for fi in file_infos:
            if fi.telegram_type == "photo":
                media_list.append(InputMediaPhoto(media=fi.path))
            elif fi.telegram_type == "video":
                media_list.append(InputMediaVideo(media=fi.path))
            elif fi.telegram_type == "audio":
                media_list.append(InputMediaAudio(media=fi.path))
            else:
                # 不支持的类型，降级处理
                log.warning(f"文件 {fi.path} 类型 {fi.telegram_type} 不支持媒体组")
                continue

        if not media_list:
            return [
                UploadResult(
                    success=False,
                    error_code="NO_VALID_MEDIA",
                    error_msg="没有有效的媒体文件",
                )
            ]

        # 发送媒体组
        try:
            messages = await self._client.send_media_group(
                chat_id=chat_id,
                media=media_list,
            )

            # 构建结果（每条消息对应一个文件）
            for i, fi in enumerate(file_infos):
                msg = messages[i] if i < len(messages) else (messages[0] if messages else None)
                result = UploadResult(
                    success=True,
                    file_path=fi.path,
                    message=msg,
                    file_unique_id=self._extract_file_unique_id(msg) if msg else None,
                )

                if delete_after:
                    result.deleted = await self.delete_local_file(fi.path)
                    if not result.deleted:
                        log.warning(f"上传后清理失败: {fi.path}")

                results.append(result)

            return results

        except Exception as e:
            log.error(f"媒体组上传失败: {e}")
            if config.fallback_to_single:
                log.info("降级为单文件上传模式")
                results = []
                for fi in file_infos:
                    result = await self.upload(
                        file_path=fi.path,
                        chat_id=chat_id,
                        progress_callback=progress_callback,
                        delete_after=delete_after,
                    )
                    results.append(result)
                return results
            else:
                return [
                    UploadResult(
                        success=False,
                        error_code="MEDIA_GROUP_FAILED",
                        error_msg=str(e),
                    )
                ]

    async def _classify_files(
        self,
        file_infos: list[FileInfo],
    ) -> tuple[list[FileInfo], list[FileInfo]]:
        """将文件列表分类为 album_compatible 和 single_only。"""
        album_compatible: list[FileInfo] = []
        single_only: list[FileInfo] = []

        for fi in file_infos:
            if fi.telegram_type in FileManagerConstants.SUPPORTED_ALBUM_TYPES:
                album_compatible.append(fi)
            else:
                single_only.append(fi)

        return album_compatible, single_only

    # ---------- 清理接口 ----------

    async def delete_local_file(self, file_path: str) -> bool:
        """安全删除本地文件或空目录，返回是否成功。"""
        abs_path = os.path.abspath(os.path.normpath(file_path))

        # 安全检查：不删除系统关键目录。
        self._check_system_path(abs_path)

        return safe_delete(abs_path)

    async def scan_expired_files(
        self,
        root: str,
        keep_days: int,
        batch_size: int = 1000,
        referenced_paths: set[str] | None = None,
    ) -> list[FileInfo]:
        """递归扫描根目录下的过期文件（供定时清理使用）。

        过期判定：``file.mtime < now - keep_days 天``（严格大于保留天数才算过期）。
        过滤规则：跳过隐藏文件、跳过写入中的 ``.temp`` 文件、
        跳过被活跃任务引用（referenced_paths）的文件。

        Args:
            root: 扫描根目录绝对路径
            keep_days: 保留天数
            batch_size: 内部累积批大小上限（防大目录一次驻留过多内存）
            referenced_paths: 任务引用保护索引（绝对路径集合）

        Returns:
            满足过期条件的 FileInfo 列表
        """
        abs_root = os.path.abspath(os.path.normpath(root))
        if not os.path.exists(abs_root) or not os.path.isdir(abs_root):
            return []

        # 系统目录黑名单检查。
        self._check_system_path(abs_root)

        cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
        referenced = {
            os.path.abspath(os.path.normpath(p)) for p in (referenced_paths or set())
        }

        result: list[FileInfo] = []
        pending: list[FileInfo] = []

        def _accumulate(info: FileInfo) -> None:
            pending.append(info)
            if len(pending) >= batch_size:
                result.extend(pending)
                pending.clear()

        def _scan(directory: str) -> None:
            try:
                entries = list(os.scandir(directory))
            except PermissionError as e:
                log.warning(f'权限不足，无法扫描目录 "{directory}": {e}')
                return

            for entry in entries:
                # 隐藏文件过滤。
                if self._is_hidden(entry.name):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        _scan(entry.path)
                    elif entry.is_file():
                        if entry.name.endswith(".temp"):
                            continue
                        abs_path = os.path.abspath(entry.path)
                        if abs_path in referenced or f"{abs_path}.temp" in referenced:
                            continue
                        try:
                            stat = entry.stat()
                        except OSError:
                            continue
                        if stat.st_mtime < cutoff:
                            _accumulate(
                                self._build_file_info(abs_path, entry.name, stat)
                            )
                except OSError:
                    continue

        _scan(abs_root)
        result.extend(pending)
        return result

    async def precheck_delete_paths(
        self,
        paths: list[str],
        save_root: str,
    ) -> list[DeleteCheckResult]:
        """批量路径安全预检（不执行删除）。

        单条检查顺序：
          1. 绝对化 + normpath
          2. 必须在 save_root 之下（防越界）
          3. 系统目录黑名单
          4. 不存在 → 视为已删除（幂等，ok=True）
          5. 是目录 → 拒绝

        Args:
            paths: 待删除文件路径列表
            save_root: 下载根目录绝对路径

        Returns:
            list[DeleteCheckResult]：与输入 paths 一一对应
        """
        root = os.path.abspath(os.path.normpath(save_root))
        results: list[DeleteCheckResult] = []

        for p in paths:
            abs_path = os.path.abspath(os.path.normpath(p))
            # 越界检查。
            if not abs_path.startswith(root):
                results.append(
                    DeleteCheckResult(path=abs_path, ok=False, reason="OUT_OF_BOUNDS")
                )
                continue
            # 系统目录黑名单。
            try:
                self._check_system_path(abs_path)
            except PermissionError:
                results.append(
                    DeleteCheckResult(path=abs_path, ok=False, reason="SYSTEM_PATH")
                )
                continue
            # 不存在：幂等视为已删除。
            if not os.path.exists(abs_path):
                results.append(DeleteCheckResult(path=abs_path, ok=True))
                continue
            # 仅支持文件。
            if os.path.isdir(abs_path):
                results.append(
                    DeleteCheckResult(path=abs_path, ok=False, reason="IS_DIRECTORY")
                )
                continue

            results.append(DeleteCheckResult(path=abs_path, ok=True))

        return results

    async def delete_many(
        self,
        paths: list[str],
        save_root: str,
        referenced_paths: set[str] | None = None,
    ) -> dict:
        """批量删除文件（每条均过预检 + 任务引用保护）。

        Args:
            paths: 待删除文件路径列表
            save_root: 下载根目录绝对路径
            referenced_paths: 任务引用保护索引（绝对路径集合）

        Returns:
            {"total", "deleted", "failed", "skipped", "results"}
            results[i] = {"file_path", "success", "deleted", "skipped", "reason"}
        """
        root = os.path.abspath(os.path.normpath(save_root))
        referenced = {
            os.path.abspath(os.path.normpath(p)) for p in (referenced_paths or set())
        }

        checks = await self.precheck_delete_paths(paths, save_root=root)

        total = len(checks)
        deleted = 0
        failed = 0
        skipped = 0
        results: list[dict] = []

        for check in checks:
            entry = {
                "file_path": check.path,
                "success": False,
                "deleted": False,
                "skipped": False,
                "reason": check.reason,
            }

            # 预检拒绝（越界/系统目录/目录）→ 失败。
            if not check.ok:
                failed += 1
                results.append(entry)
                continue

            # 任务引用保护：进行中的文件或被活跃任务引用的文件跳过。
            if check.path in referenced or f"{check.path}.temp" in referenced:
                entry["skipped"] = True
                entry["reason"] = "task_referenced"
                skipped += 1
                results.append(entry)
                continue

            # 缺失文件：幂等视为已删除。
            if not os.path.exists(check.path):
                entry["success"] = True
                entry["deleted"] = True
                entry["reason"] = None
                deleted += 1
                results.append(entry)
                continue

            # 执行删除。
            try:
                ok = await self.delete_local_file(check.path)
            except PermissionError:
                ok = False
            if ok:
                entry["success"] = True
                entry["deleted"] = True
                entry["reason"] = None
                deleted += 1
            else:
                failed += 1
            results.append(entry)

        return {
            "total": total,
            "deleted": deleted,
            "failed": failed,
            "skipped": skipped,
            "results": results,
        }

    async def cleanup_after_upload(
        self,
        results: list[UploadResult],
        delete_after_upload: bool = True,
    ) -> list[UploadResult]:
        """根据策略批量清理已上传文件的本地副本。"""
        if not delete_after_upload:
            return results

        for res in results:
            if not res.success:
                continue
            if res.file_path and os.path.exists(res.file_path):
                res.deleted = await self.delete_local_file(res.file_path)
                if not res.deleted:
                    log.warning(f"上传后清理失败: {res.file_path}")
        return results

    # ---------- 上传进度回调 ----------

    async def _progress_wrapper(
        self,
        task_id: str,
        file_path: str,
        current: int,
        total: int,
        callback: Callable[[UploadProgress], Awaitable[None]] | None,
    ):
        """进度回调包装器。"""
        progress = UploadProgress(
            task_id=task_id,
            file_path=file_path,
            current=current,
            total=total,
            percentage=round(current / total * 100, 2) if total else 0,
            status="uploading",
        )

        if callback:
            await callback(progress)
        elif self._progress_callback:
            await self._progress_callback(progress)

    # ---------- 内部工具方法 ----------

    @staticmethod
    def _extract_file_unique_id(message) -> str | None:
        """从 Pyrogram Message 对象中提取 file_unique_id。

        按优先级依次检查 photo/video/document/audio/animation 属性。

        Args:
            message: Pyrogram Message 对象

        Returns:
            file_unique_id 字符串，未找到时返回 None
        """
        for attr in ("photo", "video", "document", "audio", "animation"):
            media = getattr(message, attr, None)
            if media:
                return getattr(media, "file_unique_id", None)
        return None

    def _check_system_path(self, path: str) -> None:
        """检查路径是否在系统关键目录黑名单中。"""
        abs_path = os.path.abspath(os.path.normpath(path))
        normalized = abs_path.lower()
        for blacklist_path in self._SYSTEM_PATH_BLACKLIST:
            if normalized.startswith(blacklist_path.lower()):
                raise PermissionError(f"禁止操作系统关键目录: {abs_path}")

    @staticmethod
    def _is_hidden(name: str) -> bool:
        """判断文件或目录是否为隐藏。
        Windows 下通过 ctypes 检查文件属性，Linux/macOS 下检查是否以 '.' 开头。
        """
        if os.name == "nt":
            # Windows: 使用 ctypes 检查 FILE_ATTRIBUTE_HIDDEN (0x2) 和 FILE_ATTRIBUTE_SYSTEM (0x4)。
            import ctypes

            try:
                attrs = ctypes.windll.kernel32.GetFileAttributesW(name)
                if attrs == -1:
                    return name.startswith(".")
                return bool(attrs & 0x6)  # HIDDEN | SYSTEM
            except Exception:
                return name.startswith(".")
        else:
            # Linux/macOS: 以 '.' 开头的文件视为隐藏。
            return name.startswith(".")

    def _build_file_info(
        self, path: str, name: str, stat_result: os.stat_result
    ) -> FileInfo:
        """构建文件的 FileInfo。"""
        ext = os.path.splitext(name)[1].lstrip(".").lower() if "." in name else None
        mime_type = self._guess_mime_type(name)
        telegram_type = self._classify_telegram_type(name, mime_type)

        return FileInfo(
            path=path,
            name=name,
            is_directory=False,
            size=stat_result.st_size,
            mime_type=mime_type,
            extension=ext,
            modified_time=stat_result.st_mtime,
            telegram_type=telegram_type,
        )

    @staticmethod
    def _build_dir_info(path: str, name: str, modified_time: float) -> FileInfo:
        """构建目录的 FileInfo。"""
        return FileInfo(
            path=path,
            name=name,
            is_directory=True,
            size=0,
            mime_type=None,
            extension=None,
            modified_time=modified_time,
        )

    @staticmethod
    def _guess_mime_type(name: str) -> str | None:
        """根据文件名猜测 MIME 类型。"""
        mime_type, _ = mimetypes.guess_type(name)
        return mime_type

    @staticmethod
    def _classify_telegram_type(name: str, mime_type: str | None) -> str | None:
        """根据文件名和 MIME 类型推导 Telegram 类型。"""
        ext = os.path.splitext(name)[1].lower() if "." in name else ""

        # 图片类型。
        if mime_type and mime_type.startswith("image/"):
            if ext == ".gif":
                return "animation"
            return "photo"

        # 视频类型。
        if mime_type and mime_type.startswith("video/"):
            return "video"

        # 音频类型。
        if mime_type and mime_type.startswith("audio/"):
            return "audio"

        # 基于扩展名二次判断。
        photo_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
            ".webp",
            ".avif",
            ".heic",
            ".heif",
        }
        video_exts = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"}
        audio_exts = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wav"}
        animation_exts = {".gif"}
        sticker_exts = {".tgs", ".webm"}  # 动态贴纸。

        if ext in animation_exts:
            return "animation"
        if ext in photo_exts:
            return "photo"
        if ext in video_exts:
            return "video"
        if ext in audio_exts:
            return "audio"
        if ext in sticker_exts:
            return "sticker"

        # 其他均视为 document。
        return "document"

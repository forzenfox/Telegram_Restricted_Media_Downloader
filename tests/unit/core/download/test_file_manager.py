# coding=UTF-8
# FileManager 模块单元测试。
import os
import time
import pytest
from unittest.mock import AsyncMock

# 确保项目根目录在 sys.path 中。
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module.core.download.file_manager import (
    FileInfo,
    UploadResult,
    MediaGroupConfig,
    UploadProgress,
    FileManagerConstants,
    FileManager,
    FileManagerError,
    FileNotFound,
    UploadSizeLimit,
    MediaGroupInvalid,
)


# ============================================================
# 数据模型测试
# ============================================================


class TestFileInfo:
    """测试 FileInfo 数据类。"""

    def test_create_file_info(self, tmp_path):
        file_path = tmp_path / "test.jpg"
        file_path.write_bytes(b"hello")
        info = FileInfo(
            path=str(file_path),
            name="test.jpg",
            is_directory=False,
            size=5,
            mime_type="image/jpeg",
            extension="jpg",
            modified_time=os.path.getmtime(str(file_path)),
        )
        assert info.is_directory is False
        assert info.size == 5
        assert info.mime_type == "image/jpeg"

    def test_create_directory_info(self, tmp_path):
        dir_path = tmp_path / "subdir"
        dir_path.mkdir()
        info = FileInfo(
            path=str(dir_path),
            name="subdir",
            is_directory=True,
            size=0,
            mime_type=None,
            extension=None,
            modified_time=os.path.getmtime(str(dir_path)),
        )
        assert info.is_directory is True
        assert info.size == 0
        assert info.mime_type is None

    def test_is_selected_default(self):
        info = FileInfo(
            path="/tmp/test.jpg",
            name="test.jpg",
            is_directory=False,
            size=100,
            mime_type="image/jpeg",
            extension="jpg",
            modified_time=0.0,
        )
        assert info.is_selected is False


class TestUploadResult:
    """测试 UploadResult 数据类。"""

    def test_create_success_result(self):
        result = UploadResult(success=True, file_path="/tmp/test.jpg", message="msg")
        assert result.success is True
        assert result.deleted is False

    def test_create_failure_result(self):
        result = UploadResult(
            success=False, error_code="FILE_NOT_FOUND", error_msg="文件不存在"
        )
        assert result.success is False
        assert result.error_code == "FILE_NOT_FOUND"


class TestMediaGroupConfig:
    """测试 MediaGroupConfig 数据类。"""

    def test_default_values(self):
        config = MediaGroupConfig()
        assert config.max_group_size == 10
        assert config.sort_by == "name"
        assert config.sort_order == "asc"
        assert config.send_as_album is True
        assert config.fallback_to_single is True


class TestUploadProgress:
    """测试 UploadProgress 数据类。"""

    def test_create_progress(self):
        progress = UploadProgress(
            task_id="task1",
            file_path="/tmp/test.jpg",
            current=500,
            total=1000,
            percentage=50.0,
            status="uploading",
        )
        assert progress.current == 500
        assert progress.percentage == 50.0


# ============================================================
# 常量测试
# ============================================================


class TestFileManagerConstants:
    """测试 FileManagerConstants。"""

    def test_max_media_group_size(self):
        assert FileManagerConstants.MAX_MEDIA_GROUP_SIZE == 10

    def test_default_memory_limit_mb(self):
        assert FileManagerConstants.DEFAULT_MEMORY_LIMIT_MB == 512

    def test_supported_album_types(self):
        assert "photo" in FileManagerConstants.SUPPORTED_ALBUM_TYPES
        assert "video" in FileManagerConstants.SUPPORTED_ALBUM_TYPES
        assert "audio" in FileManagerConstants.SUPPORTED_ALBUM_TYPES

    def test_unsupported_album_types(self):
        assert "document" in FileManagerConstants.UNSUPPORTED_ALBUM_TYPES
        assert "sticker" in FileManagerConstants.UNSUPPORTED_ALBUM_TYPES
        assert "animation" in FileManagerConstants.UNSUPPORTED_ALBUM_TYPES


# ============================================================
# 异常测试
# ============================================================


class TestExceptions:
    """测试自定义异常类。"""

    def test_file_manager_error(self):
        err = FileManagerError(
            code="TEST", message="测试错误", file_path="/tmp/test.jpg"
        )
        assert err.code == "TEST"
        assert err.message == "测试错误"
        assert err.file_path == "/tmp/test.jpg"

    def test_file_not_found(self):
        err = FileNotFound(
            code="FILE_NOT_FOUND", message="文件不存在", file_path="/tmp/missing.jpg"
        )
        assert isinstance(err, FileManagerError)

    def test_upload_size_limit(self):
        err = UploadSizeLimit(code="UPLOAD_SIZE_LIMIT", message="文件过大")
        assert isinstance(err, FileManagerError)

    def test_media_group_invalid(self):
        err = MediaGroupInvalid(code="MEDIA_GROUP_INVALID", message="媒体组无效")
        assert isinstance(err, FileManagerError)


# ============================================================
# FileManager - 文件浏览与选择测试
# ============================================================


@pytest.fixture
def mock_client():
    """创建模拟的 Pyrogram Client。"""
    client = AsyncMock()
    return client


@pytest.fixture
def default_config():
    """默认配置字典。"""
    return {
        "resource_limits": {
            "memory_limit_mb": 512,
        },
        "upload": {
            "max_group_size": 10,
            "delete_after_upload": False,
        },
    }


@pytest.fixture
def file_manager(mock_client, default_config):
    """创建 FileManager 实例。"""
    return FileManager(config=default_config, client=mock_client)


class TestListFiles:
    """FM-LIST 系列：文件浏览测试。"""

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, file_manager, tmp_path):
        """FM-LIST-01: 列出空目录。"""
        result = await file_manager.list_files(str(tmp_path))
        assert result == []

    @pytest.mark.asyncio
    async def test_list_directory_with_files_and_subdirs(self, file_manager, tmp_path):
        """FM-LIST-02: 列出含文件与子目录的目录。"""
        # 创建文件和子目录。
        (tmp_path / "photo.jpg").write_bytes(b"jpg data")
        (tmp_path / "video.mp4").write_bytes(b"mp4 data")
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        result = await file_manager.list_files(str(tmp_path))
        names = {f.name for f in result}
        assert "photo.jpg" in names
        assert "video.mp4" in names
        assert "subdir" in names

        # 检查目录标记。
        dir_info = [f for f in result if f.name == "subdir"][0]
        assert dir_info.is_directory is True

    @pytest.mark.asyncio
    async def test_list_recursive(self, file_manager, tmp_path):
        """FM-LIST-03: 递归列出多层目录。"""
        (tmp_path / "root.jpg").write_bytes(b"root")
        sub1 = tmp_path / "sub1"
        sub1.mkdir()
        (sub1 / "child.jpg").write_bytes(b"child")
        sub2 = sub1 / "sub2"
        sub2.mkdir()
        (sub2 / "grandchild.jpg").write_bytes(b"grandchild")

        result = await file_manager.list_files(str(tmp_path), recursive=True)
        names = {f.name for f in result}
        assert "root.jpg" in names
        assert "child.jpg" in names
        assert "grandchild.jpg" in names
        assert "sub1" in names
        assert "sub2" in names

    @pytest.mark.asyncio
    async def test_list_nonexistent_path(self, file_manager):
        """FM-LIST-04: 路径不存在。"""
        with pytest.raises(FileNotFoundError):
            await file_manager.list_files("/nonexistent/path")

    @pytest.mark.asyncio
    async def test_list_filter_hidden_files(self, file_manager, tmp_path):
        """FM-LIST-05: 过滤隐藏文件。"""
        (tmp_path / "visible.jpg").write_bytes(b"visible")
        (tmp_path / ".hidden.jpg").write_bytes(b"hidden")

        # 不显示隐藏文件。
        result = await file_manager.list_files(str(tmp_path), include_hidden=False)
        names = {f.name for f in result}
        assert "visible.jpg" in names
        assert ".hidden.jpg" not in names

        # 显示隐藏文件。
        result_all = await file_manager.list_files(str(tmp_path), include_hidden=True)
        names_all = {f.name for f in result_all}
        assert ".hidden.jpg" in names_all


class TestGetFileInfo:
    """FM-INFO 系列：文件信息获取测试。"""

    @pytest.mark.asyncio
    async def test_get_image_file_info(self, file_manager, tmp_path):
        """FM-INFO-01: 获取图片文件信息。"""
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake jpg")

        info = await file_manager.get_file_info(str(img))
        assert info.is_directory is False
        assert info.name == "photo.jpg"
        assert info.size > 0
        assert info.telegram_type == "photo"

    @pytest.mark.asyncio
    async def test_get_gif_file_info(self, file_manager, tmp_path):
        """FM-INFO-02: 获取 GIF 文件信息。"""
        gif = tmp_path / "animation.gif"
        gif.write_bytes(b"fake gif")

        info = await file_manager.get_file_info(str(gif))
        assert info.telegram_type == "animation"

    @pytest.mark.asyncio
    async def test_get_document_file_info(self, file_manager, tmp_path):
        """FM-INFO-03: 获取文档文件信息。"""
        doc = tmp_path / "document.pdf"
        doc.write_bytes(b"fake pdf")

        info = await file_manager.get_file_info(str(doc))
        assert info.telegram_type == "document"

    @pytest.mark.asyncio
    async def test_get_directory_info(self, file_manager, tmp_path):
        """获取目录信息。"""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        info = await file_manager.get_file_info(str(subdir))
        assert info.is_directory is True
        assert info.telegram_type is None

    @pytest.mark.asyncio
    async def test_get_video_file_info(self, file_manager, tmp_path):
        """获取视频文件信息。"""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake mp4")

        info = await file_manager.get_file_info(str(video))
        assert info.telegram_type == "video"

    @pytest.mark.asyncio
    async def test_get_audio_file_info(self, file_manager, tmp_path):
        """获取音频文件信息。"""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake mp3")

        info = await file_manager.get_file_info(str(audio))
        assert info.telegram_type == "audio"


class TestSelectFiles:
    """FM-SELECT 系列：文件选择测试。"""

    @pytest.mark.asyncio
    async def test_select_mixed_files_and_directories(self, file_manager, tmp_path):
        """FM-SELECT-01: 选择混合文件与目录。"""
        (tmp_path / "file1.jpg").write_bytes(b"jpg")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file2.jpg").write_bytes(b"jpg2")

        paths = [str(tmp_path / "file1.jpg"), str(subdir)]
        result = await file_manager.select_files(paths)
        # file1.jpg 直接入选，subdir 递归展开。
        assert len(result) >= 2

    @pytest.mark.asyncio
    async def test_select_with_nonexistent_path(self, file_manager, tmp_path):
        """FM-SELECT-02: 选择含不存在路径的列表。"""
        (tmp_path / "exists.jpg").write_bytes(b"exists")
        paths = [str(tmp_path / "exists.jpg"), str(tmp_path / "nonexistent.jpg")]
        result = await file_manager.select_files(paths)
        # 只返回存在的路径。
        assert len(result) == 1
        assert result[0].name == "exists.jpg"

    @pytest.mark.asyncio
    async def test_select_filter_by_extension(self, file_manager, tmp_path):
        """FM-SELECT-03: 按扩展名过滤。"""
        (tmp_path / "photo.jpg").write_bytes(b"jpg")
        (tmp_path / "video.mp4").write_bytes(b"mp4")
        (tmp_path / "doc.pdf").write_bytes(b"pdf")

        paths = [
            str(tmp_path / "photo.jpg"),
            str(tmp_path / "video.mp4"),
            str(tmp_path / "doc.pdf"),
        ]
        result = await file_manager.select_files(
            paths, allowed_extensions=[".jpg", ".mp4"]
        )
        names = {f.name for f in result}
        assert "photo.jpg" in names
        assert "video.mp4" in names
        assert "doc.pdf" not in names


class TestGetDirectorySize:
    """测试目录大小计算。"""

    @pytest.mark.asyncio
    async def test_directory_size(self, file_manager, tmp_path):
        """计算目录总大小。"""
        (tmp_path / "file1.txt").write_bytes(b"12345")
        (tmp_path / "file2.txt").write_bytes(b"1234567")

        size = await file_manager.get_directory_size(str(tmp_path))
        assert size == 12  # 5 + 7

    @pytest.mark.asyncio
    async def test_empty_directory_size(self, file_manager, tmp_path):
        """空目录大小为 0。"""
        size = await file_manager.get_directory_size(str(tmp_path))
        assert size == 0


# ============================================================
# FileManager - 媒体组拆分测试
# ============================================================


class TestSplitMediaGroup:
    """FM-SPLIT 系列：媒体组拆分测试。"""

    def _make_file_info(self, name, telegram_type, tmp_path):
        """辅助函数：创建 FileInfo。"""
        file_path = tmp_path / name
        file_path.write_bytes(b"data")
        return FileInfo(
            path=str(file_path),
            name=name,
            is_directory=False,
            size=4,
            mime_type="image/jpeg" if telegram_type == "photo" else "video/mp4",
            extension=name.split(".")[-1],
            modified_time=0.0,
            telegram_type=telegram_type,
        )

    @pytest.mark.asyncio
    async def test_split_11_photos(self, file_manager, tmp_path):
        """FM-SPLIT-01: 11 个图片均分。"""
        files = [
            self._make_file_info(f"photo{i}.jpg", "photo", tmp_path) for i in range(11)
        ]
        groups = await file_manager.split_media_group(files)
        # 应拆分为 10 + 1 两组（album_compatible）。
        assert len(groups) == 2
        assert len(groups[0]["files"]) == 10
        assert len(groups[1]["files"]) == 1

    @pytest.mark.asyncio
    async def test_split_25_videos(self, file_manager, tmp_path):
        """FM-SPLIT-02: 25 个视频均分。"""
        files = [
            self._make_file_info(f"video{i}.mp4", "video", tmp_path) for i in range(25)
        ]
        groups = await file_manager.split_media_group(files)
        assert len(groups) == 3
        assert len(groups[0]["files"]) == 10
        assert len(groups[1]["files"]) == 10
        assert len(groups[2]["files"]) == 5

    @pytest.mark.asyncio
    async def test_split_mixed_photos_and_documents(self, file_manager, tmp_path):
        """FM-SPLIT-03: 混合图片与文档。"""
        files = [
            self._make_file_info("photo1.jpg", "photo", tmp_path),
            self._make_file_info("photo2.jpg", "photo", tmp_path),
            self._make_file_info("doc1.pdf", "document", tmp_path),
        ]
        groups = await file_manager.split_media_group(files)
        # photo 进媒体组，document 走单文件。
        assert len(groups) == 2
        album_group = [g for g in groups if g["is_album"]]
        single_group = [g for g in groups if not g["is_album"]]
        assert len(album_group) == 1
        assert len(album_group[0]["files"]) == 2
        assert len(single_group) == 1
        assert len(single_group[0]["files"]) == 1

    @pytest.mark.asyncio
    async def test_split_mixed_gif_and_photos(self, file_manager, tmp_path):
        """FM-SPLIT-04: 混合 GIF 与图片。"""
        files = [
            self._make_file_info("gif1.gif", "animation", tmp_path),
            self._make_file_info("photo1.jpg", "photo", tmp_path),
        ]
        groups = await file_manager.split_media_group(files)
        album_group = [g for g in groups if g["is_album"]]
        single_group = [g for g in groups if not g["is_album"]]
        assert len(album_group) == 1
        assert len(single_group) == 1

    @pytest.mark.asyncio
    async def test_split_max_group_size_greater_than_10(self, file_manager, tmp_path):
        """FM-SPLIT-05: max_group_size > 10 强制截断为 10。"""
        files = [
            self._make_file_info(f"photo{i}.jpg", "photo", tmp_path) for i in range(15)
        ]
        config = MediaGroupConfig(max_group_size=20)
        groups = await file_manager.split_media_group(files, config)
        # max_group_size 被截断为 10。
        assert len(groups) == 2
        assert len(groups[0]["files"]) == 10

    @pytest.mark.asyncio
    async def test_split_empty_list(self, file_manager):
        """FM-SPLIT-06: 空列表。"""
        groups = await file_manager.split_media_group([])
        assert groups == []


# ============================================================
# FileManager - 本地文件清理测试
# ============================================================


class TestDeleteLocalFile:
    """测试本地文件删除。"""

    @pytest.mark.asyncio
    async def test_delete_existing_file(self, file_manager, tmp_path):
        """删除存在的文件。"""
        f = tmp_path / "to_delete.txt"
        f.write_bytes(b"data")
        result = await file_manager.delete_local_file(str(f))
        assert result is True
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file(self, file_manager, tmp_path):
        """删除不存在的文件返回 False（safe_delete 对非路径返回 False）。"""
        result = await file_manager.delete_local_file(str(tmp_path / "nonexistent.txt"))
        assert result is False


class TestCleanupAfterUpload:
    """测试上传后清理。"""

    @pytest.mark.asyncio
    async def test_cleanup_success_results(self, file_manager, tmp_path):
        """清理成功的上传结果。"""
        f1 = tmp_path / "upload1.jpg"
        f1.write_bytes(b"data")
        f2 = tmp_path / "upload2.jpg"
        f2.write_bytes(b"data2")

        results = [
            UploadResult(success=True, file_path=str(f1)),
            UploadResult(success=True, file_path=str(f2)),
        ]
        cleaned = await file_manager.cleanup_after_upload(
            results, delete_after_upload=True
        )
        assert all(r.deleted for r in cleaned)
        assert not f1.exists()
        assert not f2.exists()

    @pytest.mark.asyncio
    async def test_cleanup_skips_failed_results(self, file_manager, tmp_path):
        """跳过失败的上传结果。"""
        f = tmp_path / "failed.jpg"
        f.write_bytes(b"data")

        results = [
            UploadResult(success=False, file_path=str(f), error_code="UPLOAD_FAILED"),
        ]
        cleaned = await file_manager.cleanup_after_upload(
            results, delete_after_upload=True
        )
        assert cleaned[0].deleted is False
        assert f.exists()  # 文件未被删除。

    @pytest.mark.asyncio
    async def test_cleanup_when_delete_disabled(self, file_manager, tmp_path):
        """清理策略关闭时不删除文件。"""
        f = tmp_path / "keep.jpg"
        f.write_bytes(b"data")

        results = [
            UploadResult(success=True, file_path=str(f)),
        ]
        cleaned = await file_manager.cleanup_after_upload(
            results, delete_after_upload=False
        )
        assert cleaned[0].deleted is False
        assert f.exists()


# ============================================================
# FileManager - _classify_files 内部方法测试
# ============================================================


class TestClassifyFiles:
    """测试 _classify_files 内部方法。"""

    def _make_file_info(self, name, telegram_type, tmp_path):
        file_path = tmp_path / name
        file_path.write_bytes(b"data")
        return FileInfo(
            path=str(file_path),
            name=name,
            is_directory=False,
            size=4,
            mime_type="image/jpeg",
            extension=name.split(".")[-1],
            modified_time=0.0,
            telegram_type=telegram_type,
        )

    @pytest.mark.asyncio
    async def test_classify_all_supported(self, file_manager, tmp_path):
        """所有文件都支持媒体组。"""
        files = [
            self._make_file_info("p1.jpg", "photo", tmp_path),
            self._make_file_info("v1.mp4", "video", tmp_path),
        ]
        album, single = await file_manager._classify_files(files)
        assert len(album) == 2
        assert len(single) == 0

    @pytest.mark.asyncio
    async def test_classify_all_unsupported(self, file_manager, tmp_path):
        """所有文件都不支持媒体组。"""
        files = [
            self._make_file_info("d1.pdf", "document", tmp_path),
            self._make_file_info("g1.gif", "animation", tmp_path),
        ]
        album, single = await file_manager._classify_files(files)
        assert len(album) == 0
        assert len(single) == 2

    @pytest.mark.asyncio
    async def test_classify_mixed(self, file_manager, tmp_path):
        """混合支持和不支持的文件。"""
        files = [
            self._make_file_info("p1.jpg", "photo", tmp_path),
            self._make_file_info("d1.pdf", "document", tmp_path),
            self._make_file_info("v1.mp4", "video", tmp_path),
            self._make_file_info("s1.webp", "sticker", tmp_path),
        ]
        album, single = await file_manager._classify_files(files)
        assert len(album) == 2  # photo + video
        assert len(single) == 2  # document + sticker


# ============================================================
# scan_expired_files 测试（FM-CLEAN 系列）
# ============================================================


class TestScanExpiredFiles:
    """FM-CLEAN 系列：过期文件扫描测试。"""

    @staticmethod
    def _set_mtime(path, age_seconds):
        """将文件最后修改时间设置为 now - age_seconds。"""
        ts = time.time() - age_seconds
        os.utime(path, (ts, ts))

    @pytest.mark.asyncio
    async def test_returns_only_expired_files(self, file_manager, tmp_path):
        """只有超过保留天数的文件会被返回。"""
        old = tmp_path / "old.jpg"
        old.write_bytes(b"old")
        (tmp_path / "new.jpg").write_bytes(b"new")
        self._set_mtime(str(old), 10 * 86400)  # 10 天前

        result = await file_manager.scan_expired_files(str(tmp_path), keep_days=7)
        assert [f.name for f in result] == ["old.jpg"]

    @pytest.mark.asyncio
    async def test_keeps_file_within_keep_days(self, file_manager, tmp_path):
        """mtime 接近但未超过保留天数的文件应保留（严格大于才过期）。"""
        f = tmp_path / "near.jpg"
        f.write_bytes(b"near")
        self._set_mtime(str(f), 7 * 86400 - 3600)  # 7 天减去 1 小时

        result = await file_manager.scan_expired_files(str(tmp_path), keep_days=7)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_hidden_files(self, file_manager, tmp_path):
        """隐藏文件不应被清理。"""
        hidden = tmp_path / ".hidden.jpg"
        hidden.write_bytes(b"h")
        self._set_mtime(str(hidden), 30 * 86400)

        result = await file_manager.scan_expired_files(str(tmp_path), keep_days=7)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_temp_files(self, file_manager, tmp_path):
        """写入中的 .temp 文件不应被清理，同名的正式文件正常清理。"""
        (tmp_path / "movie.mp4.temp").write_bytes(b"t")
        done = tmp_path / "movie.mp4"
        done.write_bytes(b"d")
        self._set_mtime(str(done), 10 * 86400)

        result = await file_manager.scan_expired_files(str(tmp_path), keep_days=7)
        assert [f.name for f in result] == ["movie.mp4"]

    @pytest.mark.asyncio
    async def test_filters_referenced_paths(self, file_manager, tmp_path):
        """被活跃任务引用的文件应被跳过。"""
        ref = tmp_path / "referenced.mp4"
        ref.write_bytes(b"r")
        self._set_mtime(str(ref), 10 * 86400)
        free = tmp_path / "free.mp4"
        free.write_bytes(b"f")
        self._set_mtime(str(free), 10 * 86400)

        result = await file_manager.scan_expired_files(
            str(tmp_path),
            keep_days=7,
            referenced_paths={str(ref)},
        )
        assert [f.name for f in result] == ["free.mp4"]

    @pytest.mark.asyncio
    async def test_recursive_subdirs(self, file_manager, tmp_path):
        """递归扫描子目录中的过期文件。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        old = sub / "old.mp4"
        old.write_bytes(b"o")
        self._set_mtime(str(old), 20 * 86400)

        result = await file_manager.scan_expired_files(str(tmp_path), keep_days=7)
        assert [f.name for f in result] == ["old.mp4"]

    @pytest.mark.asyncio
    async def test_nonexistent_root_returns_empty(self, file_manager, tmp_path):
        """根目录不存在时返回空列表。"""
        result = await file_manager.scan_expired_files(
            str(tmp_path / "missing"), keep_days=7
        )
        assert result == []


# ============================================================
# precheck_delete_paths / delete_many 测试（FM-DEL 系列）
# ============================================================


class TestBatchDelete:
    """FM-DEL 系列：批量删除与路径预检测试。"""

    @pytest.mark.asyncio
    async def test_precheck_accepts_normal_file(self, file_manager, tmp_path):
        """save_root 内的普通文件允许删除。"""
        f = tmp_path / "a.jpg"
        f.write_bytes(b"a")
        result = await file_manager.precheck_delete_paths(
            [str(f)], save_root=str(tmp_path)
        )
        assert len(result) == 1
        assert result[0].ok is True
        assert result[0].reason is None

    @pytest.mark.asyncio
    async def test_precheck_rejects_out_of_bounds(self, file_manager, tmp_path):
        """save_root 之外的文件拒绝删除。"""
        outside = tmp_path.parent / "outside.jpg"
        outside.write_bytes(b"o")
        result = await file_manager.precheck_delete_paths(
            [str(outside)], save_root=str(tmp_path)
        )
        assert result[0].ok is False
        assert result[0].reason == "OUT_OF_BOUNDS"

    @pytest.mark.asyncio
    async def test_precheck_rejects_directory(self, file_manager, tmp_path):
        """目录拒绝删除（手动删除仅支持文件）。"""
        d = tmp_path / "subdir"
        d.mkdir()
        result = await file_manager.precheck_delete_paths(
            [str(d)], save_root=str(tmp_path)
        )
        assert result[0].ok is False
        assert result[0].reason == "IS_DIRECTORY"

    @pytest.mark.asyncio
    async def test_precheck_accepts_missing_path_inside_root(self, file_manager, tmp_path):
        """save_root 内不存在的路径视为已删除（幂等）。"""
        result = await file_manager.precheck_delete_paths(
            [str(tmp_path / "ghost.jpg")], save_root=str(tmp_path)
        )
        assert result[0].ok is True

    @pytest.mark.asyncio
    async def test_delete_many_all_success(self, file_manager, tmp_path):
        """批量删除全部成功，文件从磁盘消失。"""
        paths = []
        for name in ("a.mp4", "b.jpg"):
            f = tmp_path / name
            f.write_bytes(b"x")
            paths.append(str(f))

        stats = await file_manager.delete_many(
            paths, save_root=str(tmp_path)
        )
        assert stats["total"] == 2
        assert stats["deleted"] == 2
        assert stats["failed"] == 0
        assert stats["skipped"] == 0
        assert not (tmp_path / "a.mp4").exists()
        assert not (tmp_path / "b.jpg").exists()

    @pytest.mark.asyncio
    async def test_delete_many_skips_referenced(self, file_manager, tmp_path):
        """被任务引用的文件跳过，其余正常删除。"""
        free = tmp_path / "free.mp4"
        free.write_bytes(b"f")
        ref = tmp_path / "ref.mp4"
        ref.write_bytes(b"r")

        stats = await file_manager.delete_many(
            [str(free), str(ref)],
            save_root=str(tmp_path),
            referenced_paths={str(ref)},
        )
        assert stats["total"] == 2
        assert stats["deleted"] == 1
        assert stats["skipped"] == 1
        assert stats["failed"] == 0
        assert not free.exists()
        assert ref.exists()

        skipped = [r for r in stats["results"] if r["skipped"]]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "task_referenced"

    @pytest.mark.asyncio
    async def test_delete_many_idempotent_for_missing(self, file_manager, tmp_path):
        """不存在的路径幂等删除，计入 deleted。"""
        stats = await file_manager.delete_many(
            [str(tmp_path / "ghost.mp4")], save_root=str(tmp_path)
        )
        assert stats["total"] == 1
        assert stats["deleted"] == 1
        assert stats["failed"] == 0

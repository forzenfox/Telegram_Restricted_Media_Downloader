# coding=UTF-8
"""任务相关 Pydantic 模型单元测试。

覆盖 TaskType、RangeMode、TaskParams、TaskCreate 的校验逻辑，
确保新增 listen_* 任务类型与 recent 范围模式按预期工作。
"""

import pytest
from pydantic import ValidationError

from module.api.models.task import TaskCreate, TaskParams, TaskOut


# ==================== 任务类型校验 ====================


class TestTaskType:
    """TaskType Literal 校验测试。"""

    @pytest.mark.parametrize(
        "task_type",
        ["download", "forward", "upload", "listen_download", "listen_forward"],
    )
    def test_valid_task_types(self, task_type):
        """所有有效任务类型均应通过校验。"""
        task = TaskCreate(task_type=task_type, params={})
        assert task.task_type == task_type

    def test_invalid_task_type(self):
        """无效任务类型应抛出 ValidationError。"""
        with pytest.raises(ValidationError) as exc_info:
            TaskCreate(task_type="invalid_type", params={})
        assert "download" in str(exc_info.value)
        assert "listen_download" in str(exc_info.value)


# ==================== 范围模式校验 ====================


class TestRangeMode:
    """RangeMode Literal 校验测试。"""

    @pytest.mark.parametrize(
        "range_mode",
        ["id_range", "date_range", "multiple_ids", "all", "recent"],
    )
    def test_valid_range_modes(self, range_mode):
        """所有有效范围模式均应通过校验。"""
        params = {"range_mode": range_mode}
        if range_mode == "recent":
            params["recent_count"] = 10
        task = TaskCreate(task_type="download", params=params)
        assert task.params["range_mode"] == range_mode


# ==================== TaskParams 校验 ====================


class TestTaskParams:
    """TaskParams 模型校验测试。"""

    def test_source_and_target_identifier(self):
        """source_identifier 与 target_identifier 应被接受。"""
        params = TaskParams(
            source_identifier="@source",
            target_identifier="@target",
        )
        assert params.source_identifier == "@source"
        assert params.target_identifier == "@target"

    def test_recent_mode_requires_count(self):
        """range_mode="recent" 时 recent_count 必须大于 0。"""
        with pytest.raises(ValidationError) as exc_info:
            TaskParams(range_mode="recent", recent_count=0)
        assert "recent_count" in str(exc_info.value)

    def test_recent_mode_negative_count(self):
        """range_mode="recent" 时 recent_count 不能为负数。"""
        with pytest.raises(ValidationError) as exc_info:
            TaskParams(range_mode="recent", recent_count=-1)
        assert "recent_count" in str(exc_info.value)

    def test_non_recent_mode_with_count(self):
        """range_mode != "recent" 时 recent_count 应为 None。"""
        with pytest.raises(ValidationError) as exc_info:
            TaskParams(range_mode="id_range", recent_count=10)
        assert "recent_count" in str(exc_info.value)

    def test_valid_recent_params(self):
        """正确的 recent 参数应通过校验。"""
        params = TaskParams(range_mode="recent", recent_count=10)
        assert params.range_mode == "recent"
        assert params.recent_count == 10

    def test_media_types_and_size_filters(self):
        """media_types、min_size、max_size 应被接受。"""
        params = TaskParams(
            media_types=["video", "photo"],
            min_size=1024,
            max_size=1024 * 1024 * 100,
        )
        assert params.media_types == ["video", "photo"]
        assert params.min_size == 1024
        assert params.max_size == 1024 * 1024 * 100

    def test_enable_repository_backup(self):
        """enable_repository_backup 应被接受。"""
        params = TaskParams(enable_repository_backup=True)
        assert params.enable_repository_backup is True


# ==================== TaskCreate 兼容性 ====================


class TestTaskCreate:
    """TaskCreate 创建模型测试。"""

    def test_params_as_dict(self):
        """params 仍支持裸 dict（向后兼容）。"""
        task = TaskCreate(
            task_type="download",
            params={"chat_id": "-1001234567890", "range_mode": "id_range"},
        )
        assert isinstance(task.params, dict)
        assert task.params["chat_id"] == "-1001234567890"

    def test_params_as_task_params(self):
        """params 支持 TaskParams 对象。"""
        task = TaskCreate(
            task_type="listen_download",
            params=TaskParams(
                source_identifier="@channel",
                range_mode="recent",
                recent_count=5,
            ),
        )
        assert task.task_type == "listen_download"
        assert task.params["source_identifier"] == "@channel"
        assert task.params["range_mode"] == "recent"

    def test_listen_download_with_recent(self):
        """listen_download 任务可携带 recent 参数。"""
        task = TaskCreate(
            task_type="listen_download",
            params={
                "source_identifier": "@channel",
                "range_mode": "recent",
                "recent_count": 20,
            },
        )
        assert task.params["source_identifier"] == "@channel"
        assert task.params["recent_count"] == 20


# ==================== TaskOut 序列化 ====================


class TestTaskOut:
    """TaskOut 响应模型测试。"""

    def test_task_out_accepts_new_types(self):
        """TaskOut 应能序列化新增任务类型。"""
        out = TaskOut(
            id="task_123",
            task_type="listen_forward",
            status="pending",
            params={"source_identifier": "@a", "target_identifier": "@b"},
        )
        data = out.model_dump()
        assert data["task_type"] == "listen_forward"
        assert data["params"]["source_identifier"] == "@a"

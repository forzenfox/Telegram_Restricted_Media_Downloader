# 任务完成/错误通知发送逻辑 — 设计文档

- 日期：2026-09-08
- 状态：已实现（用户已批准设计后执行）
- 对应问题：通知开关（`notification_enabled` / `error_notification_enabled`）已打通持久化与 WebUI 回显，但实际发送逻辑未实现

## 1. 目标

新任务管理器体系（WebUI 与新版 bot 任务）的任务进入终态时，通过 Telegram Bot 向唯一授权用户发送通知：

- 任务完成（`COMPLETED`）→「完成通知」
- 任务失败（终态 `FAILED`）→「错误通知」

## 2. 触发语义

| 场景 | 是否通知 | 类型 |
|------|---------|------|
| completed | ✅ 完成通知 | `preference.notice` 且 `preference.notification_enabled` |
| failed（终态） | ✅ 错误通知 | `preference.notice` 且 `preference.error_notification_enabled` |
| cancelled（用户取消） | ❌ | - |
| 重试中的失败（非终态） | ❌ | - |
| 常驻任务（`RESIDENT_RUNNING_TASK_TYPES`：监听/定时清理） | ❌ | - |

## 3. 组件设计

### 3.1 `TaskNotifier`（新增 `module/core/task/notifier.py`）

```python
class TaskNotifier:
    def __init__(self, client, root_ids: list[int], config_manager):
        # client: 发送通知用的 pyrogram Client（实际注入 Bot.bot）
        # root_ids: 授权用户 id 列表（Bot.root，单用户通常 1 个）
        # config_manager: 读取 preference.notice / *_enabled 开关

    async def notify_completed(self, task) -> None: ...
    async def notify_failed(self, task) -> None: ...
```

- 开关判定：`preference.notice`（总开关）为真 **且** 对应子开关为真 **且** `task.task_type not in RESIDENT_RUNNING_TASK_TYPES`
- 消息格式（Telegram HTML）：
  - 完成：`✅ 任务完成` + 任务类型 / 任务 ID / 目标 chat / 耗时
  - 失败：`❌ 任务失败` + 任务类型 / 任务 ID / 目标 chat / 错误原因（`task.error_message`）
- 发送目标：`client.send_message(chat_id=root_ids[0], text=...)`（单用户）
- 错误处理：发送异常仅 `log.error` 记录，不向上抛（通知尽力而为，不影响任务状态流转）

### 3.2 TaskManager 挂载（`module/core/task/manager.py`）

- 新增 `set_notifier(notifier)`（与 `set_executor` 同模式）
- `complete_task()`：状态落库后 `asyncio.create_task(notifier.notify_completed(task))`
- `fail_task()`：状态落库后 `asyncio.create_task(notifier.notify_failed(task))`
- `cancel_task()` / `retry_task()`：不触发
- 通知 fire-and-forget，不阻塞状态机；未注入 notifier 时行为不变

### 3.3 集成（`module/core/download/downloader.py`）

- bot 启动成功后（`__download_media_from_links` 中 `init_task_executor` 同位置）：
  - 若 `is_bot_running`：构造 `TaskNotifier(client=self.bot.bot, root_ids=self.bot.root, config_manager=ctx.config_manager)` 并 `task_manager.set_notifier(notifier)`
- 纯 user 模式（无 bot_token）不注入 → 无通知
- 注入失败仅 `log.warning`，不致命

## 4. 测试策略（TDD）

- `tests/unit/core/task/test_notifier.py`：
  - 开关矩阵：任意开关关闭 → 不发送；全开 → 发送
  - 常驻任务（CLEANUP_FILES / LISTEN_*）终态不通知
  - 消息内容包含任务类型 / 任务 ID / 目标 / 原因
  - 发送异常被吞：`send_message` 抛错不向上冒
- `tests/unit/core/task/test_manager.py`：
  - `complete_task` 触发 `notify_completed`
  - `fail_task` 触发 `notify_failed`
  - `cancel_task` 不触发
  - 未注入 notifier 时正常完成/失败不报错

## 5. 边界

- 只覆盖新任务管理器体系；旧 downloader 内置管道的 `done_notice` 保持不动
- 通知依赖 bot 客户端（`root` 用户），纯 user 模式无通知
- 通知读取 `task.max_tasks` 无关，仅依赖 `preference` 区块
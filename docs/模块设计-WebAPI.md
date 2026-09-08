# Web API 模块设计文档

> **项目名称**: Telegram_Restricted_Media_Downloader  
> **文档版本**: v1.1  
> **创建日期**: 2026-06-18  
> **更新日期**: 2026-07-03  
> **作者**: AI Assistant  
> **状态**: 已发布  
> **对应主文档**: [交互增强设计.md](./交互增强设计.md)

---

## 变更记录

| 版本 | 日期 | 变更内容 | 状态 |
|------|------|----------|------|
| v1.1 | 2026-07-03 | 新增 `GET /api/chats/resolve` 标识符解析端点；扩展 `POST /api/tasks` 支持 `source_identifier`、`target_identifier`、`recent` 模式、媒体/大小过滤、仓库备份；新增 `listen_download`/`listen_forward` 任务类型；新增 `LISTEN_ALREADY_EXISTS`、`INVALID_RECENT_COUNT`、`RATE_LIMITED` 错误响应；移除 WebSocket 相关设计，统一使用 REST 轮询 | 已发布 |
| v1.0 | 2026-06-18 | 初始 Web API 模块设计 | 已归档 |

---

## 1. 设计目标与职责边界

### 1.1 设计目标

本模块为 Telegram_Restricted_Media_Downloader 项目提供 **WebUI 后端服务**，基于 FastAPI 构建，目标如下：

| 目标 | 说明 |
|------|------|
| **统一入口** | 为 WebUI 提供唯一的 HTTP 服务端点 |
| **安全访问** | 所有端点强制 Token 认证，防止未授权访问 |
| **实时通信** | 通过 REST 轮询获取任务状态、监控指标和日志流（WebSocket 已移除） |
| **共享核心** | 复用 `TaskManager`、`FileManager`、`ConfigManager` 等核心业务层 |
| **轻量无构建** | 不依赖前端构建工具，后端纯 Python 运行 |
| **向后兼容** | 不修改、不破坏现有 Bot 命令和核心下载逻辑 |
| **高性能** | API 响应时间目标 < 200ms（不含文件 I/O 与 Telegram API 调用） |

### 1.2 职责边界

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户层                                      │
│   ┌──────────────┐                          ┌──────────────────────┐   │
│   │ Telegram Bot │                          │   WebUI (浏览器)      │   │
│   │  轻量入口     │                          │   完整管理界面        │   │
│   └──────┬───────┘                          └──────────┬───────────┘   │
└──────────┼──────────────────────────────────────────────┼───────────────┘
           │                                              │
           │ filters.user(self.root)                     │ Token 认证
           │                                              │
┌──────────▼──────────────────────────────────────────────▼───────────────┐
│                         核心业务层（共享）                                │
│   TaskManager │ FileManager │ ConfigManager │ InteractionMgr │ Monitor  │
└─────────────────────────────────────────────────────────────────────────┘
           ▲                              ▲
           │                              │
┌──────────┴──────────────────────────────┴───────────────────────────────┐
│                         Web API 模块（本文档）                            │
│   FastAPI 应用 + RESTful API + Token 中间件 + 统一响应                     │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Bot 端**：保留命令解析与轻量操作，复杂操作通过 `/web` 命令引导至 WebUI。
- **Web API 模块**：只负责 HTTP 接入、认证、参数校验、序列化，不直接实现下载/转发/上传业务逻辑。
- **核心业务层**：任务调度、文件操作、配置持久化、监控采集由共享模块负责。
- **数据持久层**：任务状态使用 SQLite，配置文件使用 YAML，文件存储使用本地文件系统。

### 1.3 非功能性约束

| 约束 | 说明 |
|------|------|
| **单用户** | 仅支持一个授权用户，无需用户 ID 隔离 |
| **后端无构建** | 不使用 npm/webpack/vite 等构建流程 |
| **Token 必校验** | 所有 REST API 必须校验 Token |
| **响应时间** | 普通 API 响应 < 200ms（文件上传/大统计除外） |
| **向后兼容** | 不删除、不修改现有 Bot 命令签名 |

---

## 2. 应用结构

### 2.1 目录结构

按主文档规划，`module/api/` 为 Web API 模块根目录：

```
module/
├── api/
│   ├── __init__.py              # 模块导出，create_app() 工厂函数
│   ├── app.py                   # FastAPI 应用工厂、生命周期事件
│   ├── dependencies.py          # 依赖注入：Token 校验、核心管理器获取
│   ├── middleware.py            # 自定义中间件：认证、日志、响应时间、CORS
│   ├── exceptions.py            # 统一异常类与异常处理器
│   ├── responses.py             # 统一响应封装
│   ├── routes/                  # RESTful 路由
│   │   ├── __init__.py
│   │   ├── auth.py              # 认证相关（登录态检查、Token 刷新）
│   │   ├── tasks.py             # 任务管理
│   │   ├── chats.py             # 频道/私聊、消息统计、标识符解析
│   │   ├── files.py             # 文件浏览与上传
│   │   ├── config.py            # 配置管理
│   │   ├── monitor.py           # 监控与资源状态
│   │   └── repository.py        # 仓库模式（文件/来源/分发/同步）
│   └── models/                  # Pydantic 数据模型
│       ├── __init__.py
│       ├── common.py            # 通用模型（APIResponse、分页等）
│       ├── auth.py              # 认证模型
│       ├── task.py              # 任务模型
│       ├── chat.py              # 频道/消息模型
│       ├── file.py              # 文件模型
│       ├── config.py            # 配置模型
│       ├── monitor.py           # 监控模型
│       └── repository.py        # 仓库模式模型
```

### 2.2 FastAPI 应用工厂

`module/api/app.py` 提供 `create_app()` 工厂函数，便于测试注入 Mock 依赖。

```python
from fastapi import FastAPI
from module.api.routes import api_router
from module.api.middleware import setup_middleware
from module.api.exceptions import setup_exception_handlers


def create_app(
    token_manager: TokenManager | None = None,
    task_manager: TaskManager | None = None,
    file_manager: FileManager | None = None,
    config_manager: ConfigManager | None = None,
    monitor: Monitor | None = None,
    repository_manager: RepositoryManager | None = None,
) -> FastAPI:
    app = FastAPI(
        title="TRMD Web API",
        version="1.1.0",
        docs_url=None,          # 禁用 Swagger，避免暴露接口
        redoc_url=None,         # 禁用 ReDoc
    )

    # 挂载核心管理器到应用状态，供依赖注入使用
    app.state.token_manager = token_manager or TokenManager()
    app.state.task_manager = task_manager or TaskManager()
    app.state.file_manager = file_manager or FileManager()
    app.state.config_manager = config_manager or ConfigManager()
    app.state.monitor = monitor or Monitor()
    app.state.repository_manager = repository_manager  # 可选，仓库模式未启用时为 None

    setup_middleware(app)
    setup_exception_handlers(app)
    app.include_router(api_router, prefix="/api")

    return app
```

### 2.3 路由组织

| 路由器 | 前缀 | 职责 |
|--------|------|------|
| `auth_router` | `/api/auth` | Token 校验、当前用户状态 |
| `tasks_router` | `/api/tasks` | 任务 CRUD、开始/取消/重试 |
| `chats_router` | `/api/chats` | 频道/私聊列表、消息统计、精确分析、标识符解析 |
| `files_router` | `/api/files` | 文件列表、上传 |
| `config_router` | `/api/config` | 配置读取与更新 |
| `monitor_router` | `/api/monitor` | 监控统计、资源状态 |
| `repository_router` | `/api/repository` | 仓库模式：文件管理、来源映射、分发、同步 (✅ 已实现) |

### 2.4 中间件栈

按执行顺序从上到下：

| 顺序 | 中间件 | 职责 |
|------|--------|------|
| 1 | **CORSMiddleware** | 单用户场景下允许所有来源，认证由 Token 机制保障 |
| 2 | **ProcessTimeMiddleware** | 记录响应时间，超过阈值告警 |
| 3 | **RequestLogMiddleware** | 记录请求方法、路径、状态码（不记录敏感 Token） |
| 4 | **SecurityHeadersMiddleware** | 添加安全响应头（X-Content-Type-Options 等） |

> 说明：原 TrustedHostMiddleware（Host 头白名单）已移除。单用户 + Token 认证场景下收益有限，且会拦截自定义域名访问。

> 认证不通过全局中间件实现，而是使用 FastAPI `Depends` 依赖注入，便于按路由精确控制。

---

## 3. 认证集成方式

### 3.1 Token 生命周期

Token 由 `TokenManager` 统一管理：

```python
class TokenManager:
    def create_token(self, ttl_seconds: int = 3600) -> str:
        """生成随机 Token，记录过期时间，可持久化到 SQLite。"""

    def validate_token(self, token: str) -> bool:
        """校验 Token 是否存在且未过期。"""

    def revoke_token(self, token: str) -> bool:
        """撤销指定 Token。"""

    def revoke_all(self) -> None:
        """撤销所有已发行 Token（Bot /web_revoke 调用）。"""
```

- **生成方**：Bot `/web` 命令。
- **存储**：内存 + SQLite，进程重启后可恢复未过期 Token。
- **有效期**：1 小时（3600 秒）。
- **撤销**：支持单条撤销和全部撤销。

### 3.2 认证传递方式

| 场景 | 传递方式 | 说明 |
|------|----------|------|
| 首次页面访问 | URL 参数 `?token=xxx` | 仅用于加载前端页面 |
| AJAX/Fetch | `Authorization: Bearer xxx` | 后续 API 请求首选 |
| Cookie（可选） | HttpOnly Cookie | 首次验证后下发，作为 fallback |

### 3.3 FastAPI 依赖注入

```python
# module/api/dependencies.py
from fastapi import Header, Query, HTTPException, status

async def require_token(
    authorization: str | None = Header(None, alias="Authorization"),
    token_query: str | None = Query(None, alias="token"),
) -> str:
    raw = authorization or token_query
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MISSING_TOKEN")
    token = raw.removeprefix("Bearer ").strip() if raw.startswith("Bearer ") else raw.strip()
    if not token_manager.validate_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_OR_EXPIRED_TOKEN")
    return token
```

所有受保护路由统一使用：

```python
@router.get("/tasks", dependencies=[Depends(require_token)])
async def list_tasks(...):
    ...
```

---

## 4. RESTful API 详细设计

### 4.1 端点总览

#### 4.1.1 认证

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/auth/me` | GET | 获取当前 Token 状态与过期时间 | Token |
| `/api/auth/refresh` | POST | 在 Token 过期前刷新（可选） | Token |

#### 4.1.2 任务管理

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/tasks` | GET | 获取任务列表 | Token |
| `/api/tasks` | POST | 创建任务 | Token |
| `/api/tasks/{task_id}` | GET | 获取任务详情 | Token |
| `/api/tasks/{task_id}/start` | POST | 开始/排队任务 | Token |
| `/api/tasks/{task_id}/cancel` | POST | 取消任务 | Token |
| `/api/tasks/{task_id}/retry` | POST | 重试任务 | Token |
| `/api/tasks/{task_id}` | DELETE | 删除已完成/失败任务记录 | Token |

#### 4.1.3 频道与消息

| 端点 | 方法 | 功能 | 认证 | 缓存 |
|------|------|------|------|------|
| `/api/chats` | GET | 获取已加入频道/私聊列表 | Token | 1 小时 |
| `/api/chats/resolve` | GET | 解析 username/chat_id/t.me 链接为 chat_id 与元信息 | Token | 无 |
| `/api/chats/{chat_id}/messages/estimate` | POST | 抽样估算消息范围统计 | Token | 10 分钟 |
| `/api/chats/{chat_id}/messages/analyze` | POST | 精确分析消息范围（遍历） | Token | 按参数 |

#### 4.1.4 文件管理

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/files` | GET | 获取文件列表 | Token |
| `/api/files/upload` | POST | 上传文件到本地或目标频道 | Token |

#### 4.1.5 配置管理

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/config` | GET | 获取配置 | Token |
| `/api/config` | PUT | 更新配置 | Token |

#### 4.1.6 监控与资源

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/monitor/stats` | GET | 获取监控统计 | Token |
| `/api/resource/status` | GET | 获取资源状态（磁盘/内存/并发） | Token |

#### 4.1.7 仓库模式

| 端点 | 方法 | 功能 | 认证 |
|------|------|------|------|
| `/api/repository/status` | GET | 获取仓库模式状态（是否启用、chat_id、文件数、最后同步时间） | Token |
| `/api/repository/files` | GET | 列出仓库文件（分页、按 file_type/status 过滤） | Token |
| `/api/repository/files/{file_unique_id}` | GET | 获取仓库文件详情 | Token |
| `/api/repository/sources` | GET | 列出来源映射记录 | Token |
| `/api/repository/distributions` | GET | 列出分发记录 | Token |
| `/api/repository/distribute` | POST | 触发分发（指定文件、目标频道、可选 caption） | Token |
| `/api/repository/sync` | POST | 触发手动同步 | Token |

### 4.2 重点端点说明

#### 4.2.1 `GET /api/tasks`

**功能**：查询任务列表。

**请求参数**：

| 参数 | 类型 | 位置 | 必填 | 说明 |
|------|------|------|------|------|
| `status` | `string` | Query | 否 | 过滤状态：`pending`/`running`/`completed`/`failed`/`cancelled`/`queued` |
| `task_type` | `string` | Query | 否 | 过滤类型：`download`/`forward`/`upload`/`listen_download`/`listen_forward` |
| `limit` | `int` | Query | 否 | 分页大小，默认 20，最大 100 |
| `offset` | `int` | Query | 否 | 偏移量，默认 0 |

**响应体**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "task_001",
        "task_type": "download",
        "status": "running",
        "progress": 45.5,
        "created_at": "2026-06-18T10:00:00+08:00",
        "updated_at": "2026-06-18T10:05:00+08:00"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  }
}
```

#### 4.2.2 `POST /api/tasks`

**功能**：创建任务。

**请求体示例**（私聊下载任务，recent 模式）：

```json
{
  "task_type": "download",
  "params": {
    "source_identifier": "@seseYunBot",
    "range_mode": "recent",
    "recent_count": 10,
    "media_types": ["video"],
    "min_size": null,
    "max_size": null,
    "enable_repository_backup": false
  }
}
```

**请求体示例**（私聊转发任务，ID 范围）：

```json
{
  "task_type": "forward",
  "params": {
    "source_identifier": "@seseYunBot",
    "target_identifier": "@my_channel",
    "range_mode": "id_range",
    "min_id": 14425,
    "max_id": 14426,
    "media_types": ["video"]
  }
}
```

**请求体示例**（监听下载任务）：

```json
{
  "task_type": "listen_download",
  "params": {
    "source_identifier": "@seseYunBot",
    "media_types": ["video", "photo"],
    "enable_repository_backup": true
  }
}
```

**请求体示例**（监听转发任务）：

```json
{
  "task_type": "listen_forward",
  "params": {
    "source_identifier": "@seseYunBot",
    "target_identifier": "@my_channel",
    "media_types": ["video"]
  }
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_type` | `TaskType` | 是 | `download` / `forward` / `upload` / `listen_download` / `listen_forward` |
| `params` | `object` | 是 | 任务参数，根据类型不同结构不同 |
| `params.source_identifier` | `string` | 条件必填 | 源对话标识符：username / chat_id / t.me 链接；与 `chat_id` 二选一 |
| `params.chat_id` | `string` | 条件必填 | 原 t.me 链接格式，保留向后兼容 |
| `params.target_identifier` | `string` | 转发/监听转发必填 | 目标对话标识符 |
| `params.forward_target` | `string` | 旧版转发必填 | 目标频道（向后兼容） |
| `params.range_mode` | `string` | 下载/转发必填 | `date_range` / `id_range` / `multiple_ids` / `all` / `recent` |
| `params.recent_count` | `int` | recent 模式必填 | 最近 N 条消息，N > 0；API 层校验，> 1000 时 TaskManager 截断至 1000 |
| `params.min_id` / `params.max_id` | `int` | 条件必填 | ID 范围模式 |
| `params.start_date` / `params.end_date` | `string` | 条件必填 | 日期范围模式，ISO 8601 |
| `params.message_list` | `string[]` | 条件必填 | 多个 ID 或链接模式 |
| `params.media_types` | `string[]` | 否 | 媒体类型过滤：`video` / `photo` / `document` / `audio` |
| `params.download_type` | `string[]` | 否 | 旧版类型过滤字段，向后兼容 |
| `params.min_size` / `params.max_size` | `int` | 否 | 文件大小过滤，单位字节，仅对非监听任务生效 |
| `params.save_directory` | `string` | 否 | 下载保存目录 |
| `params.delete_after_upload` | `bool` | 否 | 转发后删除本地文件，默认 `true` |
| `params.file_paths` | `string[]` | 上传必填 | 本地文件路径 |
| `params.enable_repository_backup` | `bool` | 否 | 是否备份到仓库频道；`null` 表示继承全局配置，仅对 `download` / `listen_download` 生效 |
| `distribution_method` | `string` | 否 | 分发方式：`copy_message` / `file_id_send` / `upload`，仓库模式启用时有效 |
| `enable_dedup` | `bool` | 否 | 是否启用去重，默认 `true`，仓库模式启用时有效 |

**响应体**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "task_002",
    "task_type": "download",
    "status": "queued",
    "created_at": "2026-06-18T10:10:00+08:00"
  }
}
```

**错误码**：

| HTTP 状态 | 业务码 | 说明 |
|-----------|--------|------|
| 400 | `TASK_SIZE_WARNING` | 任务超过 5GB，需用户二次确认 |
| 400 | `TASK_SIZE_EXCEEDED` | 任务超过 10GB，禁止创建 |
| 400 | `INVALID_RECENT_COUNT` | `recent_count` 小于等于 0 或格式错误 |
| 400 | `INSUFFICIENT_DISK_SPACE` | 磁盘空间不足 |
| 409 | `TASK_CONFLICT` | 同类任务正在执行，已加入队列 |
| 409 | `LISTEN_ALREADY_EXISTS` | 同一 chat_id 已存在运行中的 `listen_download` / `listen_forward` 任务 |

#### 4.2.3 `GET /api/chats/resolve`

**功能**：解析标识符（username / chat_id / t.me 链接）为标准化对话信息。

**请求参数**：

| 参数 | 类型 | 位置 | 必填 | 说明 |
|------|------|------|------|------|
| `identifier` | `string` | Query | 是 | 要解析的标识符，例如 `@seseYunBot`、`8288406549`、`https://t.me/seseYunBot` |

**响应体**（200 OK）：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "chat_id": 8288406549,
    "chat_type": "bot",
    "chat_name": "sosov2☁️精选资源",
    "username": "seseYunBot",
    "message_count": 9,
    "media_count": 2,
    "has_access": true,
    "is_private": true
  }
}
```

**错误响应示例**：

```json
// 400 - 格式无效
{
  "code": 400,
  "message": "标识符格式不正确",
  "data": null
}

// 403 - 无权限
{
  "code": 403,
  "message": "您尚未与此用户建立对话",
  "data": null
}

// 404 - 用户/频道不存在
{
  "code": 404,
  "message": "无法找到该用户/频道",
  "data": null
}

// 429 - API 限流
{
  "code": 429,
  "message": "请求过于频繁，请稍后再试",
  "data": {"retry_after": 30}
}

// 504 - 解析超时
{
  "code": 504,
  "message": "解析请求超时，请重试",
  "data": null
}
```

> **缓存与限流策略**：`GET /api/chats/resolve` 不实现服务端缓存，避免 chat 信息不一致；前端“解析”按钮需做至少 3 秒的防抖处理，降低触发 Telegram FloodWait 的概率。

#### 4.2.4 `POST /api/chats/{chat_id}/messages/estimate`

**功能**：抽样估算消息范围大小与数量。

**请求体**：

```json
{
  "range_mode": "id_range",
  "min_id": 100,
  "max_id": 500,
  "media_types": ["video", "photo"]
}
```

**响应体**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "message_count": 850,
    "total_size_bytes": 7730941132,
    "total_size_human": "7.2 GB",
    "estimated_duration_seconds": 2700,
    "sampled": true
  }
}
```

> 全部消息模式下采用头尾各 10 条抽样估算。

#### 4.2.5 `GET /api/files`

**功能**：列出本地文件。

**请求参数**：

| 参数 | 类型 | 位置 | 必填 | 说明 |
|------|------|------|------|------|
| `path` | `string` | Query | 否 | 目录路径，默认下载根目录 |
| `recursive` | `bool` | Query | 否 | 是否递归，默认 `false` |

**响应体**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "path": "/downloads",
    "items": [
      {
        "name": "video_001.mp4",
        "type": "file",
        "size": 104857600,
        "modified_at": "2026-06-18T09:00:00+08:00"
      }
    ]
  }
}
```

#### 4.2.6 `PUT /api/config`

**功能**：更新配置。

**请求体**：

```json
{
  "resource_limits": {
    "max_concurrent_tasks": 1,
    "max_download_concurrency": 3,
    "max_upload_concurrency": 1,
    "task_size_warning_gb": 5,
    "task_size_max_gb": 10
  }
}
```

**校验规则**：

- 数值类配置必须 ≥ 1。
- `task_size_max_gb` 必须 > `task_size_warning_gb`。
- 关键配置（`api_id`、`api_hash`、`bot_token`）修改后需重启 Bot 生效。

### 4.3 错误码设计

| HTTP 状态 | 业务码 | 说明 |
|-----------|--------|------|
| 200 | `0` / `success` | 成功 |
| 400 | `BAD_REQUEST` | 请求参数错误 |
| 400 | `INVALID_RECENT_COUNT` | `recent_count` 不合法 |
| 401 | `MISSING_TOKEN` | 缺少 Token |
| 401 | `INVALID_OR_EXPIRED_TOKEN` | Token 无效或过期 |
| 403 | `FORBIDDEN` | 权限不足（保留，当前单用户场景较少触发） |
| 403 | `ACCESS_DENIED` | 尚未与目标对话建立访问关系 |
| 404 | `TASK_NOT_FOUND` | 任务不存在 |
| 404 | `CHAT_NOT_FOUND` | 频道不存在 |
| 404 | `USER_NOT_FOUND` | 无法找到该用户/频道 |
| 409 | `TASK_CONFLICT` | 任务状态冲突 |
| 409 | `LISTEN_ALREADY_EXISTS` | 同一对话已存在运行中的监听任务 |
| 422 | `VALIDATION_ERROR` | Pydantic 校验失败 |
| 429 | `RATE_LIMITED` | 请求过于频繁；`resolve` 端点响应体含 `retry_after` |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 504 | `RESOLVE_TIMEOUT` | 标识符解析超时 |

---

## 5. 实时状态获取

WebSocket 端点已从本模块中移除。前端通过 REST 轮询获取实时状态：

| 数据类型 | 轮询方式 | 说明 |
|----------|----------|------|
| 任务状态 | 周期性 `GET /api/tasks` / `GET /api/tasks/{task_id}` | 默认 3~5 秒刷新一次 |
| 监控指标 | 周期性 `GET /api/monitor/stats` | 默认 5 秒刷新一次 |
| 日志流 | 按需轮询监控/日志端点 | 不再通过长连接推送 |

> 移除 WebSocket 后，前端无需处理 WebSocket 认证、断线重连和心跳逻辑，降低了部署复杂度；服务端也无需维护长连接管理器。

---

## 6. 数据模型

### 6.1 通用模型

```python
# module/api/models/common.py
from pydantic import BaseModel
from typing import Generic, TypeVar, Optional

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None


class PaginationParams(BaseModel):
    limit: int = 20
    offset: int = 0


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
```

### 6.2 认证模型

```python
# module/api/models/auth.py
from pydantic import BaseModel
from datetime import datetime


class TokenInfo(BaseModel):
    valid: bool
    expires_at: datetime
```

### 6.3 任务模型

```python
# module/api/models/task.py
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime

TaskType = Literal["download", "forward", "upload", "listen_download", "listen_forward"]
TaskStatus = Literal["pending", "queued", "running", "completed", "failed", "cancelled"]
RangeMode = Literal["date_range", "id_range", "multiple_ids", "all", "recent"]


class TaskBase(BaseModel):
    task_type: TaskType


class DownloadTaskParams(BaseModel):
    range_mode: RangeMode
    chat_id: Optional[str] = None
    source_identifier: Optional[str] = None
    min_id: Optional[int] = None
    max_id: Optional[int] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    message_list: Optional[list[str]] = None
    recent_count: Optional[int] = None
    media_types: list[str] = Field(default_factory=lambda: ["video", "photo"])
    download_type: Optional[list[str]] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    save_directory: Optional[str] = None
    enable_repository_backup: Optional[bool] = None


class ForwardTaskParams(DownloadTaskParams):
    target_identifier: Optional[str] = None
    forward_target: Optional[str] = None
    delete_after_upload: bool = True


class ListenDownloadTaskParams(BaseModel):  # (⏳ 待实现)
    source_identifier: str
    media_types: list[str] = Field(default_factory=lambda: ["video", "photo"])
    enable_repository_backup: Optional[bool] = None


class ListenForwardTaskParams(BaseModel):  # (⏳ 待实现)
    source_identifier: str
    target_identifier: str
    media_types: list[str] = Field(default_factory=lambda: ["video", "photo"])


class UploadTaskParams(BaseModel):
    file_paths: list[str]
    target_chat: str
    send_as_media_group: bool = False


class TaskCreate(BaseModel):
    task_type: TaskType
    params: dict
    distribution_method: Optional[Literal["copy_message", "file_id_send", "upload"]] = None
    enable_dedup: bool = True


class TaskOut(BaseModel):
    id: str
    task_type: TaskType
    status: TaskStatus
    progress: float = 0.0
    created_at: datetime
    updated_at: datetime
    message: Optional[str] = None
```

### 6.4 频道与消息模型

```python
# module/api/models/chat.py
from pydantic import BaseModel
from typing import Optional


class ChatOut(BaseModel):
    id: str
    title: str
    type: str
    username: Optional[str] = None


class ResolvedChatOut(BaseModel):
    chat_id: int
    chat_type: str
    chat_name: str
    username: Optional[str] = None
    message_count: int
    media_count: int
    has_access: bool
    is_private: bool


class MessageRangeRequest(BaseModel):
    range_mode: str
    min_id: Optional[int] = None
    max_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    message_list: Optional[list[str]] = None
    media_types: Optional[list[str]] = None


class MessageEstimateOut(BaseModel):
    message_count: int
    total_size_bytes: int
    total_size_human: str
    estimated_duration_seconds: int
    sampled: bool
```

### 6.5 文件模型

```python
# module/api/models/file.py
from pydantic import BaseModel
from datetime import datetime
from typing import Literal


class FileInfo(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: Optional[int] = None
    modified_at: Optional[datetime] = None


class FileListOut(BaseModel):
    path: str
    items: list[FileInfo]
```

### 6.6 配置模型

```python
# module/api/models/config.py
from pydantic import BaseModel, Field
from typing import Optional


class ResourceLimits(BaseModel):
    max_concurrent_tasks: int = 1
    max_download_concurrency: int = 3
    max_upload_concurrency: int = 1
    max_forward_concurrency: int = 1
    min_disk_space_gb: int = 2
    memory_limit_mb: int = 512
    task_size_warning_gb: int = 5
    task_size_max_gb: int = 10


class ProxyConfig(BaseModel):
    enable_proxy: bool = False
    scheme: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class RepositoryConfig(BaseModel):
    enabled: bool = False
    chat_id: Optional[int] = None          # 仓库频道数字 ID（兼容旧配置）
    auto_backup_downloads: bool = True      # 全局自动备份开关：所有 DOWNLOAD / LISTEN_DOWNLOAD 任务默认是否备份到仓库
    repository_channel: Optional[str] = None  # 仓库频道标识符（username 或数字 ID 均可，当 auto_backup_downloads=true 时必需）
    dedup_enabled: bool = True              # 去重功能开关（启用时执行三级去重检查）
    auto_sync_enabled: bool = False
    auto_sync_interval_minutes: int = 60


class ConfigOut(BaseModel):
    api_id: str
    api_hash: str
    bot_token: Optional[str] = None
    resource_limits: ResourceLimits
    proxy: ProxyConfig
    download_type: list[str]
    max_retry_count: int
    repository: RepositoryConfig = RepositoryConfig()
    notification_enabled: bool = False            # 启用完成通知（持久化于 config.yaml 的 preference 区块）
    error_notification_enabled: bool = False      # 启用错误通知（持久化于 config.yaml 的 preference 区块）


class ConfigUpdate(BaseModel):
    resource_limits: Optional[ResourceLimits] = None
    proxy: Optional[ProxyConfig] = None
    download_type: Optional[list[str]] = None
    max_retry_count: Optional[int] = None
    repository: Optional[RepositoryConfig] = None
    notification_enabled: Optional[bool] = None       # 更新时合并写入 preference.notification_enabled
    error_notification_enabled: Optional[bool] = None # 更新时合并写入 preference.error_notification_enabled
```

> **配置合并说明**：原 `config.yaml` 与 `global_config.yaml` 已合并为单一 `config.yaml`，`repository` 节为新增配置段。

### 6.7 监控模型

```python
# module/api/models/monitor.py
from pydantic import BaseModel


class DiskInfo(BaseModel):
    total: int
    used: int
    free: int


class MonitorStats(BaseModel):
    cpu_percent: float
    memory_percent: float
    disk: DiskInfo
    download_speed: int
    upload_speed: int
    running_tasks: int
    queued_tasks: int


class ResourceStatus(BaseModel):
    disk: DiskInfo
    memory_percent: float
    max_concurrent_tasks: int
    current_running_tasks: int
```

### 6.8 仓库模式模型

```python
# module/api/models/repository.py
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class RepositoryFileOut(BaseModel):
    file_unique_id: str
    file_id: str
    content_hash: str
    file_size: int
    file_type: str
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    repo_chat_id: int
    repo_message_id: int
    status: str
    created_at: datetime
    updated_at: datetime


class RepositorySourceOut(BaseModel):
    file_unique_id: str
    source_chat_id: int
    source_message_id: int
    source_link: Optional[str] = None
    created_at: datetime


class FileDistributionOut(BaseModel):
    file_unique_id: str
    target_chat_id: int
    target_message_id: Optional[int] = None
    method: Literal["copy_message", "file_id_send", "upload"]
    task_id: Optional[str] = None
    created_at: datetime


class DistributeRequest(BaseModel):
    file_unique_id: str
    target_chat_id: int
    caption: Optional[str] = None


class RepositoryStatusOut(BaseModel):
    enabled: bool
    chat_id: Optional[int] = None
    total_files: int = 0
    total_distributions: int = 0
    last_sync_at: Optional[datetime] = None
```

---

## 7. 错误处理与统一响应

### 7.1 统一响应格式

所有 REST API 返回统一结构：

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

错误时：

```json
{
  "code": 1001,
  "message": "任务超过 10GB 上限",
  "data": null
}
```

### 7.2 异常类设计

```python
# module/api/exceptions.py
from fastapi import Request, status
from fastapi.responses import JSONResponse


class TRMDAPIException(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


class TaskSizeExceeded(TRMDAPIException):
    def __init__(self):
        super().__init__(code=1001, message="任务超过 10GB 上限", status_code=400)


class TaskSizeWarning(TRMDAPIException):
    def __init__(self, size_human: str):
        super().__init__(code=1002, message=f"任务大小 {size_human} 超过 5GB，请确认", status_code=400)


class ListenAlreadyExists(TRMDAPIException):
    def __init__(self, message: str = "该对话已存在运行中的监听任务"):
        super().__init__(code=1003, message=message, status_code=409)


class InvalidRecentCount(TRMDAPIException):
    def __init__(self):
        super().__init__(code=1004, message="recent_count 必须大于 0", status_code=400)


class RateLimited(TRMDAPIException):
    def __init__(self, retry_after: int):
        super().__init__(code=1005, message="请求过于频繁，请稍后再试", status_code=429)
        self.retry_after = retry_after


def setup_exception_handlers(app):
    @app.exception_handler(TRMDAPIException)
    async def trmd_exception_handler(request: Request, exc: TRMDAPIException):
        content = {"code": exc.code, "message": exc.message, "data": None}
        if hasattr(exc, "retry_after"):
            content["retry_after"] = exc.retry_after
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": 422,
                "message": "请求参数校验失败",
                "data": {"detail": exc.errors()}
            }
        )
```

### 7.3 日志与错误隐藏

- 生产环境不返回 Python Traceback 给客户端。
- 内部错误记录到服务端日志，并生成 `request_id` 返回给客户端便于排查。
- 敏感信息（Token、密码）不记录到日志。

---

## 8. TDD 测试策略

### 8.1 测试分层

| 层级 | 工具 | 目标 |
|------|------|------|
| **单元测试** | `pytest` + `pytest-asyncio` | 依赖注入、Pydantic 模型、TokenManager、异常处理 |
| **集成测试** | `httpx.AsyncClient` + `TestClient` | 路由端到端、中间件、统一响应 |

### 8.2 单元测试用例清单

#### 认证模块

| 用例 | 输入 | 期望结果 |
|------|------|----------|
| `test_create_token` | TTL=3600 | 返回非空字符串并记录过期时间 |
| `test_validate_valid_token` | 未过期 Token | `True` |
| `test_validate_expired_token` | 过期 Token | `False` |
| `test_revoke_token` | 有效 Token | 再次校验为 `False` |
| `test_revoke_all` | 多个 Token | 全部失效 |

#### 依赖注入

| 用例 | 输入 | 期望结果 |
|------|------|----------|
| `test_require_token_header` | `Authorization: Bearer valid_token` | 返回 Token |
| `test_require_token_query` | `?token=valid_token` | 返回 Token |
| `test_require_token_missing` | 无 Token | 401 `MISSING_TOKEN` |
| `test_require_token_invalid` | 错误 Token | 401 `INVALID_OR_EXPIRED_TOKEN` |

#### 任务路由

| 用例 | 输入 | 期望结果 |
|------|------|----------|
| `test_create_download_task` | 合法下载参数 | 201，返回任务 ID |
| `test_create_task_with_source_identifier` | `source_identifier` + `range_mode=recent` | 201，IdentifierService 被调用 |
| `test_create_listen_download_task` | `task_type=listen_download` | 201，返回监听任务 ID |
| `test_create_duplicate_listen_task` | 同一 chat_id 第二次创建 | 409 `LISTEN_ALREADY_EXISTS` |
| `test_create_task_invalid_recent_count` | `recent_count=0` | 400 `INVALID_RECENT_COUNT` |
| `test_create_task_size_exceeded` | 大小 > 10GB | 400 `TASK_SIZE_EXCEEDED` |
| `test_create_task_size_warning` | 5GB < 大小 < 10GB | 400 `TASK_SIZE_WARNING` |
| `test_list_tasks` | 无/有过滤 | 200，返回分页列表 |
| `test_cancel_running_task` | running 任务 | 200，状态变为 cancelled |
| `test_retry_failed_task` | failed 任务 | 200，状态变为 queued |
| `test_task_not_found` | 不存在的 task_id | 404 `TASK_NOT_FOUND` |

#### 标识符解析路由

| 用例 | 输入 | 期望结果 |
|------|------|----------|
| `test_resolve_username` | `identifier=@seseYunBot` | 200，返回 chat_id 与元信息 |
| `test_resolve_invalid_identifier` | `identifier=bad!!!` | 400 `INVALID_IDENTIFIER` |
| `test_resolve_rate_limited` | 触发限流 | 429 `RATE_LIMITED`，响应含 `retry_after` |

#### 配置路由

| 用例 | 输入 | 期望结果 |
|------|------|----------|
| `test_get_config` | 有效 Token | 200，返回配置 |
| `test_update_config` | 合法配置 | 200，配置持久化 |
| `test_update_config_invalid` | `task_size_max_gb < task_size_warning_gb` | 422 `VALIDATION_ERROR` |

### 8.3 Mock 点

| 被 Mock 对象 | 原因 | 注入方式 |
|--------------|------|----------|
| `TokenManager` | 避免真实 Token 生成与过期等待 | `create_app(token_manager=mock)` |
| `TaskManager` | 避免真实 Telegram API 调用与文件 I/O | `create_app(task_manager=mock)` |
| `FileManager` | 避免真实文件系统操作 | `create_app(file_manager=mock)` |
| `ConfigManager` | 避免修改真实配置文件 | `create_app(config_manager=mock)` |
| `Monitor` | 避免真实系统资源采集 | `create_app(monitor=mock)` |
| `RepositoryManager` | 避免真实仓库同步与分发操作 | `create_app(repository_manager=mock)` |
| `IdentifierService` | 避免真实 Telegram get_chat 调用 | 在 TaskManager / chats 路由 Mock 中隔离 |
| `TelegramClient` | 避免连接 Telegram 服务器 | 在 TaskManager Mock 中隔离 |

### 8.4 覆盖率目标

| 模块 | 目标覆盖率 |
|------|------------|
| `module/api/dependencies.py` | ≥ 90% |
| `module/api/exceptions.py` | ≥ 90% |
| `module/api/responses.py` | ≥ 90% |
| `module/api/routes/*.py` | ≥ 80% |
| `module/api/models/*.py` | ≥ 85% |
| **Web API 模块整体** | **≥ 80%** |

---

## 9. 依赖关系

### 9.1 新增 Python 依赖

| 依赖 | 版本建议 | 用途 |
|------|----------|------|
| `fastapi` | `>=0.110.0` | Web 框架 |
| `uvicorn[standard]` | `>=0.29.0` | ASGI 服务器 |
| `python-multipart` | `>=0.0.9` | 文件上传解析 |
| `pydantic` | FastAPI 自带 | 数据模型与校验 |
| `httpx` | `>=0.27.0` | 集成测试 |
| `pytest` | `>=8.0.0` | 单元/集成测试 |
| `pytest-asyncio` | `>=0.23.0` | 异步测试 |
| `pytest-cov` | `>=5.0.0` | 覆盖率统计 |

> **注意**：当前 `requirements.txt` 与 `pyproject.toml` 尚未包含 FastAPI 相关依赖，需在实现阶段补充。

### 9.2 内部模块依赖

```
module/api/app.py
    ├── module/api/routes/tasks.py  ──>  module/core/task_manager.py
    ├── module/api/routes/chats.py  ──>  module/core/identifier_service.py
    ├── module/api/routes/files.py  ──>  module/core/file_manager.py
    ├── module/api/routes/config.py ──>  module/core/config_manager.py
    ├── module/api/routes/monitor.py ──> module/core/monitor.py
    ├── module/api/routes/repository.py ──> module/repository/repository_manager.py
    └── module/api/dependencies.py  ──>  module/core/token_manager.py
```

### 9.3 外部服务依赖

| 服务 | 用途 | 说明 |
|------|------|------|
| Telegram API | 频道/私聊信息、消息统计、下载/转发/上传 | 通过 `TelegramClient` / `IdentifierService` 封装，Web API 不直接调用 |
| SQLite | 任务与 Token 持久化 | 本地文件 |
| YAML 配置文件 | 用户配置持久化 | 本地文件 |

---

## 10. 风险与假设

### 10.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| **Token 泄露** | 攻击者可在 1 小时内访问 WebUI | Token 仅通过 Bot 私聊发放；支持 `/web_revoke` 一键撤销；Cookie 使用 HttpOnly |
| **长任务期间 Token 过期** | 前端轮询请求 401 | 前端在 Token 过期前调用 `/api/auth/refresh` 或重新通过 Bot 获取 |
| **大文件上传阻塞** | 单连接长时间占用 | 使用流式上传；设置超时；大文件分片上传（未来扩展） |
| **Telegram API 限流** | 消息统计/分析耗时超过 200ms | 异步执行；提供 estimate 快速估算；analyze 明确提示耗时；resolve 端点前端防抖 |
| **单用户并发访问** | 多个浏览器同时操作可能导致状态冲突 | 当前版本定位为单用户；核心管理器加锁或原子操作 |
| **向后兼容风险** | 新增依赖可能改变现有启动流程 | `main.py` 启动时自动启动 Web API，无需额外参数 |

### 10.2 假设

| 假设 | 说明 |
|------|------|
| **单用户** | 不实现多用户隔离、权限角色、登录系统 |
| **Bot 与 WebUI 同进程** | 默认 Bot 和 Web API 运行在同一 Python 进程，便于共享核心管理器 |
| **网络可达** | WebUI 浏览器与 FastAPI 服务在同一网络或公网可达 |
| **配置文件已存在** | 首次启动 Bot 已完成 `config.yaml` 配置 |
| **Token 仅通过 Bot 发放** | 不开放自助登录/注册接口 |
| **前端无构建** | WebUI 使用原生 HTML + Alpine.js + Tailwind CDN，后端直接提供静态文件 |

---

## 11. 附录

### 11.1 新增/修改文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `module/api/` | 新增目录 | Web API 模块 |
| `module/core/` | 新增目录 | 核心业务层（TaskManager、FileManager、IdentifierService 等） |
| `module/web.py` | 修改 | 保留现有 ttyd 方案；新增 FastAPI 启动分支 |
| `module/bot.py` | 修改 | 新增 `/web`、`/web_revoke` 命令 |
| `main.py` | 修改 | 解析 `--port` 参数；Web API 随主程序自动启动 |
| `requirements.txt` | 修改 | 增加 FastAPI、uvicorn、httpx、pytest 等 |
| `pyproject.toml` | 修改 | 同步依赖 |

### 11.2 参考文档

- [私聊按用户名下载-PRD.md](./私聊按用户名下载-PRD.md)
- [交互增强设计.md](./交互增强设计.md)
- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [Pydantic 官方文档](https://docs.pydantic.dev/)

---

> **文档结束**

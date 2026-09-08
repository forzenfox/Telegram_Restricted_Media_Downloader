# WebUI 模块级设计文档

> **项目名称**: Telegram_Restricted_Media_Downloader  
> **文档版本**: v1.3
> **创建日期**: 2026-06-18
> **更新日期**: 2026-07-03
> **作者**: SOLO
> **状态**: 已更新
> **关联文档**: [交互增强设计.md](./交互增强设计.md)、[私聊按用户名下载-PRD.md](./私聊按用户名下载-PRD.md)

---

## 变更记录

| 版本 | 日期 | 变更内容 | 状态 |
|------|------|----------|------|
| v1.3 | 2026-07-03 | 同步 交互增强设计 v7.0: Monitor 页面合并至 Dashboard，移除独立 Monitor 页面设计；移除 /monitor 路由；移除 monitor.js 页面脚本；清理相关 x-data、轮询配置、API 封装与测试项 | 已更新 |
| v1.2 | 2026-07-03 | 同步 PRD v2.3: 支持 username/chat_id/t.me 解析;新增解析按钮与对话信息卡片;消息范围增加最近N条;新增媒体类型+文件大小过滤;新增监听任务创建表单;新增仓库备份开关;全面采用 REST 轮询替代 WebSocket;更新 x-data 字段与测试策略 | 已更新 |
| v1.1 | 2026-06-21 | 初始 WebUI 模块设计 | 已归档 |

## 1. 设计目标与职责边界

### 1.1 设计目标

WebUI 模块是 Telegram_Restricted_Media_Downloader 的**浏览器端管理界面**，目标是为单用户提供一个无需命令行记忆、可视化配置与监控的入口。具体目标如下：

| 目标 | 说明 |
|------|------|
| **降低操作门槛** | 用表单、选择器、进度条替代 `/forward`、`/upload` 等命令参数 |
| **统一任务管理** | 在一个页面内完成下载、转发、上传任务的创建、监控、重试、取消 |
| **实时可视化** | 通过 REST 轮询拉取任务进度、日志流、系统监控数据并渲染 |
| **资源可控** | 在任务创建前明确告知总量，超限时阻止创建，避免服务器资源耗尽 |
| **向后兼容** | 不修改 Bot 命令语义，新增 WebUI 作为可选增强入口 |
| **无构建部署** | 前端不依赖 npm/webpack，直接由 FastAPI 托管静态文件 |

### 1.2 职责边界

```
┌─────────────────────────────────────────────────────────────┐
│                         用户层                               │
│  ┌──────────────────┐         ┌──────────────────────────┐  │
│  │  Telegram Bot    │         │      WebUI (浏览器)       │  │
│  │  轻量操作入口     │         │   完整管理界面            │  │
│  └────────┬─────────┘         └────────────┬─────────────┘  │
└───────────┼────────────────────────────────┼────────────────┘
            │                                │
            └────────────────┬───────────────┘
                             │
              ┌──────────────▼──────────────┐
              │      核心业务层（共享）       │
              │ TaskManager / FileManager   │
              │ ConfigManager / Monitor     │
              └─────────────────────────────┘
```

WebUI 模块只负责：

1. **页面渲染与交互**：HTML 模板、Alpine.js 状态、Tailwind CSS 样式。
2. **数据呈现与提交**：调用后端 REST API 获取数据，提交用户输入。
3. **实时刷新展示**：通过定时 REST 轮询获取任务、日志、监控数据并更新页面。
4. **Token 携带**：首次从 URL 读取 `?token=`，后续通过 Cookie / Header 维持认证。

WebUI 模块**不**负责：

- 业务规则判定（如资源保护阈值判断、任务状态机转换）——由后端共享模块完成。
- Telegram 客户端操作（下载、上传、转发）——由后端 Pyrogram 客户端完成。
- 配置持久化——由后端 ConfigManager 完成。

---

## 2. 页面结构与导航

### 2.1 总体布局

所有页面采用统一的 **Sidebar + Main Content** 布局：

```
┌─────────────────────────────────────────────────────────┐
│  Header: Logo | 页面标题 | 刷新/全屏/帮助/退出           │
├──────────┬──────────────────────────────────────────────┤
│          │                                              │
│  Sidebar │              Main Content                    │
│          │                                              │
│  ┌────┐  │  ┌──────────────────────────────────────┐  │
│  │ 📊 │  │  │                                      │  │
│  │ Dashboard  │  │                                      │  │
│  ├────┤  │  │                                      │  │
│  │ 📦 │  │  │                                      │  │
│  │ Tasks    │  │                                      │  │
│  ├────┤  │  │                                      │  │
│  │ 📁 │  │  │                                      │  │
│  │ Files    │  │                                      │  │
│  ├────┤  │  │                                      │  │
│  │ 🗃️ │  │  │                                      │  │
│  │ Repository│ │                                      │  │
│  ├────┤  │  └──────────────────────────────────────┘  │
│  │ ⚙️ │  │                                              │
│  │ Settings │  │                                              │
│  └────┘  │                                              │
│          │                                              │
└──────────┴──────────────────────────────────────────────┘
```

### 2.2 页面清单

| 页面 | 路由 | 核心功能 |
|------|------|---------|
| **Dashboard** | `/` | 系统概览、快速创建任务入口、最近任务、资源状态 |
| **Tasks** | `/tasks` | 任务列表、创建任务弹窗/抽屉、任务详情抽屉、操作（开始/重试/取消/删除） |
| **Files** | `/files` | 文件树浏览、多选、上传预览、媒体组配置 |
| **Repository** | `/repository` | 仓库文件列表、来源映射视图、分发历史、手动同步触发、分发表单 |
| **Settings** | `/settings` | 基础配置、下载/上传配置、代理配置、通知配置、资源限制、仓库配置 |

### 2.3 导航交互

- **Sidebar 导航**：单页应用（SPA）方式，通过 Alpine.js 路由状态切换内容区域，不触发整页刷新。
- **刷新后保持位置**：使用 `localStorage` 保存最后一次访问的页面路径，刷新后自动恢复。
- **移动端适配**：小屏幕下 Sidebar 折叠为汉堡菜单，Main Content 全宽显示。
- **URL 同步**：切换页面时同步更新浏览器地址（`history.pushState`），支持前进/后退。

### 2.4 错误/认证页面

| 页面 | 路由 | 触发条件 |
|------|------|---------|
| **Token 过期页** | `/error?code=401` | URL Token 无效或过期 |
| **无权限页** | `/error?code=403` | Token 被撤销 |
| **资源不足页** | `/error?code=503` | 磁盘/内存不满足创建条件 |
| **服务未启动页** | `/error?code=502` | FastAPI 后端不可达 |

---

## 3. 前端状态管理（Alpine.js 数据流）

### 3.1 技术选型理由

项目选用 **Alpine.js** 作为前端框架，原因如下：

- **无构建步骤**：直接通过 CDN 或本地静态文件引入即可运行。
- **体积小巧**：~15 KB（压缩后），适合资源受限的服务器。
- **贴近原生 HTML**：在 HTML 标签内通过 `x-data`、`x-bind`、`x-on` 声明状态与行为。
- **与 Tailwind CSS 配合良好**：无需额外 CSS-in-JS 方案。

### 3.2 全局 Store 设计

使用 Alpine.js `Alpine.store()` 定义全局状态：

```javascript
Alpine.store('app', {
    // 页面路由
    currentPage: 'dashboard',

    // 认证状态
    auth: {
        token: null,
        expiresAt: null,
        isAuthenticated: false,
    },

    // 系统状态
    system: {
        diskTotal: 0,
        diskUsed: 0,
        diskFree: 0,
        memoryUsed: 0,
        memoryTotal: 0,
        cpuPercent: 0,
    },

    // 实时任务与日志
    tasks: [],
    logs: [],

    // 仓库状态
    repository: {
        files: [],
        sources: [],
        distributions: [],
        status: {},  // { enabled, chat_id, file_count, last_sync_time }
    },

    // REST 轮询控制
    polling: {
        tasks: null,    // 任务列表轮询 interval id
        logs: null,     // 日志轮询 interval id
        tasksInterval: 3000,
        logsInterval: 2000,
    },

    // 通知队列
    notifications: [],
});
```

### 3.3 页面级状态（x-data）

每个主要页面使用独立的 `x-data` 作用域，避免全局状态污染：

| 页面 | x-data 核心字段 |
|------|----------------|
| **Dashboard** | `quickTasks`、`recentTasks`、`stats`、`resourceStatus` |
| **Tasks** | `taskList`、`filterStatus`、`selectedTask`、`createForm`、`messageRangeMode`、`sourceIdentifier`、`targetIdentifier`、`sourceResolveResult`、`targetResolveResult`、`resolveResult`、`recentCount`、`mediaTypes`、`minSize`、`maxSize`、`sizeUnit`、`enableRepositoryBackup`、`isResolving`、`resolveDebounceTimer` |
| **Files** | `currentPath`、`fileTree`、`selectedFiles`、`uploadQueue`、`mediaGroupSize` |
| **Repository** | `repoFiles`、`sources`、`distributions`、`syncStatus`、`distributionForm` |
| **Settings** | `config`、`originalConfig`、`activeTab`、`saving` |

### 3.4 数据流示意图

```
┌──────────────────────────────────────────────────────────────┐
│                        Alpine.js 前端                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 页面 x-data │  │ 全局 Store  │  │  Component x-data   │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                    │             │
│         └────────────────┴────────────────────┘             │
│                          │                                   │
│                    ┌─────▼─────┐                             │
│                    │ api.js    │  Fetch / REST 轮询封装       │
│                    └─────┬─────┘                             │
└──────────────────────────┼───────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
        ┌─────▼─────┐           ┌──────▼──────┐
        │ REST API  │           │   Static    │
        │  (/api/*) │           │  (/static)  │
        └───────────┘           └─────────────┘
```

### 3.5 状态更新原则

1. **单向数据流**：后端是唯一的真实数据源，前端通过 REST API 轮询获取后更新 Store。
2. **乐观更新仅用于交互反馈**：例如点击“取消任务”后立即更新按钮状态，但若后端返回失败则回滚。
3. **错误统一进入 Store**：所有 API 错误写入 `$store.app.notifications`，由全局通知组件渲染。
4. **轮询数据合并**：不覆盖整列表，而是按任务 ID 做局部更新，减少 DOM 抖动。

### 3.6 弹框/遮罩 FOUC 防护（x-cloak）

所有弹框、抽屉、遮罩等全屏覆盖层元素均由 `x-show` 控制显隐。`x-show` 仅在 Alpine.js 初始化完成后生效：初始化之前元素默认可见，若静态资源（`vendor/alpinejs.js` 为 body 末尾最后一个阻塞脚本）加载慢于浏览器首帧渲染，页面加载瞬间会闪现全部弹框（生产环境网络延迟下可见，本地磁盘加载无感）。

**约定**：fixed 定位且由 `x-show` 控制显隐（非常显）的覆盖层元素必须同时携带 `x-cloak`，配合 style.css 的 `[x-cloak] { display: none !important; }` 规则，在 Alpine 初始化前保持隐藏；初始化完成后 Alpine 自动移除 `x-cloak`，由 `x-show` 无缝接管。该约定由契约测试 `TestOverlayFOUC`（tests/unit/api/test_web_ui_templates.py）强制校验。

---

## 4. 与后端交互方式（REST API 调用与轮询）

### 4.1 REST API 调用封装

所有 API 请求通过统一的 `api.js` 模块处理，核心职责：

- 注入 `Authorization: Bearer <token>` Header。
- 处理 `401 Unauthorized` 自动跳转到 `/error?code=401`。
- 统一序列化请求体、解析 JSON 响应。
- 对 5xx 错误进行降级提示。

```javascript
// api.js 伪代码
const api = {
    baseUrl: '',
    token: null,

    async request(method, path, body = null) {
        const headers = {
            'Content-Type': 'application/json',
        };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const res = await fetch(`${this.baseUrl}${path}`, { method, headers, body: body ? JSON.stringify(body) : null });
        if (res.status === 401) {
            window.location.href = '/error?code=401';
            return;
        }
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        }
        return res.status === 204 ? null : await res.json();
    },

    getTasks(status) { return this.request('GET', `/api/tasks${status ? '?status=' + status : ''}`); },
    createTask(payload) { return this.request('POST', '/api/tasks', payload); },
    startTask(id) { return this.request('POST', `/api/tasks/${id}/start`); },
    cancelTask(id) { return this.request('DELETE', `/api/tasks/${id}`); },
    retryTask(id) { return this.request('POST', `/api/tasks/${id}/retry`); },
    getFiles(path) { return this.request('GET', `/api/files?path=${encodeURIComponent(path)}`); },
    getConfig() { return this.request('GET', '/api/config'); },
    updateConfig(config) { return this.request('PUT', '/api/config', config); },
    resolveChat(identifier) { return this.request('GET', `/api/chats/resolve?identifier=${encodeURIComponent(identifier)}`); },
    estimateMessages(chatId, payload) { return this.request('POST', `/api/chats/${chatId}/messages/estimate`, payload); },
    analyzeMessages(chatId, payload) { return this.request('POST', `/api/chats/${chatId}/messages/analyze`, payload); },
    getResourceStatus() { return this.request('GET', '/api/resource/status'); },
    getLogs(level, since) { return this.request('GET', `/api/logs?level=${level || ''}&since=${since || ''}`); },  // (⏳ 待实现，日志查看功能已移除)
};
```

### 4.2 REST 轮询封装

```javascript
// polling.js 伪代码
class Poller {
    constructor(fetchFn, interval, onData) {
        this.fetchFn = fetchFn;
        this.interval = interval;
        this.onData = onData;
        this.timer = null;
    }

    start() {
        this.run();
        this.timer = setInterval(() => this.run(), this.interval);
    }

    async run() {
        try {
            const data = await this.fetchFn();
            this.onData(data);
        } catch (err) {
            // 401 已在 api.js 统一处理；其他错误进入通知队列
            console.error('Polling error', err);
        }
    }

    stop() {
        if (this.timer) clearInterval(this.timer);
    }
}
```

### 4.3 轮询接口与数据格式

| 轮询源 | 接口 | 返回内容 | 前端处理 | 默认间隔 |
|--------|------|---------|---------|---------|
| **任务列表** | `GET /api/tasks` | `[{id, status, progress, speed, eta, ...}]` | 按任务 ID 合并到 `$store.app.tasks` | 3 秒 |
| **系统监控** | `GET /api/monitor/stats` | `{cpu, memory, disk, network_speed}` | 更新 `$store.app.system` 与 Dashboard 图表（Monitor 页面已合并至 Dashboard） | 5 秒 |
| **日志流** | `GET /api/logs?since=...` | `[{level, timestamp, source, message}]` | 追加到 `$store.app.logs`，按级别过滤后渲染 | 2 秒 |

> **注意**：根据 交互增强设计 v7.0 设计变更，日志查看功能已移除。`GET /api/logs` 端点尚未实现（⏳ 待实现），如需恢复该功能需在后续版本中重新设计。当前任务相关日志仅在 Tasks 页面的任务详情抽屉中查看。

**轮询控制原则**：

- 页面可见且在当前路由时启动轮询；切离页面或进入后台时暂停，减少资源消耗。
- 日志接口使用 `since` 参数（上次拉取的最大时间戳），避免重复拉取全部日志。
- 轮询失败不阻塞界面，错误进入全局通知队列；连续多次失败可在页面上方给出离线提示。

### 4.4 Token 传递方式

| 场景 | Token 传递位置 | 说明 |
|------|---------------|------|
| 首次页面访问 | URL Query `?token=xxx` | Bot `/web` 命令生成的链接 |
| 后续 REST 请求 | `Authorization: Bearer xxx` Header | 从 Cookie 或 Store 读取 |
| Cookie | HttpOnly Cookie `trmd_token` | 后端首次验证成功后下发，前端 JS 不可读 |

### 4.5 Token 续期机制

- Token 有效期 **1 小时**，由后端 `TokenManager` 控制。
- 轮询请求若返回 `401`，前端跳转至 `/error?code=401`，提示用户重新在 Bot 中发送 `/web`。
- **不实现无感续期**：因为单用户场景下，重新获取链接的成本低于维护刷新 Token 的复杂度。

---

## 5. 核心页面详细设计

### 5.1 Dashboard 页面

**路由**: `/`

**功能定位**: 系统概览与快速操作入口，页面加载后 < 3 秒内呈现核心信息。

**包含区域**:

| 区域 | 内容 |
|------|------|
| **资源卡片** | 磁盘剩余、内存占用、CPU 占用、当前运行任务数 |
| **快捷操作** | 新建下载任务、新建转发任务、新建监听任务、新建上传任务、打开设置 |
| **最近任务** | 最近 5 条任务的状态、进度、最后更新时间 |
| **实时速度** | 下载/上传速度折线图（基于 `/api/monitor/stats` 轮询数据） |

**Alpine.js 状态示例**:

```html
<div x-data="{
    stats: { running: 0, queued: 0, completed: 0, failed: 0 },
    resource: { diskFree: 0, memoryPercent: 0, cpuPercent: 0 },
    recentTasks: [],
    init() {
        this.loadStats();
        this.$store.app.$watch('tasks', () => this.recentTasks = this.$store.app.tasks.slice(0, 5));
    },
    async loadStats() { ... }
}">
```

### 5.2 Tasks 页面

**路由**: `/tasks`

**功能定位**: 任务全生命周期管理，覆盖下载、转发、上传、监听下载、监听转发五种任务类型。

**布局**:

```
┌─────────────────────────────────────────────────────────────────┐
│  任务管理                                           [+ 新建任务] │
├─────────────────────────────────────────────────────────────────┤
│  状态筛选: [全部] [排队中] [执行中] [监听中] [已完成] [失败] [已取消] │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ #101  下载任务      ● 执行中  78%  12.5 MB/s  [详情] [取消] │  │
│  │ #102  转发任务      ○ 排队中  [详情] [取消]                │  │
│  │ #103  监听下载任务  ■ 监听中  [详情] [取消]                │  │
│  │ #104  上传任务      ✓ 已完成  2.1 GB  [详情] [删除]        │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**任务列表字段**:

| 字段 | 说明 |
|------|------|
| ID | 任务唯一编号 |
| 类型 | 下载 / 转发 / 上传 / 监听下载 / 监听转发 |
| 状态 | pending / queued / running / completed / failed / cancelled；监听任务长期运行，终态为 running / cancelled / failed |
| 进度 | 百分比 + 进度条；监听任务显示已处理消息数 |
| 速度 | 当前下载/上传速度 |
| 已处理/总数 | 例如 `45 / 120` 条消息；监听任务总数动态增长 |
| 创建时间 | 本地格式化时间 |
| 操作 | 开始 / 重试 / 取消 / 删除 / 详情 |

**创建任务弹窗/抽屉**:

点击“新建任务”后弹出抽屉，按任务类型展示对应表单。核心增强点：

1. **源输入增强**
   - 输入框同时支持 `username` / `chat_id` / `t.me` 链接。
   - 右侧增加 **[解析]** 按钮，调用 `GET /api/chats/resolve?identifier=xxx`。
   - 解析按钮带 **3 秒防抖**：点击后 3 秒内再次点击无效，避免触发 Telegram FloodWait。
   - 解析成功后展示对话信息卡片（名称、类型、消息数、媒体数、是否有权限）。

2. **目标输入增强**
   - 在转发任务与监听转发任务中显示目标输入框，与源输入采用同样的解析能力。

3. **消息范围扩展**
   - 在原有日期范围 / ID 范围 / 多个 ID / 全部消息基础上，新增 **最近 N 条** 选项（`1 ≤ N ≤ 1000`）。

4. **媒体过滤增强**
   - 保留媒体类型多选（video / photo / document / audio）。
   - 新增文件大小范围：最小值、最大值，单位可选 **MB / GB**；提交时统一转换为字节。

5. **监听任务表单**
   - 选择监听下载或监听转发。
   - 源对话必填，目标对话仅在监听转发时必填。
   - 可选媒体类型过滤。
   - 提交后创建长期运行的 `LISTEN_*` 任务。

6. **仓库备份开关**
   - 在下载任务与监听下载任务中显示"备份到仓库频道"复选框。
   - 默认勾选状态 = `repository.auto_backup_downloads` 配置值；用户可手动覆盖。
   - Settings 页面仓库标签页需展示该配置项（`repository.auto_backup_downloads`），修改后影响新建下载/监听下载任务的默认勾选状态。

**任务详情抽屉**:

点击任务行右侧“详情”按钮，从右侧滑出抽屉，展示：

- 任务参数（源对话、目标对话、消息范围、媒体类型、大小过滤、仓库备份选项）。
- 进度条与子任务统计（成功/失败/跳过）。
- 实时日志流（该任务相关的日志，通过 `/api/logs` 轮询）。
- 错误信息列表（可展开每条错误详情）。
- 监听任务额外展示已注册 Handler 的 chat_id 与最近处理消息时间。

### 5.3 Files 页面

**路由**: `/files`

**功能定位**: 本地文件浏览、选择、上传准备。

**布局**:

```
┌──────────────────────────────────────────────────────────────┐
│  文件管理                      [刷新] [上传选中文件]          │
├──────────────────────────────────────────────────────────────┤
│  路径: /downloads/channel_a > [父目录]                        │
├──────────────────────────────────────────────────────────────┤
│  ┌────────────┐  ┌─────────────────────────────────────────┐ │
│  │ 目录树      │  │  文件列表                                │ │
│  │ - channel_a│  │  [ ] file_001.mp4   120 MB  2026-06-18   │ │
│  │ - channel_b│  │  [ ] file_002.jpg    3 MB  2026-06-18   │ │
│  │ - temp     │  │  [ ] file_003.mp4   890 MB  2026-06-17   │ │
│  └────────────┘  └─────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│  已选择 2 个文件，共 1.01 GB    [清空] [配置媒体组] [上传]     │
└──────────────────────────────────────────────────────────────┘
```

**交互要点**:

- 单击目录进入，面包屑可回退到任意父级。
- 文件列表支持勾选，目录支持进入后全选当前页。
- 选中文件后底部出现操作栏，可配置媒体组大小（默认 10，Telegram 上限）。
- 点击“上传”后弹出上传配置抽屉：选择目标频道、是否发送为媒体组、是否上传后删除本地文件。

### 5.4 Settings 页面

**路由**: `/settings`

**功能定位**: 可视化配置管理，替代命令行交互式修改。

**标签页结构**:

| 标签页 | 配置项 |
|--------|--------|
| **基础** | API ID、API Hash、Bot Token、工作目录 |
| **下载** | 下载类型（图片/视频/文档/音频等）、下载并发数、重试次数 |
| **上传** | 上传并发数、媒体组大小、默认发送方式、上传后删除本地文件（`preference.upload.delete`） |
| **代理** | 启用代理开关、代理类型、地址、端口、认证 |
| **通知** | 完成通知、错误通知开关 |
| **仓库** | 启用仓库开关、仓库频道标识符（username/chat_id/t.me）、自动备份下载开关、自动同步开关、同步间隔 |
| **资源限制** | `max_concurrent_tasks`、`task_size_warning_gb`、`task_size_max_gb` 等 |

> **说明**：配置已合并为单一 `config.yaml`（原 `config.yaml` + `global_config.yaml` 已合并），所有配置项统一在一个文件中管理。

**交互要点**:

- 页面加载时拉取 `/api/config` 填充表单。
- 修改后启用“保存”按钮，保存时显示 loading 与成功提示。
- 对必填项进行前端基础校验（非空、数字范围、端口范围）。
- 后端再次校验并返回详细错误，前端展示在对应字段下方。
- 不保存时离开页面给出确认提示。

### 5.5 Repository 页面

**路由**: `/repository`

**功能定位**: 仓库模式管理，查看仓库文件索引、来源映射、分发历史，触发同步与分发操作。

**布局**:

```
┌──────────────────────────────────────────────────────────────┐
│  仓库管理            状态: ● 已启用  Chat: -100xxx  文件: 234  │
│                      最后同步: 2026-06-18 10:30:00  [手动同步] │
├──────────────────────────────────────────────────────────────┤
│  [文件列表]  [来源映射]  [分发历史]                            │
├──────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────┐  │
│  │  文件列表视图:                                          │  │
│  │  file_id  | 文件名        | 类型  | 大小   | 来源消息    │  │
│  │  f001     | video_001.mp4 | video | 120 MB | #101       │  │
│  │  f002     | image_002.jpg | photo | 3 MB   | #102       │  │
│  │  ...                                                   │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  [+ 新建分发]                                                │
└──────────────────────────────────────────────────────────────┘
```

**状态显示**:

| 字段 | 说明 |
|------|------|
| 启用状态 | 已启用（绿色）/ 已禁用（灰色），与 Settings 仓库标签页联动 |
| Chat ID | 仓库频道 Chat ID |
| 文件数量 | 仓库索引中的文件总数 |
| 最后同步时间 | 上次自动/手动同步完成的时间 |

**标签页**:

| 标签页 | 内容 |
|--------|------|
| **文件列表** | 仓库索引中的所有文件，支持搜索与类型过滤 |
| **来源映射** | 文件与源消息的映射关系视图 |
| **分发历史** | 历次分发记录，含目标频道、分发方式、文件数、时间 |

**交互要点**:

- 仓库未启用时，页面显示引导提示，引导用户前往 Settings 启用仓库并配置 Chat ID。
- 点击"手动同步"调用后端同步接口，按钮显示 loading 状态，完成后刷新文件列表与状态。
- 点击"新建分发"弹出分发表单：选择目标频道、分发方式（copy_message / file_id_send / upload）、文件范围。

### 5.6 Monitor 页面（已废弃）

> **设计变更说明**：Monitor 页面已合并至 Dashboard（见 [交互增强设计.md](./交互增强设计.md) v7.0 设计变更），不再保留独立页面。原 Monitor 页面的路由 `/monitor` 已废弃，监控统计数据通过 Dashboard 页面的 REST 轮询获取（`GET /api/monitor/stats`，默认每 5 秒拉取一次），在 Dashboard 的"实时速度"区域以折线图展示。日志查看功能已移除，任务相关日志在 Tasks 页面的任务详情抽屉中查看。

---

## 6. 任务创建表单交互流程

### 6.1 通用创建流程

所有任务类型遵循“**填写 → 预览/统计 → 确认 → 创建**”四步流程：

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  填写   │ -> │  统计   │ -> │  确认   │ -> │  创建   │
│  表单   │    │  估算   │    │  弹窗   │    │  任务   │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
    │               │               │               │
    │               │               │               │
    ▼               ▼               ▼               ▼
 输入源/目标    调用 estimate    资源保护判断    POST /api/tasks
 消息范围       获取大小/时间    5GB告警/10GB禁止  进入任务队列
 类型过滤
```

### 6.2 下载任务表单

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 任务名称 | text | 否 | 用户自定义，默认生成 |
| 源对话 | text + 按钮 | 是 | 支持 `username` / `chat_id` / `t.me` 链接；右侧 **[解析]** 按钮调用 `GET /api/chats/resolve` |
| 消息范围 | 选择器 | 是 | 日期 / ID / 多 ID 链接 / 全部 / 最近 N 条 |
| 媒体类型过滤 | checkbox | 否 | 视频 / 图片 / 文档 / 音频等 |
| 文件大小范围 | 数字 + 单位选择 | 否 | 最小值、最大值，单位可选 **MB / GB**；提交时转换为字节 |
| 仓库备份 | checkbox | 否 | 是否备份到仓库频道；默认继承 `repository.auto_backup_downloads` |
| 保存路径 | text | 否 | 默认使用配置中的下载目录 |

**源对话解析信息卡片**：解析成功后展示 `chat_name`、`chat_type`、`message_count`、`media_count`、`has_access`。

### 6.3 转发任务表单

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 任务名称 | text | 否 | 用户自定义 |
| 源对话 | text + 按钮 | 是 | 支持 `username` / `chat_id` / `t.me` 链接；带解析按钮与信息卡片 |
| 目标对话 | text + 按钮 | 是 | 与源对话同样的解析能力 |
| 消息范围 | 选择器 | 是 | 同上 |
| 媒体类型过滤 | checkbox | 否 | 同上 |
| 文件大小范围 | 数字 + 单位选择 | 否 | 最小值、最大值，单位可选 **MB / GB** |
| 分发方式 | select | 是 | `copy_message`（默认）/ `file_id_send` / `upload` |
| 启用去重 | checkbox | 否 | 基于仓库文件索引跳过已分发文件 |
| 去重状态 | 只读指示器 | 否 | 显示去重后实际传输数量（如“去重后 45 / 原始 120 条”） |
| 上传后删除本地文件 | checkbox | 否 | 默认勾选 |

### 6.4 上传任务表单

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 任务名称 | text | 否 | 用户自定义 |
| 目标频道 | text | 是 | 目标频道 |
| 文件列表 | 文件选择 | 是 | 从 Files 页面带入或本地上传 |
| 发送为媒体组 | checkbox | 否 | 默认勾选，按 10 个一组拆分 |
| 上传后删除本地文件 | checkbox | 否 | 默认不勾选 |

### 6.5 监听任务表单

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 任务类型 | radio | 是 | `listen_download` / `listen_forward` |
| 源对话 | text + 按钮 | 是 | 支持 `username` / `chat_id` / `t.me` 链接；带解析按钮与信息卡片 |
| 目标对话 | text + 按钮 | 监听转发时必填 | 与源对话同样的解析能力 |
| 媒体类型过滤 | checkbox | 否 | 视频 / 图片 / 文档 / 音频等 |
| 仓库备份 | checkbox | 否 | 监听下载任务显示；默认继承全局配置 |

**说明**：

- 监听任务创建后进入 `running` 状态，长期运行；不会进入 `completed` 状态。
- 同一 `chat_id` + 任务类型（`listen_download` 或 `listen_forward`）已存在运行中/待启动任务时，后端返回 `409 LISTEN_ALREADY_EXISTS`。
- 新消息到达后动态生成 `TaskItem`，通过任务列表轮询即可观察处理进度。

### 6.6 表单校验规则

| 校验项 | 规则 |
|--------|------|
| 源/目标标识符 | 支持 `https://t.me/xxx`、`t.me/xxx`、`@username`、纯 username、纯数字 chat_id |
| 解析按钮防抖 | 点击后至少 3 秒内不可再次点击 |
| 消息 ID 范围 | 最小 ID ≤ 最大 ID，且均为正整数 |
| 日期范围 | 结束日期 ≥ 开始日期 |
| 多 ID/链接 | 每行一条，去重后至少一条有效 |
| 最近 N 条 | `N` 为整数，`1 ≤ N ≤ 1000` |
| 文件大小范围 | 最小值 ≤ 最大值；单位 MB/GB，提交前转换为字节 |
| 文件选择 | 至少选择一个文件，总大小不超过当前磁盘剩余 - `min_disk_space_gb` |
| 监听任务冲突 | 前端不预检，依赖后端 `409` 响应提示 |

### 6.7 创建后的行为

- 创建成功后关闭弹窗，自动跳转到 Tasks 页面并滚动到新任务。
- 如果后端返回资源不足（磁盘空间不够），弹窗提示用户清理磁盘。
- 如果任务进入 `queued` 状态，列表中显示“排队中”与当前队列位置。
- 监听任务创建成功后直接进入“监听中”，新消息到达时任务进度与日志自动更新。

---

## 7. 消息范围选择器交互

### 7.1 选择模式

所有批量下载/转发任务共享同一消息范围选择器组件，支持五种互斥模式：

| 模式 | UI 组件 | 输出数据结构 |
|------|---------|-------------|
| **日期范围** | 开始日期 + 结束日期选择器 | `{mode: 'date_range', start_date: '2026-06-01', end_date: '2026-06-18'}` |
| **消息 ID 范围** | 最小 ID + 最大 ID 输入框 | `{mode: 'id_range', min_id: 100, max_id: 500}` |
| **多个 ID / 链接** | 多行文本域 | `{mode: 'id_list', items: ['100', '150', 'https://t.me/ch/200']}` |
| **全部消息** | 单选 + 二次确认 | `{mode: 'all'}` |
| **最近 N 条** | 单选 + 数字输入框 | `{mode: 'recent', recent_count: 10}` |

### 7.2 组件 UI 示例

```html
<div x-data="messageRangeSelector()" class="space-y-4">
    <div class="flex flex-wrap gap-4">
        <label><input type="radio" x-model="mode" value="date_range"> 日期范围</label>
        <label><input type="radio" x-model="mode" value="id_range"> 消息 ID 范围</label>
        <label><input type="radio" x-model="mode" value="id_list"> 多个 ID / 链接</label>
        <label><input type="radio" x-model="mode" value="all"> 全部消息</label>
        <label><input type="radio" x-model="mode" value="recent"> 最近 N 条</label>
    </div>

    <div x-show="mode === 'date_range'" class="flex gap-4">
        <input type="date" x-model="startDate">
        <span>至</span>
        <input type="date" x-model="endDate">
    </div>

    <div x-show="mode === 'id_range'" class="flex gap-4">
        <input type="number" x-model="minId" placeholder="最小消息 ID">
        <span>至</span>
        <input type="number" x-model="maxId" placeholder="最大消息 ID">
    </div>

    <div x-show="mode === 'id_list'">
        <textarea x-model="rawItems" rows="6" placeholder="每行输入一个消息 ID 或链接"></textarea>
        <p x-text="`已解析 ${parsedCount} 条`"></p>
    </div>

    <div x-show="mode === 'recent'" class="flex items-center gap-4">
        <span>最近</span>
        <input type="number" x-model="recentCount" min="1" max="1000" placeholder="N">
        <span>条消息</span>
        <p class="text-sm text-gray-500">取值范围 1 - 1000，超过 1000 后端会自动截断</p>
    </div>

    <div x-show="mode === 'all'" class="p-4 bg-yellow-50 text-yellow-800 rounded">
        ⚠️ 将处理频道历史所有消息，可能耗时较长。统计时采用抽样估算。
    </div>
</div>
```

### 7.3 交互细节

- **模式切换时保留输入**：用户切换模式后，之前填写的内容保留，避免误清空。
- **多 ID/链接解析**：前端实时解析文本域内容，高亮无效行，显示有效数量。
- **全部消息二次确认**：选择“全部消息”后，点击“统计”按钮前先弹出确认对话框。
- **最近 N 条校验**：前端校验 `1 ≤ recentCount ≤ 1000`；`N ≤ 0` 时给出错误提示，不可提交。
- **联动统计**：选择模式并填写参数后，点击“统计”调用 `/api/chats/{chat_id}/messages/estimate` 获取消息数与总大小。

### 7.4 解析规则

| 输入 | 解析结果 |
|------|---------|
| `100` | 消息 ID 100 |
| `https://t.me/channel/200` | 消息 ID 200，所属频道 `channel` |
| `https://t.me/c/1234567890/300` | 消息 ID 300，私有频道 ID `1234567890` |
| 空行/非法字符 | 过滤并提示 |

### 7.5 与后端交互

- 前端仅做格式校验，实际消息存在性、可访问性由后端在统计/执行阶段验证。
- “全部消息”模式下，后端采用头尾各 10 条样本进行估算，避免遍历全部消息。
- “最近 N 条”模式下，后端按时间倒序取最多 1000 条；若 `recent_count > 1000`，自动截断至 1000 并记录 warning 日志。

---

## 8. 资源保护提示交互

### 8.1 阈值行为

任务创建前，后端返回统计结果，前端根据结果展示不同提示：

> **去重感知计数**：当转发任务启用去重时，资源保护阈值判断基于**去重后的实际传输量**，而非原始消息总数。前端在统计结果中同时展示原始消息数与去重后传输数，让用户明确实际资源消耗。
>
> **过滤感知统计**：媒体类型过滤与文件大小范围会作为参数一并传入 `estimate` 接口，统计结果已反映过滤后的数据量。

| 任务总量 | 前端行为 |
|---------|---------|
| **< 5GB** | 直接显示统计结果，用户点击“确认创建”后提交 |
| **5GB - 10GB** | 弹窗告警，展示总大小与预估时间，要求二次确认 |
| **> 10GB** | 弹窗禁止创建，提供缩小范围建议 |

### 8.2 告警弹窗设计

```html
<div x-show="showWarning" class="fixed inset-0 bg-black/50 flex items-center justify-center">
    <div class="bg-white rounded-lg shadow-lg w-full max-w-md p-6">
        <h3 class="text-lg font-bold text-yellow-600">⚠️ 资源告警</h3>
        <div class="mt-4 space-y-2">
            <p>消息总数：<span x-text="estimate.message_count"></span> 条</p>
            <p>总大小：<span x-text="estimate.total_size"></span></p>
            <p>预估时间：<span x-text="estimate.eta"></span></p>
        </div>
        <p class="mt-4 text-sm text-gray-600">
            任务总量超过 5GB，请确认服务器磁盘空间充足且了解带宽消耗。
        </p>
        <div class="mt-6 flex justify-end gap-3">
            <button @click="showWarning = false">返回修改</button>
            <button @click="confirmCreate()" class="bg-yellow-500 text-white">确认创建</button>
        </div>
    </div>
</div>
```

### 8.3 禁止弹窗设计

与告警弹窗类似，但按钮仅保留“返回修改”，标题与提示为红色，并附带缩小范围建议：

- 缩小消息 ID 范围
- 缩小日期范围
- 使用类型过滤（如只选择视频或图片）
- 拆分多个小任务

### 8.4 磁盘空间不足提示

当 `/api/resource/status` 返回 `disk_free < min_disk_space_gb` 时：

- 在 Dashboard 顶部显示持久化警告条。
- 在 Tasks 页面禁用“新建任务”按钮，点击后提示清理磁盘。
- 弹窗展示磁盘总空间、已用空间、剩余空间。

### 8.5 前端状态联动

```javascript
// 创建任务前的资源判断
async function handleCreate() {
    const estimate = await api.estimateMessages(this.chatId, {
        ...this.rangePayload,
        media_types: this.mediaTypes,
        min_size: this.minSizeBytes,
        max_size: this.maxSizeBytes,
    });
    this.estimate = estimate;

    if (estimate.total_size_gb > TASK_SIZE_MAX_GB) {
        this.showForbidden = true;
    } else if (estimate.total_size_gb > TASK_SIZE_WARNING_GB) {
        this.showWarning = true;
    } else {
        await this.submitTask();
    }
}
```

---

## 9. Token 处理与错误页面

### 9.1 Token 获取与初始化

1. 用户通过 Bot `/web` 命令获得链接：`http://host:port/?token=xxx`。
2. 前端 `index.html` 加载时，从 URL 解析 `token` 并存入 `api.token`。
3. 首次调用 `/api/config`（或任意 API）验证 Token。
4. 后端验证成功后，通过 `Set-Cookie` 下发 `HttpOnly Cookie: trmd_token`。
5. 后续页面刷新时，若 URL 无 token，则依赖 Cookie 中的 Token。

### 9.2 Token 失效处理

| 失效场景 | 前端行为 |
|---------|---------|
| URL Token 无效 | 直接跳转 `/error?code=401` |
| API / 轮询请求返回 401 | 跳转 `/error?code=401` |
| 连续多次轮询因认证失败 | 页面顶部显示离线/认证失败提示，引导重新获取链接 |
| Token 被 Bot `/web_revoke` 撤销 | 后续 API 返回 403，跳转 `/error?code=403` |

### 9.3 错误页面设计

统一错误页 `/error.html` 根据 `code` 参数展示不同内容：

| 错误码 | 标题 | 说明 | 建议操作 |
|--------|------|------|---------|
| 401 | Token 已过期 | 当前访问链接已失效 | 请在 Bot 中重新发送 `/web` 获取新链接 |
| 403 | Token 已撤销 | 管理员已撤销该 Token | 请重新发送 `/web` |
| 404 | 页面不存在 | 访问的页面未找到 | 返回 Dashboard |
| 500 | 服务器内部错误 | 后端发生异常 | 查看 Dashboard 页面日志或重启服务 |
| 502 | 服务未启动 | 无法连接到后端 | 检查服务是否运行 |
| 503 | 资源不足 | 磁盘/内存不满足要求 | 清理磁盘空间或降低并发配置 |

### 9.4 Token 安全

- 前端 JS 不持久化 Token 到 `localStorage`，仅保存在内存的 `api.token` 中。
- 使用 HttpOnly Cookie 降低 XSS 风险。
- URL 中的 Token 在首次验证成功后，前端可调用 `history.replaceState` 清理 URL 中的 `token` 参数，避免链接被复制泄露。

---

## 10. TDD 测试策略

### 10.1 测试范围

由于 WebUI 是无构建步骤的纯前端项目，测试分为两个层次：

| 层次 | 范围 | 工具建议 |
|------|------|---------|
| **单元测试** | Alpine.js 组件逻辑、表单校验、消息范围解析、资源判断逻辑 | [Vitest](https://vitest.dev/) + [happy-dom](https://github.com/capricorn86/happy-dom)（零配置，不依赖浏览器） |
| **端到端测试** | 页面跳转、任务创建流程、REST 轮询实时更新、Token 失效跳转 | [Playwright](https://playwright.dev/) |

### 10.2 单元测试重点

1. **消息范围解析器**
   - 日期范围：开始 ≤ 结束。
   - ID 范围：最小 ≤ 最大，均为正整数。
   - 多 ID/链接：正确提取消息 ID 与频道标识，过滤空行与非法输入。
   - 全部消息：返回 `{mode: 'all'}`。
   - 最近 N 条：`1 ≤ N ≤ 1000`；`N ≤ 0` 或超过上限时给出明确错误。

2. **标识符解析与校验**
   - 支持 `https://t.me/xxx`、`t.me/xxx`、`@username`、纯 username、纯数字 chat_id。
   - 解析按钮防抖：3 秒内重复点击仅触发一次。
   - 解析结果卡片正确渲染 ResolvedChat 字段。

3. **文件大小过滤**
   - MB / GB 单位与字节转换正确。
   - 最小值 ≤ 最大值。
   - 边界值（0、空值、极大值）处理。

4. **资源保护判断函数**
   - `< 5GB`：返回 `allow`。
   - `5GB - 10GB`：返回 `warning`。
   - `> 10GB`：返回 `forbidden`。
   - 磁盘剩余不足 `min_disk_space_gb`：返回 `disk_insufficient`。

5. **API 错误处理**
   - 401 跳转错误页。
   - 5xx 触发全局通知。
   - 网络异常触发重试。

6. **表单校验**
   - 必填项缺失。
   - 源/目标标识符格式。
   - 监听任务类型切换时目标输入框显隐。
   - 仓库备份开关默认值继承逻辑。
   - 文件选择非空。

### 10.3 端到端测试重点

1. **Token 认证流程**
   - 携带有效 Token 访问首页，成功加载。
   - 携带无效 Token 访问首页，跳转 401 错误页。

2. **Dashboard 加载**
   - 页面加载 < 3s。
   - 资源卡片、最近任务正确显示。

3. **源/目标标识符解析**
   - 输入 username / chat_id / t.me 链接 → 点击解析 → 展示对话信息卡片。
   - 解析按钮 3 秒防抖生效。

4. **创建下载任务（含 recent 模式）**
   - 选择“最近 N 条”，填写 N → 统计 → 创建 → 任务出现在列表中。

5. **创建转发任务（含大小过滤）**
   - 配置源/目标、媒体类型、大小范围 → 统计 → 创建。

6. **创建监听任务**
   - 选择监听下载 / 监听转发 → 解析源对话 → 创建 → 任务进入“监听中”。
   - 同一 chat_id 重复创建监听任务时后端返回 409，前端展示冲突提示。

7. **REST 轮询实时更新**
   - 创建任务后，任务列表与进度条随轮询自动更新。
   - Dashboard 页面监控统计数据随轮询自动更新。

8. **资源保护提示**
   - 构造大任务触发 5GB 告警弹窗。
   - 构造超大任务触发 10GB 禁止弹窗。

### 10.4 覆盖率目标

| 模块 | 目标覆盖率 |
|------|-----------|
| `static/js/utils.js`（解析器/校验器） | ≥ 90% |
| `static/js/api.js` | ≥ 80% |
| `static/js/polling.js` | ≥ 70% |
| 页面级 `x-data` 逻辑 | ≥ 60%（E2E 覆盖） |

### 10.5 持续集成建议

- 在 `pyproject.toml` 中新增可选测试依赖 `pytest-playwright`。
- 编写 `tests/webui/` 目录存放前端单元与 E2E 测试。
- CI 中先运行后端服务，再运行 Playwright 测试。

---

## 11. 依赖关系

### 11.1 前端依赖

所有依赖均通过 CDN 或本地静态文件引入，无 npm 构建：

| 依赖 | 版本建议 | 用途 | 引入方式 |
|------|---------|------|---------|
| Alpine.js | v3.x | 响应式状态与交互 | CDN / 本地 |
| Tailwind CSS | v3.x | 原子化样式 | CDN / 本地 |
| Chart.js 或 ApexCharts | v3.x | Dashboard 图表 | CDN / 本地 |
| Font Awesome / Heroicons | - | 图标 | CDN / 本地 |

### 11.2 后端依赖

WebUI 依赖后端 FastAPI 提供 REST API：

| 依赖 | 用途 |
|------|------|
| FastAPI | REST API 服务 |
| uvicorn | ASGI 服务器 |
| python-multipart | 文件上传解析 |
| jinja2 | HTML 模板渲染（可选，若使用纯静态文件则不需要） |

### 11.3 与现有模块的集成

| 后端模块 | WebUI 使用方式 |
|---------|---------------|
| `TokenManager` | 认证中间件校验 Token |
| `IdentifierService` | `GET /api/chats/resolve` 解析源/目标对话 |
| `TaskManager` | `/api/tasks/*` 路由调用 |
| `FileManager` | `/api/files/*` 路由调用 |
| `ConfigManager` | `/api/config` 路由调用 |
| `Monitor` | `GET /api/monitor/stats` 轮询（监控数据已合并至 Dashboard 展示）、`GET /api/logs` 轮询 |

---

## 12. 风险与假设

### 12.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| **Token 链接泄露** | 拿到链接的人可在 1 小时内访问 WebUI | Token 有效期短、支持 `/web_revoke` 撤销、使用 HttpOnly Cookie |
| **大任务统计慢** | “全部消息”模式可能 still 耗时 | 后端采用抽样估算，前端提供取消按钮 |
| **轮询延迟** | 任务状态/日志更新存在秒级延迟 | 合理设置轮询间隔（任务 3s / 日志 2s / 监控 5s） |
| **标识符解析触发 FloodWait** | 频繁调用 `get_chat` 导致 Telegram 限流 | 解析按钮 3 秒防抖；错误提示引导用户稍后重试 |
| **前端无构建导致测试工具有限** | 无法使用现代前端测试生态的全部特性 | 使用 Vitest + happy-dom 做单元测试，Playwright 做 E2E |
| **单用户下无多页面隔离** | 同一用户多个浏览器标签可能操作冲突 | 状态由后端统一管理，前端只作为视图；并发限制由后端控制 |
| **页面加载超时** | 单页应用首次加载需拉取多个静态资源 | 资源本地化、开启 gzip/ brotli、按需加载 Alpine 组件 |

### 12.2 假设

| 假设 | 说明 |
|------|------|
| **单用户** | 无需登录页、无需多用户权限隔离 |
| **Bot 与 WebUI 同时运行** | `/web` 命令生成链接时，FastAPI 服务已启动 |
| **浏览器支持现代 Web 标准** | 支持 Fetch、CSS Grid/Flexbox、ES6+ |
| **后端 API 按主设计文档实现** | 本模块设计基于 `/api/*` 接口契约 |
| **无 HTTPS 要求** | 本地/内网使用 HTTP，若需 HTTPS 由反向代理处理 |

---

## 附录 A：前端文件结构建议

```
module/web/                      # WebUI 前端目录
├── static/
│   ├── css/
│   │   └── tailwind.css         # Tailwind  CDN 或本地构建后的样式
│   ├── js/
│   │   ├── app.js               # Alpine 全局 store、路由初始化
│   │   ├── api.js               # REST API 封装
│   │   ├── polling.js           # REST 轮询封装
│   │   ├── utils.js             # 消息范围解析、标识符校验、表单校验、资源判断
│   │   ├── components/
│   │   │   ├── messageRangeSelector.js
│   │   │   ├── chatResolver.js  # 源/目标对话解析与信息卡片
│   │   │   ├── taskList.js
│   │   │   ├── fileTree.js
│   │   │   └── resourceAlert.js
│   │   └── pages/
│   │       ├── dashboard.js
│   │       ├── tasks.js
│   │       ├── files.js
│   │       ├── repository.js
│   │       ├── settings.js
│   │       └── (monitor.js 已废弃，监控功能合并至 dashboard.js)
│   └── img/
│       └── logo.png
├── templates/
│   ├── index.html               # 主 SPA 页面
│   └── error.html               # 错误页
└── __init__.py
```

## 附录 B：关键配置项映射

| 后端配置键 | WebUI 用途 |
|-----------|-----------|
| `resource_limits.max_concurrent_tasks` | 任务队列显示与并发控制 |
| `resource_limits.task_size_warning_gb` | 5GB 告警阈值 |
| `resource_limits.task_size_max_gb` | 10GB 禁止阈值 |
| `resource_limits.min_disk_space_gb` | 磁盘不足判断 |
| `download_type` | 下载/转发任务类型过滤 |
| `max_download_task` | 下载并发数 |
| `temp` | 临时目录与上传任务根目录 |
| `repository.enabled` | 仓库启用开关，控制 Repository 页面可用性 |
| `repository.chat_id` | 仓库频道 Chat ID |
| `repository.auto_backup_downloads` | 下载/监听下载任务默认仓库备份行为；影响下载/监听下载表单的"备份到仓库频道" checkbox 默认勾选状态 |
| `repository.repository_channel` | 仓库频道标识符（username/chat_id），用于任务级备份 |
| `repository.auto_sync` | 自动同步开关 |
| `repository.sync_interval` | 同步间隔（秒） |
| `preference.upload.delete` | 上传后删除本地文件偏好 |

---

> **文档结束**

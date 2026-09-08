/**
 * 任务管理逻辑模块
 *
 * 处理任务列表、创建、操作（启动/取消/重试/删除）
 * 通过定时轮询获取最新任务状态
 */

class TaskManager {
  constructor() {
    this.tasks = [];
    this.loading = false;
    this.error = null;
    this.filter = "all"; // all, pending, running, completed, failed, cancelled
    this.typeFilter = "all"; // all, download, forward, upload
    this.selectedTask = null;
    this.selectedTasks = []; // 批量选择的任务 ID 列表（H2）
    this.showDetailDrawer = false;
    this.showCreateModal = false;
    this._pollTimer = null;

    // 智能轮询相关属性
    this._smartPollTimer = null;
    this._smartPollInterval = 10000; // 10秒间隔
    this.lastSyncTime = null;
    this._consecutiveErrors = 0; // 连续错误计数
    this._maxConsecutiveErrors = 3; // 最大连续错误次数

    // 分页
    this.page = 1;
    this.pageSize = 20;
    this.totalTasks = 0;
    this.totalPages = 0;

    // 创建任务表单
    this.createForm = this._resetCreateForm();

    // 资源告警状态（P0-2: 前端资源保护增强）
    this.showResourceAlert = false;
    this.resourceAlertType = null; // 'blocked' | 'warning'
    this.resourceAlertMessage = "";
    this.resourceSuggestion = "";
    this.resourceEstimate = null;
  }

  /**
   * 重置创建表单
   */
  _resetCreateForm() {
    return {
      taskType: "download", // download, forward, listen_download, listen_forward, cleanup_files
      sourceChat: "",
      targetChat: "",
      messageRangeMode: "id_range", // date_range, id_range, multiple_ids, all, recent
      startDate: "",
      endDate: "",
      minId: "",
      maxId: "",
      rawItems: "",
      recentCount: "",
      typeFilters: [], // video, photo, document, audio, etc.
      minSize: "",
      maxSize: "",
      minSizeUnit: "MB",
      maxSizeUnit: "MB",
      // 定时清理（cleanup_files）专用字段
      keepDaysPreset: "7", // 1日/3日/7日/30日/自定义
      keepDays: 7, // 自定义保留天数 1~365
      scheduleMode: "daily", // daily | interval
      scheduleTime: "03:00", // daily 模式时刻
      scheduleIntervalHours: 24, // interval 模式间隔（小时 1~72）
      removeEmptyDirs: true, // 是否清理空目录
    };
  }

  /**
   * 加载任务列表
   * @param {boolean} resetPage - 是否重置到第一页
   */
  async loadTasks(resetPage = false) {
    this.loading = true;
    this.error = null;

    if (resetPage) {
      this.page = 1;
    }

    try {
      const offset = (this.page - 1) * this.pageSize;
      const params = {
        offset: offset,
        limit: this.pageSize,
      };

      if (this.filter !== "all") {
        params.status = this.filter;
      }
      if (this.typeFilter !== "all") {
        params.task_type = this.typeFilter;
      }

      const response = await api.getTasks(params);

      // 获取 Alpine 组件数据
      const alpineRoot = document.querySelector("[x-data]");
      const alpineData = alpineRoot && window.Alpine ? window.Alpine.$data(alpineRoot) : null;

      // 如果 Alpine 组件正在导航（翻页/筛选），跳过数据更新以避免竞态覆盖
      if (alpineData && alpineData._navigating) {
        console.log("[loadTasks] 导航中，跳过数据更新");
        return;
      }

      this.tasks = response.items || [];
      this.totalTasks = response.total || 0;
      this.totalPages = Math.ceil(this.totalTasks / this.pageSize) || 1;

      // 同步到 Alpine 组件的响应式数据（解决智能轮询时视图不更新的问题）
      if (alpineData) {
        alpineData.tasks = this.tasks;
        if (typeof alpineData._syncPagination === "function") {
          alpineData._syncPagination();
        }
      }

      // 重置连续错误计数
      this._consecutiveErrors = 0;

      // 更新最后同步时间
      this.lastSyncTime = new Date();
    } catch (error) {
      this.error = error.message;
      console.error("加载任务列表失败:", error);

      // 增加连续错误计数
      this._consecutiveErrors++;

      // 连续错误达到阈值，停止智能轮询
      if (this._consecutiveErrors >= this._maxConsecutiveErrors) {
        console.warn(
          `连续${this._maxConsecutiveErrors}次请求失败，停止自动刷新`,
        );
        this.stopSmartPolling();
      }
    } finally {
      this.loading = false;
    }
  }

  /**
   * 创建新任务（P0-2: 增加资源预检和告警）
   */
  async createTask() {
    try {
      const payload = this._buildCreatePayload();

      // 【P0-2】下载/转发任务需要先估算大小并进行资源保护检查
      if (["download", "forward"].includes(payload.task_type)) {
        const precheckResult = await this._precheckResourceLimits(payload);

        if (precheckResult.blocked) {
          // 显示禁止弹窗
          this._showResourceAlert("blocked", precheckResult);
          return; // 阻止创建
        }

        if (precheckResult.warning) {
          // 显示告警弹窗并等待用户确认
          const confirmed = await this._showWarningConfirmation(precheckResult);
          if (!confirmed) {
            return; // 用户取消
          }
        }

        // 将估算结果附加到 payload（供后端二次校验）
        if (precheckResult.estimate) {
          payload.params.estimated_size =
            precheckResult.estimate.total_size_bytes || 0;
          payload.params.size_human =
            precheckResult.estimate.total_size_human || "";
        }
      }

      const task = await api.createTask(payload);

      // 关闭弹窗，重置表单
      this.showCreateModal = false;
      this.createForm = this._resetCreateForm();

      // 重新加载任务列表
      await this.loadTasks(true);

      // 成功通知交给调用方（handleCreateTask）统一处理，避免重复提示
      return task;
    } catch (error) {
      // 处理后端返回的 TaskSizeExceeded / TaskSizeWarning 异常
      const errorMsg = error.message || "";
      if (
        errorMsg.includes("超出限制") ||
        errorMsg.includes("超过") ||
        errorMsg.includes("InsufficientDiskSpace") ||
        errorMsg.includes("TaskSizeExceeded")
      ) {
        this._notify("error", `无法创建任务: ${errorMsg}`);
        return;
      }

      this._notify("error", `创建任务失败: ${errorMsg}`);
      throw error;
    }
  }

  /**
   * 构建创建任务的请求体
   */
  _buildCreatePayload() {
    const params = {};

    // 辅助：将嵌套消息范围展平为后端期望的扁平字段
    const _flattenRange = (range) => {
      params.range_mode = range.mode;
      if (range.mode === "id_range") {
        params.min_id = range.min_id;
        params.max_id = range.max_id;
      } else if (range.mode === "date_range") {
        params.start_date = range.start_date;
        params.end_date = range.end_date;
      } else if (range.mode === "multiple_ids") {
        params.message_list = range.message_list;
      } else if (range.mode === "recent") {
        params.recent_count = range.recent_count;
      }
    };

    // 辅助：添加媒体大小过滤
    const _addSizeFilter = () => {
      const minBytes = this._convertSizeToBytes(
        this.createForm.minSize,
        this.createForm.minSizeUnit,
      );
      const maxBytes = this._convertSizeToBytes(
        this.createForm.maxSize,
        this.createForm.maxSizeUnit,
      );
      if (minBytes !== null) params.min_size = minBytes;
      if (maxBytes !== null) params.max_size = maxBytes;
    };

    if (this.createForm.taskType === "download") {
      // sourceChat 可能是用户输入的字符串标识符，也可能是解析后的数字 ID
      if (typeof this.createForm.sourceChat === "number") {
        params.chat_id = this.createForm.sourceChat;
      } else {
        params.source_identifier = this.createForm.sourceChat;
      }
      _flattenRange(this._buildMessageRange());
      // 类型过滤（仅下载/转发任务有意义）
      if (this.createForm.typeFilters.length > 0) {
        params.filter_types = this.createForm.typeFilters;
      }
      _addSizeFilter();
    } else if (this.createForm.taskType === "forward") {
      if (typeof this.createForm.sourceChat === "number") {
        params.chat_id = this.createForm.sourceChat;
      } else {
        params.source_identifier = this.createForm.sourceChat;
      }
      params.forward_target = this.createForm.targetChat;
      _flattenRange(this._buildMessageRange());
      // 类型过滤
      if (this.createForm.typeFilters.length > 0) {
        params.filter_types = this.createForm.typeFilters;
      }
      _addSizeFilter();
    } else if (this.createForm.taskType === "listen_download") {
      if (typeof this.createForm.sourceChat === "number") {
        params.chat_id = this.createForm.sourceChat;
      } else {
        params.source_identifier = this.createForm.sourceChat;
      }
      if (this.createForm.typeFilters.length > 0) {
        params.media_types = this.createForm.typeFilters;
      }
    } else if (this.createForm.taskType === "listen_forward") {
      if (typeof this.createForm.sourceChat === "number") {
        params.chat_id = this.createForm.sourceChat;
      } else {
        params.source_identifier = this.createForm.sourceChat;
      }
      params.target_identifier = this.createForm.targetChat;
      if (this.createForm.typeFilters.length > 0) {
        params.media_types = this.createForm.typeFilters;
      }
    } else if (this.createForm.taskType === "cleanup_files") {
      params.keep_days = this._resolveKeepDays();
      params.schedule = this.createForm.scheduleMode === "daily"
        ? { mode: "daily", time: this.createForm.scheduleTime }
        : { mode: "interval", interval_hours: parseInt(this.createForm.scheduleIntervalHours) };
      params.remove_empty_dirs = this.createForm.removeEmptyDirs;
    }

    return {
      task_type: this.createForm.taskType,
      params,
    };
  }

  /**
   * 解析最终保留天数：preset 为自定义时取 keepDays，否则取预设值
   * @returns {number} 保留天数 1~365
   */
  _resolveKeepDays() {
    if (this.createForm.keepDaysPreset === "custom") {
      return parseInt(this.createForm.keepDays);
    }
    return parseInt(this.createForm.keepDaysPreset);
  }

  // ==================== P0-2: 资源预检方法 ====================

  /**
   * 预检任务资源限制
   * @param {Object} payload - 创建任务的 payload
   * @returns {Promise<Object>} { blocked, warning, estimate, message }
   */
  async _precheckResourceLimits(payload) {
    try {
      // 支持 chat_id 和 source_identifier 两种源端标识
      const chatId = payload.params.chat_id || payload.params.source_identifier;
      if (!chatId) {
        return { blocked: false, warning: false, message: "缺少频道信息" };
      }

      // 调用后端估算 API
      const rangeParams = this._extractRangeParams(payload);
      const estimate = await api.estimateMessages(chatId, rangeParams);

      // 资源保护检查
      return this._checkSizeThresholds(estimate);
    } catch (error) {
      console.warn("资源预检失败，跳过预检:", error);
      // 预检失败不阻止创建，由后端兜底
      return { blocked: false, warning: false, estimate: null };
    }
  }

  /**
   * 从 payload 提取消息范围参数
   * @param {Object} payload - 创建任务的 payload
   * @returns {Object} 范围参数
   */
  _extractRangeParams(payload) {
    const params = payload.params || {};

    return {
      range_mode: params.range_mode || "id_range",
      min_id: params.min_id,
      max_id: params.max_id,
      start_date: params.start_date,
      end_date: params.end_date,
      message_list: params.message_list,
      recent_count: params.recent_count,
      type_filters: this.createForm.typeFilters || [],
    };
  }

  /**
   * 检查任务大小是否超过阈值
   * @param {Object} estimate - 估算结果
   * @returns {Object} 检查结果
   */
  _checkSizeThresholds(estimate) {
    if (!estimate || !estimate.total_size_bytes) {
      return { blocked: false, warning: false, estimate, message: "" };
    }

    const sizeGB = estimate.total_size_bytes / 1024 ** 3;
    const warningThreshold = 5; // GB - 告警阈值
    const maxThreshold = 10; // GB - 禁止阈值

    if (sizeGB > maxThreshold) {
      return {
        blocked: true,
        warning: false,
        estimate,
        message: `任务总量 ${sizeGB.toFixed(1)} GB 超过 ${maxThreshold} GB 上限`,
        suggestion: "建议：缩小消息 ID 范围、缩小日期范围或使用类型过滤",
      };
    }

    if (sizeGB > warningThreshold) {
      return {
        blocked: false,
        warning: true,
        estimate,
        message: `任务总量 ${sizeGB.toFixed(1)} GB 超过 ${warningThreshold} GB 告警阈值`,
        suggestion: "确认你的服务器磁盘有足够空间且知晓该任务可能消耗较多带宽",
      };
    }

    return { blocked: false, warning: false, estimate, message: "" };
  }

  /**
   * 显示资源告警/禁止弹窗
   * @param {string} type - 'blocked' | 'warning'
   * @param {Object} result - 检查结果
   */
  _showResourceAlert(type, result) {
    this.resourceAlertType = type;
    this.resourceAlertMessage = result.message;
    this.resourceSuggestion = result.suggestion || "";
    this.resourceEstimate = result.estimate || null;
    this.showResourceAlert = true;
  }

  /**
   * 显示告警确认对话框
   * @param {Object} result - 检查结果
   * @returns {Promise<boolean>} 用户是否确认
   */
  async _showWarningConfirmation(result) {
    // ✅ 使用自定义 ConfirmDialog 组件（P2-3）
    const message = `
      <p class="font-medium mb-2">${result.message}</p>
      <div class="bg-gray-50 border border-gray-200 rounded p-3 my-2 text-xs space-y-1">
        <p><strong>消息总数:</strong> ${result.estimate?.message_count || "未知"} 条</p>
        <p><strong>预估大小:</strong> ${result.estimate?.total_size_human || "未知"}</p>
        <p><strong>预估耗时:</strong> 约 ${Math.round((result.estimate?.estimated_duration_seconds || 0) / 60)} 分钟</p>
      </div>
      ${result.suggestion ? `<p class="text-red-600 font-medium mt-2">${result.suggestion}</p>` : ""}
    `;

    return window.confirmDialog.show({
      title: "资源告警",
      message: message,
      type: "warning",
      confirmText: "确认创建任务",
      cancelText: "返回修改",
    });
  }

  /**
   * 关闭资源告警弹窗
   */
  closeResourceAlert() {
    this.showResourceAlert = false;
    this.resourceAlertType = null;
    this.resourceAlertMessage = "";
    this.resourceSuggestion = "";
    this.resourceEstimate = null;
  }

  /**
   * 构建消息范围对象
   */
  _buildMessageRange() {
    const mode = this.createForm.messageRangeMode;

    switch (mode) {
      case "date_range":
        return {
          mode: "date_range",
          start_date: this.createForm.startDate,
          end_date: this.createForm.endDate,
        };

      case "id_range":
        return {
          mode: "id_range",
          min_id: parseInt(this.createForm.minId),
          max_id: parseInt(this.createForm.maxId),
        };

      case "multiple_ids":
        const messageList = this.createForm.rawItems
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line.length > 0);
        return {
          mode: "multiple_ids",
          message_list: messageList,
        };

      case "all":
        return { mode: "all" };

      case "recent":
        return {
          mode: "recent",
          recent_count: parseInt(this.createForm.recentCount),
        };

      default:
        return { mode: "all" };
    }
  }

  /**
   * 将文件大小转换为字节
   * @param {string|number} value - 大小值
   * @param {string} unit - 单位（MB/GB）
   * @returns {number|null} 字节数，无效输入返回 null
   */
  _convertSizeToBytes(value, unit) {
    if (!value || value <= 0) return null;
    const multiplier = unit === "GB" ? 1024 * 1024 * 1024 : 1024 * 1024;
    return parseInt(value) * multiplier;
  }

  /**
   * 启动任务
   * @param {string} taskId - 任务 ID
   */
  async startTask(taskId) {
    try {
      await api.startTask(taskId);
      await this.loadTasks();
      // 成功通知由调用方（Alpine 组件层）统一处理，避免重复提示
    } catch (error) {
      this._notify("error", `启动任务失败: ${error.message}`);
    }
  }

  /**
   * 取消任务
   * @param {string} taskId - 任务 ID
   */
  async cancelTask(taskId) {
    try {
      await api.cancelTask(taskId);
      await this.loadTasks();
      // 成功通知由调用方（Alpine 组件层）统一处理，避免重复提示
    } catch (error) {
      this._notify("error", `取消任务失败: ${error.message}`);
    }
  }

  /**
   * 重试任务
   * @param {string} taskId - 任务 ID
   */
  async retryTask(taskId) {
    try {
      await api.retryTask(taskId);
      await this.loadTasks();
      // 成功通知由调用方（Alpine 组件层）统一处理，避免重复提示
    } catch (error) {
      this._notify("error", `重试任务失败: ${error.message}`);
    }
  }

  /**
   * 删除任务
   * @param {string} taskId - 任务 ID
   */
  async deleteTask(taskId) {
    // ✅ 使用自定义 ConfirmDialog 组件（P2-3）
    const confirmed = await window.confirmDialog.show({
      title: "删除任务",
      message:
        '<p>确定要删除此任务吗？</p><p class="text-red-600 text-xs mt-2">此操作不可撤销。</p>',
      type: "danger",
      confirmText: "确认删除",
      cancelText: "取消",
    });

    if (!confirmed) {
      return;
    }

    try {
      await api.deleteTask(taskId);
      await this.loadTasks();
      this._notify("success", "任务已删除");
    } catch (error) {
      this._notify("error", `删除任务失败: ${error.message}`);
    }
  }

  // ==================== H2: 批量选择与批量操作 ====================

  /**
   * 判断任务是否已被选中
   * @param {string} taskId - 任务 ID
   * @returns {boolean}
   */
  isTaskSelected(taskId) {
    return this.selectedTasks.includes(taskId);
  }

  /**
   * 切换任务选中状态
   * @param {string} taskId - 任务 ID
   */
  toggleTaskSelection(taskId) {
    if (this.isTaskSelected(taskId)) {
      this.selectedTasks = this.selectedTasks.filter((id) => id !== taskId);
    } else {
      this.selectedTasks = [...this.selectedTasks, taskId];
    }
    this._syncSelectedToAlpine();
  }

  /**
   * 全选当前页任务
   */
  selectAllTasks() {
    this.selectedTasks = this.tasks.map((task) => task.id);
    this._syncSelectedToAlpine();
  }

  /**
   * 切换全选：若当前页全部已选则清空，否则全选当前页
   */
  toggleSelectAll() {
    if (this.isAllSelected()) {
      this.clearSelection();
    } else {
      this.selectAllTasks();
    }
  }

  /**
   * 当前页任务是否全部选中
   * @returns {boolean}
   */
  isAllSelected() {
    return (
      this.tasks.length > 0 &&
      this.tasks.every((task) => this.isTaskSelected(task.id))
    );
  }

  /**
   * 清空批量选择
   */
  clearSelection() {
    this.selectedTasks = [];
    this._syncSelectedToAlpine();
  }

  /**
   * 将选中状态同步到 Alpine 响应式数据
   */
  _syncSelectedToAlpine() {
    const alpineRoot = document.querySelector("[x-data]");
    const alpineData =
      alpineRoot && window.Alpine ? window.Alpine.$data(alpineRoot) : null;
    if (alpineData) {
      alpineData.selectedTasks = this.selectedTasks;
    }
  }

  /**
   * 批量取消任务
   */
  async batchCancelTasks() {
    if (this.selectedTasks.length === 0) return;

    // ✅ 使用自定义 ConfirmDialog 组件确认（P2-3）
    const confirmed = await window.confirmDialog.show({
      title: "批量取消任务",
      message: `<p>确定要取消选中的 <strong>${this.selectedTasks.length}</strong> 个任务吗？</p>`,
      type: "warning",
      confirmText: "确认取消",
      cancelText: "取消",
    });
    if (!confirmed) return;

    const ids = [...this.selectedTasks];
    let failed = 0;
    for (const id of ids) {
      try {
        await api.cancelTask(id);
      } catch (error) {
        failed++;
        console.error(`取消任务 ${id} 失败:`, error);
      }
    }

    this.clearSelection();
    if (failed > 0) {
      this._notify("error", `批量取消完成，${failed} 个任务取消失败`);
    } else {
      this._notify("success", `已取消 ${ids.length} 个任务`);
    }
    await this.loadTasks(true);
  }

  /**
   * 批量删除任务
   */
  async batchDeleteTasks() {
    if (this.selectedTasks.length === 0) return;

    // ✅ 使用自定义 ConfirmDialog 组件确认（P2-3）
    const confirmed = await window.confirmDialog.show({
      title: "批量删除任务",
      message: `<p>确定要删除选中的 <strong>${this.selectedTasks.length}</strong> 个任务吗？</p><p class="text-red-600 text-xs mt-2">此操作不可撤销。</p>`,
      type: "danger",
      confirmText: "确认删除",
      cancelText: "取消",
    });
    if (!confirmed) return;

    const ids = [...this.selectedTasks];
    let failed = 0;
    for (const id of ids) {
      try {
        await api.deleteTask(id);
      } catch (error) {
        failed++;
        console.error(`删除任务 ${id} 失败:`, error);
      }
    }

    this.clearSelection();
    if (failed > 0) {
      this._notify("error", `批量删除完成，${failed} 个任务删除失败`);
    } else {
      this._notify("success", `已删除 ${ids.length} 个任务`);
    }
    await this.loadTasks(true);
  }

  /**
   * 查看任务详情
   * @param {object} task - 任务对象
   */
  viewTaskDetail(task) {
    this.selectedTask = task;
    this.showDetailDrawer = true;
  }

  /**
   * 立即执行定时清理任务
   * @param {string} taskId - 任务 ID
   */
  async runTask(taskId) {
    try {
      await api.runTask(taskId);
      this._notify("success", "已触发立即清理");
      await this.loadTasks();
    } catch (error) {
      this._notify("error", `立即清理失败: ${error.message}`);
    }
  }

  /**
   * 暂停定时清理任务调度
   * @param {string} taskId - 任务 ID
   */
  async pauseTask(taskId) {
    try {
      await api.pauseTask(taskId);
      this._notify("success", "已暂停定时清理");
      await this.loadTasks();
    } catch (error) {
      this._notify("error", `暂停失败: ${error.message}`);
    }
  }

  /**
   * 恢复定时清理任务调度
   * @param {string} taskId - 任务 ID
   */
  async resumeTask(taskId) {
    try {
      await api.resumeTask(taskId);
      this._notify("success", "已恢复定时清理");
      await this.loadTasks();
    } catch (error) {
      this._notify("error", `恢复失败: ${error.message}`);
    }
  }

  /**
   * 格式化定时清理调度文本
   * @param {object} schedule - 调度参数 { mode, time, interval_hours }
   * @returns {string} 例如 "每天 03:00" / "每隔 24 小时"
   */
  formatScheduleText(schedule) {
    if (!schedule) return "-";
    if (schedule.mode === "daily") {
      return `每天 ${schedule.time || "-"}`;
    }
    if (schedule.mode === "interval") {
      return `每隔 ${schedule.interval_hours ?? "-"} 小时`;
    }
    return "-";
  }

  /**
   * 格式化耗时（秒 → 可读文本，如 "1分30秒"）
   * @param {number} seconds - 秒数
   */
  formatDuration(seconds) {
    if (!seconds && seconds !== 0) return "-";
    if (seconds < 60) return `${seconds} 秒`;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    if (m < 60) return s > 0 ? `${m} 分 ${s} 秒` : `${m} 分钟`;
    const h = Math.floor(m / 60);
    const restM = m % 60;
    return h > 0 && restM > 0 ? `${h} 小时 ${restM} 分钟` : `${h} 小时`;
  }

  /**
   * 根据最近一次清理的起止时间计算耗时
   * @param {object} lastRun - last_run 对象（含 started_at / finished_at）
   * @returns {string} 可读耗时文本，无法计算时返回 "-"
   */
  formatLastRunDuration(lastRun) {
    if (!lastRun || !lastRun.started_at || !lastRun.finished_at) return "-";
    const start = new Date(lastRun.started_at).getTime();
    const end = new Date(lastRun.finished_at).getTime();
    if (isNaN(start) || isNaN(end) || end < start) return "-";
    return this.formatDuration((end - start) / 1000);
  }

  /**
   * 关闭详情抽屉
   */
  closeDetailDrawer() {
    this.showDetailDrawer = false;
    this.selectedTask = null;
  }

  /**
   * 设置过滤条件
   * @param {string} filter - 过滤条件
   */
  setFilter(filter) {
    this.filter = filter;
    this.loadTasks(true);
  }

  /**
   * 设置任务类型过滤条件
   * @param {string} typeFilter - 类型过滤条件
   */
  setTypeFilter(typeFilter) {
    this.typeFilter = typeFilter;
    this.loadTasks(true);
  }

  /**
   * 设置页码
   * @param {number} page - 页码
   */
  setPage(page) {
    if (page < 1 || page > this.totalPages) return;
    this.page = page;
    this.loadTasks();
  }

  /**
   * 解析多行 ID/链接输入
   */
  getParsedItemCount() {
    if (!this.createForm.rawItems) return 0;
    return this.createForm.rawItems
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0).length;
  }

  /**
   * 获取类型过滤选项列表
   */
  getTypeFilterOptions() {
    return [
      { value: "photo", label: "图片", icon: "🖼️" },
      { value: "video", label: "视频", icon: "🎬" },
      { value: "document", label: "文档", icon: "📄" },
      { value: "audio", label: "音频", icon: "🎵" },
      { value: "animation", label: "动画", icon: "🎞️" },
      { value: "voice", label: "语音", icon: "🎤" },
      { value: "video_note", label: "视频笔记", icon: "📹" },
    ];
  }

  /**
   * 检查类型过滤是否选中
   * @param {string} type - 类型值
   * @returns {boolean}
   */
  isTypeFilterSelected(type) {
    return this.createForm.typeFilters.includes(type);
  }

  /**
   * 切换类型过滤选项
   * @param {string} type - 类型值
   */
  toggleTypeFilter(type) {
    const index = this.createForm.typeFilters.indexOf(type);
    if (index === -1) {
      this.createForm.typeFilters = [...this.createForm.typeFilters, type];
    } else {
      this.createForm.typeFilters = this.createForm.typeFilters.filter((t) => t !== type);
    }
  }

  /**
   * 验证创建表单
   */
  validateCreateForm() {
    const form = this.createForm;
    const errors = [];

    // 验证源频道
    if (
      (form.taskType === "download" ||
        form.taskType === "forward" ||
        form.taskType === "listen_download" ||
        form.taskType === "listen_forward") &&
      !form.sourceChat
    ) {
      errors.push("请输入源频道");
    }

    // 验证目标频道
    if (
      (form.taskType === "forward" ||
        form.taskType === "listen_forward") &&
      !form.targetChat
    ) {
      errors.push("请输入目标频道");
    }

    // 验证定时清理参数
    if (form.taskType === "cleanup_files") {
      const keepDays = this._resolveKeepDays();
      if (!Number.isInteger(keepDays) || keepDays < 1 || keepDays > 365) {
        errors.push("保留天数需为 1~365 的整数");
      }
      if (form.scheduleMode === "daily") {
        if (!/^(0?[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$/.test(form.scheduleTime || "")) {
          errors.push("时刻格式不正确（如 03:00）");
        }
      } else if (form.scheduleMode === "interval") {
        const hours = parseInt(form.scheduleIntervalHours);
        if (!Number.isInteger(hours) || hours < 1 || hours > 72) {
          errors.push("间隔小时数需为 1~72 的整数");
        }
      } else {
        errors.push("请选择调度模式");
      }
    }

    // 验证消息范围（监听任务无需消息范围，与 HTML 显示逻辑一致）
    if (!form.taskType.startsWith("listen_") && form.taskType !== "cleanup_files") {
      if (form.messageRangeMode === "date_range") {
        if (!form.startDate || !form.endDate) {
          errors.push("请选择日期范围");
        } else if (new Date(form.startDate) > new Date(form.endDate)) {
          errors.push("开始日期不能晚于结束日期");
        }
      } else if (form.messageRangeMode === "id_range") {
        const minId = parseInt(form.minId);
        const maxId = parseInt(form.maxId);
        if (!minId || !maxId) {
          errors.push("请输入消息 ID 范围");
        } else if (minId > maxId) {
          errors.push("最小 ID 不能大于最大 ID");
        } else if (minId < 1 || maxId < 1) {
          errors.push("消息 ID 必须为正整数");
        }
      } else if (form.messageRangeMode === "multiple_ids") {
        const count = this.getParsedItemCount();
        if (count === 0) {
          errors.push("请输入至少一个消息 ID 或链接");
        }
      } else if (form.messageRangeMode === "recent") {
        const count = parseInt(form.recentCount);
        if (!count || count <= 0) {
          errors.push("请输入有效的消息数量（大于 0）");
        } else if (count > 1000) {
          errors.push("消息数量不能超过 1000 条");
        }
      }
    }

    return errors;
  }

  /**
   * 启动定时轮询任务列表（兼容旧接口，内部委托给智能轮询）
   * @param {number} interval - 轮询间隔（毫秒），已废弃，保留兼容性
   * @deprecated 使用 startSmartPolling() 替代
   */
  startPolling(interval = 5000) {
    console.warn("startPolling() 已废弃，请使用 startSmartPolling()");
    this.startSmartPolling();
  }

  /**
   * 停止定时轮询（兼容旧接口）
   * @deprecated 使用 stopSmartPolling() 替代
   */
  stopPolling() {
    this.stopSmartPolling();
  }

  /**
   * 检查是否存在活跃任务（运行中/排队中/等待中）
   * @returns {boolean}
   */
  hasActiveTasks() {
    const activeStatuses = ["pending", "queued", "running"];
    return this.tasks.some((task) => activeStatuses.includes(task.status));
  }

  /**
   * 核心决策逻辑：根据任务状态调整轮询策略
   */
  _checkAndAdjustPolling() {
    const hasActive = this.hasActiveTasks();

    if (hasActive && !this._smartPollTimer) {
      // 有活跃任务且未在轮询 → 启动智能轮询
      console.log(
        "检测到活跃任务，启动智能轮询（间隔:",
        this._smartPollInterval,
        "ms）",
      );
      this._smartPollTimer = setInterval(async () => {
        await this.loadTasks();
        // 每次加载后重新检查状态
        this._checkAndAdjustPolling();
      }, this._smartPollInterval);
    } else if (!hasActive && this._smartPollTimer) {
      // 无活跃任务且在轮询 → 停止轮询
      console.log("所有任务已静止，停止自动刷新");
      this.stopSmartPolling();
    }
  }

  /**
   * 启动智能轮询
   * 根据当前任务状态决定是否启动自动刷新
   */
  startSmartPolling() {
    // 先清除可能存在的旧定时器
    this.stopSmartPolling();

    // 重置错误计数
    this._consecutiveErrors = 0;

    // 立即执行一次检查
    this._checkAndAdjustPolling();

    // 页面不可见时暂停轮询，节省资源
    document.addEventListener("visibilitychange", this._handleVisibilityChange);
  }

  /**
   * 停止智能轮询
   */
  stopSmartPolling() {
    if (this._smartPollTimer) {
      clearInterval(this._smartPollTimer);
      this._smartPollTimer = null;
      console.log("智能轮询已停止");
    }

    // 移除可见性监听
    document.removeEventListener(
      "visibilitychange",
      this._handleVisibilityChange,
    );
  }

  /**
   * 处理页面可见性变化
   */
  _handleVisibilityChange = () => {
    if (document.hidden) {
      // 页面隐藏 → 暂停轮询
      console.log("页面隐藏，暂停智能轮询");
      this.stopSmartPolling();
    } else {
      // 页面显示 → 立即刷新 + 重新评估
      console.log("页面显示，立即刷新并评估轮询策略");
      this.loadTasks().then(() => {
        this._checkAndAdjustPolling();
      });
    }
  };

  /**
   * 显示通知
   * @param {string} type - 通知类型
   * @param {string} message - 通知内容
   */
  _notify(type, message) {
    // 使用全局通知系统（如果存在）
    if (window.showNotification) {
      window.showNotification(type, message);
    } else {
      console.log(`[${type}] ${message}`);
    }
  }

  /**
   * 关闭创建弹窗
   */
  closeCreateModal() {
    this.showCreateModal = false;
    this.createForm = this._resetCreateForm();
  }

  /**
   * 获取状态对应的 CSS 类
   * @param {string} status - 任务状态
   */
  getStatusBadgeClass(status) {
    const classMap = {
      pending: "badge-pending",
      queued: "badge-queued",
      running: "badge-running",
      completed: "badge-completed",
      failed: "badge-failed",
      cancelled: "badge-cancelled",
    };
    return classMap[status] || "badge-pending";
  }

  /**
   * 获取状态对应的中文文本
   * @param {string} status - 任务状态
   */
  getStatusText(status) {
    const textMap = {
      pending: "等待中",
      queued: "排队中",
      running: "执行中",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
    };
    return textMap[status] || status;
  }

  /**
   * 获取任务类型对应的中文文本
   * @param {string} type - 任务类型
   */
  getTypeText(type) {
    const textMap = {
      download: "下载",
      forward: "转发",
      upload: "上传",
      listen_download: "🕵️ 监听下载",
      listen_forward: "📲 监听转发",
      cleanup_files: "🧹 定时清理",
    };
    return textMap[type] || type;
  }

  /**
   * 获取范围模式对应的中文文本
   * @param {string} rangeMode - 范围模式
   */
  getRangeModeText(rangeMode) {
    const textMap = {
      id_range: "ID范围",
      date_range: "日期范围",
      multiple_ids: "消息列表",
      all: "全部消息",
      recent: "最近N条",
    };
    return textMap[rangeMode] || rangeMode || "-";
  }

  /**
   * 格式化类型过滤列表为中文文本
   * @param {string[]} filterTypes - 类型过滤列表
   */
  formatFilterTypes(filterTypes) {
    if (!filterTypes || filterTypes.length === 0) return "全部类型";
    const textMap = {
      video: "视频",
      photo: "图片",
      document: "文档",
      audio: "音频",
      animation: "动图",
      voice: "语音",
      video_note: "视频笔记",
    };
    return filterTypes.map((t) => textMap[t] || t).join(", ");
  }

  /**
   * 格式化 ID 列表，支持截断显示
   * @param {Array<number|string>} ids - ID 列表
   * @param {number} maxCount - 最多直接展示的 ID 数量
   */
  formatIdList(ids, maxCount = 5) {
    if (!ids || ids.length === 0) return "-";
    const visible = ids.slice(0, maxCount);
    let result = visible.join(", ");
    if (ids.length > maxCount) {
      result += ` ... 等 ${ids.length} 条`;
    }
    return result;
  }

  /**
   * 格式化文件大小过滤
   * @param {number|null} minSize - 最小字节数
   * @param {number|null} maxSize - 最大字节数
   */
  formatSizeFilter(minSize, maxSize) {
    const hasMin = minSize !== null && minSize !== undefined && minSize > 0;
    const hasMax = maxSize !== null && maxSize !== undefined && maxSize > 0;
    if (!hasMin && !hasMax) return null;
    if (hasMin && hasMax) {
      return `${this.formatFileSize(minSize)} — ${this.formatFileSize(maxSize)}`;
    }
    if (hasMin) {
      return `最小 ${this.formatFileSize(minSize)}`;
    }
    return `最大 ${this.formatFileSize(maxSize)}`;
  }

  /**
   * 格式化布尔值为中文文本
   * @param {boolean} value - 布尔值
   */
  formatBoolean(value) {
    return value ? "是" : "否";
  }

  /**
   * 格式化文件大小
   * @param {number} bytes - 字节数
   */
  formatFileSize(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let unitIndex = 0;

    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex++;
    }

    return `${size.toFixed(1)} ${units[unitIndex]}`;
  }

  /**
   * 格式化速度
   * @param {number} bytesPerSecond - 每秒字节数
   */
  formatSpeed(bytesPerSecond) {
    if (!bytesPerSecond) return "0 B/s";
    return this.formatFileSize(bytesPerSecond) + "/s";
  }

  /**
   * 格式化时间
   * @param {string} isoString - ISO 时间字符串
   */
  formatTime(isoString) {
    if (!isoString) return "-";
    const date = new Date(isoString);
    return date.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  /**
   * 格式化 ETA（预计剩余时间）
   * @param {number} seconds - 秒数
   */
  formatETA(seconds) {
    if (!seconds) return "-";

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    if (hours > 0) {
      return `${hours}小时${minutes}分钟`;
    } else if (minutes > 0) {
      return `${minutes}分钟${secs}秒`;
    } else {
      return `${secs}秒`;
    }
  }
}

/**
 * 通用确认对话框组件 - 替代原生 confirm() (P2-3)
 *
 * 特性:
 * - 支持 HTML 富文本内容
 * - 支持自定义按钮文案和样式
 * - 支持 Promise 化调用
 * - 与项目暗色主题一致
 */
class ConfirmDialog {
  constructor() {
    this.visible = false;
    this.title = "";
    this.message = "";
    this.type = "warning"; // 'warning' | 'danger' | 'info'
    this.confirmText = "确认";
    this.cancelText = "取消";
    this.showCancel = true;
    this.resolvePromise = null; // Promise resolve 函数

    // 图标配置
    this.icons = {
      warning: "⚠️",
      danger: "❌",
      info: "ℹ️",
    };
  }

  /**
   * 显示确认对话框
   * @param {Object} options - 配置选项
   * @returns {Promise<boolean>} 用户是否点击确认
   */
  show(options = {}) {
    // 配置合并
    this.title = options.title || "请确认";
    this.message = options.message || "";
    this.type = options.type || "warning";
    this.confirmText = options.confirmText || "确认";
    this.cancelText = options.cancelText || "取消";
    this.showCancel = options.showCancel !== false;

    // 显示弹窗
    this.visible = true;

    // 返回 Promise
    return new Promise((resolve) => {
      this.resolvePromise = resolve;
    });
  }

  /**
   * 用户点击确认
   */
  onConfirm() {
    this.visible = false;
    if (this.resolvePromise) {
      this.resolvePromise(true);
      this.resolvePromise = null;
    }
  }

  /**
   * 用户点击取消/关闭
   */
  onCancel() {
    this.visible = false;
    if (this.resolvePromise) {
      this.resolvePromise(false);
      this.resolvePromise = null;
    }
  }
}

// 创建全局单例实例
window.confirmDialog = new ConfirmDialog();

// 创建单例实例（使用 var 使其成为全局变量，供 Alpine.js 模板直接引用）
var taskManager = new TaskManager();

// 导出供 Alpine.js 使用
window.taskManager = taskManager;

/**
 * 配置逻辑模块
 * 
 * 处理配置获取、更新、表单验证等功能
 */

class ConfigManager {
  constructor() {
    this.config = {};
    this.originalConfig = {};
    this.loading = false;
    this.saving = false;
    this.error = null;
    this.success = false;
    this.activeTab = 'basic'; // basic, download, upload, proxy, notification, resource
    this.hasChanges = false;
  }

  /**
   * 加载配置
   */
  async loadConfig() {
    this.loading = true;
    this.error = null;

    try {
      const config = await api.getConfig();
      this.config = this._flattenConfig(config);
      this.originalConfig = JSON.parse(JSON.stringify(this.config));
      this.hasChanges = false;
    } catch (error) {
      this.error = error.message;
      console.error('加载配置失败:', error);
    } finally {
      this.loading = false;
    }
  }

  /**
   * 保存配置
   */
  async saveConfig() {
    this.saving = true;
    this.error = null;
    this.success = false;

    try {
      // 验证配置
      const validationErrors = this.validateConfig();
      if (validationErrors.length > 0) {
        this.error = validationErrors.join('\n');
        throw new Error(this.error);
      }

      // 构建请求体
      const payload = this._buildUpdatePayload();
      
      // 发送更新请求
      await api.updateConfig(payload);
      
      // 更新原始配置
      this.originalConfig = JSON.parse(JSON.stringify(this.config));
      this.hasChanges = false;
      this.success = true;

      this._notify('success', '配置保存成功');

      // 3 秒后清除成功状态
      setTimeout(() => {
        this.success = false;
      }, 3000);
    } catch (error) {
      this._notify('error', `保存配置失败: ${error.message}`);
      throw error;
    } finally {
      this.saving = false;
    }
  }

  /**
   * 重置配置
   */
  resetConfig() {
    this.config = JSON.parse(JSON.stringify(this.originalConfig));
    this.hasChanges = false;
    this.error = null;
  }

  /**
   * 更新配置项
   * @param {string} key - 配置键
   * @param {any} value - 配置值
   */
  updateConfigValue(key, value) {
    this.config[key] = value;
    this.hasChanges = this._checkHasChanges();
  }

  /**
   * 验证配置
   * @returns {Array<string>} 错误列表
   */
  validateConfig() {
    const errors = [];

    // 基础配置验证
    if (!this.config.api_id) {
      errors.push('API ID 不能为空');
    }

    if (!this.config.api_hash) {
      errors.push('API Hash 不能为空');
    }

    // 下载配置验证
    const maxDownloadTask = parseInt(this.config.max_download_task);
    if (isNaN(maxDownloadTask) || maxDownloadTask < 1) {
      errors.push('下载并发数必须为正整数');
    } else if (maxDownloadTask > 10) {
      errors.push('下载并发数不能超过 10');
    }

    const retryCount = parseInt(this.config.retry_count);
    if (isNaN(retryCount) || retryCount < 0) {
      errors.push('重试次数不能为负数');
    }

    // 上传配置验证
    const maxUploadTask = parseInt(this.config.max_upload_task);
    if (isNaN(maxUploadTask) || maxUploadTask < 1) {
      errors.push('上传并发数必须为正整数');
    } else if (maxUploadTask > 10) {
      errors.push('上传并发数不能超过 10');
    }

    const mediaGroupSize = parseInt(this.config.media_group_size);
    if (isNaN(mediaGroupSize) || mediaGroupSize < 1 || mediaGroupSize > 10) {
      errors.push('媒体组大小必须在 1-10 之间');
    }

    // 代理配置验证
    if (this.config.proxy_enabled === true || this.config.proxy_enabled === 'true') {
      if (!this.config.proxy_host) {
        errors.push('代理地址不能为空');
      }

      const proxyPort = parseInt(this.config.proxy_port);
      if (isNaN(proxyPort) || proxyPort < 1 || proxyPort > 65535) {
        errors.push('代理端口必须在 1-65535 之间');
      }
    }

    // 资源限制验证
    const maxConcurrentTasks = parseInt(this.config.max_concurrent_tasks);
    if (isNaN(maxConcurrentTasks) || maxConcurrentTasks < 1) {
      errors.push('最大并发任务数必须为正整数');
    }

    const taskSizeWarningGb = parseFloat(this.config.task_size_warning_gb);
    if (isNaN(taskSizeWarningGb) || taskSizeWarningGb < 1) {
      errors.push('任务大小告警阈值必须大于 1GB');
    }

    const taskSizeMaxGb = parseFloat(this.config.task_size_max_gb);
    if (isNaN(taskSizeMaxGb) || taskSizeMaxGb < 1) {
      errors.push('任务大小最大阈值必须大于 1GB');
    }

    if (taskSizeMaxGb <= taskSizeWarningGb) {
      errors.push('任务大小最大阈值必须大于告警阈值');
    }

    const minDiskSpaceGb = parseFloat(this.config.min_disk_space_gb);
    if (isNaN(minDiskSpaceGb) || minDiskSpaceGb < 1) {
      errors.push('最小磁盘空间必须大于 1GB');
    }

    return errors;
  }

  /**
   * 检查是否有未保存的更改
   */
  _checkHasChanges() {
    return JSON.stringify(this.config) !== JSON.stringify(this.originalConfig);
  }

  /**
   * 构建更新请求体
   */
  _buildUpdatePayload() {
    return {
      download_type: this.config.download_type || [],
      max_retry_count: parseInt(this.config.retry_count),
      resource_limits: {
        max_concurrent_tasks: parseInt(this.config.max_concurrent_tasks) || 1,
        max_download_concurrency: parseInt(this.config.max_download_task) || 3,
        max_upload_concurrency: parseInt(this.config.max_upload_task) || 1,
        max_forward_concurrency: 1,
        min_disk_space_gb: parseFloat(this.config.min_disk_space_gb) || 2,
        memory_limit_mb: 512,
        task_size_warning_gb: parseFloat(this.config.task_size_warning_gb) || 5,
        task_size_max_gb: parseFloat(this.config.task_size_max_gb) || 10,
      },
      proxy: {
        enable_proxy: this.config.proxy_enabled === true || this.config.proxy_enabled === 'true',
        scheme: this.config.proxy_type || 'socks5',
        hostname: this.config.proxy_host,
        port: parseInt(this.config.proxy_port),
        username: this.config.proxy_username,
        password: this.config.proxy_password,
      },
      upload_max_group_size: parseInt(this.config.media_group_size) || 10,
      // 通知配置：勾选即发送布尔真值，后端合并到 preference 区块
      notification_enabled: this.config.notification_enabled === true || this.config.notification_enabled === 'true',
      error_notification_enabled: this.config.error_notification_enabled === true || this.config.error_notification_enabled === 'true',
    };
  }

  /**
   * 将嵌套配置扁平化
   * @param {object} config - 嵌套配置对象
   * @returns {object} 扁平化配置
   */
  _flattenConfig(config) {
    const flattened = {};

    // 基础配置（后端 ConfigOut 直接返回）
    flattened.api_id = config.api_id || '';
    flattened.api_hash = config.api_hash || '';
    flattened.bot_token = config.bot_token || '';
    flattened.work_dir = config.work_dir || '';

    // 下载配置
    flattened.download_type = config.download_type || [];
    flattened.retry_count = config.max_retry_count || 3;

    // 上传配置
    flattened.media_group_size = config.upload_max_group_size || 10;
    flattened.upload_delete_after = config.upload_delete_after || false;

    // 代理配置：后端返回嵌套 proxy 对象
    if (config.proxy) {
      flattened.proxy_enabled = config.proxy.enable_proxy || false;
      flattened.proxy_type = config.proxy.scheme || 'socks5';
      flattened.proxy_host = config.proxy.hostname || '';
      flattened.proxy_port = config.proxy.port || 1080;
      flattened.proxy_username = config.proxy.username || '';
      flattened.proxy_password = config.proxy.password || '';
    } else {
      flattened.proxy_enabled = false;
      flattened.proxy_type = 'socks5';
      flattened.proxy_host = '';
      flattened.proxy_port = 1080;
      flattened.proxy_username = '';
      flattened.proxy_password = '';
    }

    // 资源限制：后端返回嵌套 resource_limits 对象
    if (config.resource_limits) {
      flattened.max_concurrent_tasks = config.resource_limits.max_concurrent_tasks || 1;
      flattened.max_download_task = config.resource_limits.max_download_concurrency || 3;
      flattened.max_upload_task = config.resource_limits.max_upload_concurrency || 1;
      flattened.task_size_warning_gb = config.resource_limits.task_size_warning_gb || 5;
      flattened.task_size_max_gb = config.resource_limits.task_size_max_gb || 10;
      flattened.min_disk_space_gb = config.resource_limits.min_disk_space_gb || 2;
    }

    // 通知配置：后端在顶层返回（持久化于 config.yaml 的 preference 区块）
    flattened.notification_enabled =
      config.notification_enabled === true || config.notification_enabled === 'true';
    flattened.error_notification_enabled =
      config.error_notification_enabled === true || config.error_notification_enabled === 'true';

    return flattened;
  }

  /**
   * 设置活动标签页
   * @param {string} tab - 标签页标识
   */
  setActiveTab(tab) {
    this.activeTab = tab;
  }

  /**
   * 判断标签页是否激活
   * @param {string} tab - 标签页标识
   */
  isTabActive(tab) {
    return this.activeTab === tab;
  }

  /**
   * 获取下载类型选项
   */
  getDownloadTypeOptions() {
    return [
      { value: 'photo', label: '图片', icon: '🖼️' },
      { value: 'video', label: '视频', icon: '🎬' },
      { value: 'document', label: '文档', icon: '📄' },
      { value: 'audio', label: '音频', icon: '🎵' },
      { value: 'animation', label: '动画', icon: '✨' },
      { value: 'voice', label: '语音', icon: '🎤' },
      { value: 'video_note', label: '视频笔记', icon: '📹' },
    ];
  }

  /**
   * 检查下载类型是否选中
   * @param {string} type - 类型值
   */
  isDownloadTypeSelected(type) {
    return this.config.download_type && this.config.download_type.includes(type);
  }

  /**
   * 切换下载类型
   * @param {string} type - 类型值
   */
  toggleDownloadType(type) {
    if (!this.config.download_type) {
      this.config.download_type = [];
    }

    const index = this.config.download_type.indexOf(type);
    if (index === -1) {
      this.config.download_type.push(type);
    } else {
      this.config.download_type.splice(index, 1);
    }

    this.hasChanges = this._checkHasChanges();
  }

  /**
   * 显示通知
   * @param {string} type - 通知类型
   * @param {string} message - 通知内容
   */
  _notify(type, message) {
    if (window.showNotification) {
      window.showNotification(type, message);
    } else {
      console.log(`[${type}] ${message}`);
    }
  }
}

// 创建单例实例
const configManager = new ConfigManager();

// 导出供 Alpine.js 使用
window.configManager = configManager;

/**
 * 文件管理逻辑模块
 * 
 * 处理文件浏览、选择、上传准备等功能
 * 注意: 状态由 Alpine 组件管理，FileManager 作为纯服务层
 */

class FileManager {
  constructor() {
    this.mediaGroupSize = 10; // Telegram 媒体组上限
  }

  /**
   * 加载文件列表
   * @param {string} path - 目录路径
   * @returns {Promise<Array>} 文件列表
   */
  async loadFiles(path) {
    try {
      const response = await api.getFiles(path);
      // 将 API 的 type 字段转换为前端的 is_directory 字段
      const items = response.items || [];
      return items.map(item => ({
        ...item,
        is_directory: item.type === 'directory'
      }));
    } catch (error) {
      console.error('加载文件列表失败:', error);
      throw error;
    }
  }

  /**
   * 进入子目录
   * @param {string} currentPath - 当前路径
   * @param {string} dirName - 目录名
   * @returns {string} 新路径
   */
  enterDirectory(currentPath, dirName) {
    return currentPath === '/' 
      ? `/${dirName}` 
      : `${currentPath}/${dirName}`;
  }

  /**
   * 返回父目录
   * @param {string} currentPath - 当前路径
   * @returns {string} 父路径
   */
  goToParentDirectory(currentPath) {
    if (currentPath === '/') return '/';
    
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    const parentPath = '/' + parts.join('/');
    return parentPath || '/';
  }

  /**
   * 切换文件选择状态
   * @param {Array} selectedFiles - 当前选中文件列表
   * @param {object} file - 文件对象
   * @returns {Array} 更新后的选中文件列表
   */
  toggleFileSelection(selectedFiles, file) {
    if (file.is_directory) return selectedFiles;

    const index = selectedFiles.findIndex(f => f.path === file.path);
    const newSelected = [...selectedFiles];
    if (index === -1) {
      newSelected.push(file);
    } else {
      newSelected.splice(index, 1);
    }
    return newSelected;
  }

  /**
   * 全选当前目录下的所有文件
   * @param {Array} files - 文件列表
   * @returns {Array} 选中的文件列表
   */
  selectAllFiles(files) {
    return files
      .filter(f => !f.is_directory)
      .map(f => ({ ...f }));
  }

  /**
   * 获取已选文件总大小
   * @param {Array} selectedFiles - 选中文件列表
   * @returns {number} 总大小（字节）
   */
  getTotalSelectedSize(selectedFiles) {
    return selectedFiles.reduce((total, file) => total + (file.size || 0), 0);
  }

  /**
   * 创建上传任务
   * @param {Array} selectedFiles - 选中文件列表
   * @param {string} targetChat - 目标频道（支持纯数字 chat_id、@username、t.me 链接、+ 私有邀请）
   * @param {boolean} sendAsMediaGroup - 是否发送为媒体组
   * @param {boolean} deleteAfterUpload - 上传后是否删除本地文件
   */
  async createUploadTask(selectedFiles, targetChat, sendAsMediaGroup, deleteAfterUpload) {
    if (!targetChat) {
      throw new Error('请输入目标频道');
    }

    // 如果启用媒体组，按组拆分文件
    const fileGroups = sendAsMediaGroup
      ? this._splitIntoMediaGroups(selectedFiles)
      : [selectedFiles];

    // 目标频道标识符归一化：纯数字走 chat_id，其它格式（@username / t.me
    // 链接 / + 私有邀请）走 source_identifier，与 download/forward 任务
    // 在前端层保持一致（后端路由对 chat_id 字段也做了非数字兜底）。
    const trimmed = String(targetChat).trim();
    const isNumeric = /^-?\d+$/.test(trimmed);

    // 为每个组创建上传任务
    for (const fileGroup of fileGroups) {
      const params = {
        file_paths: fileGroup.map(f => f.path),
        send_as_media_group: sendAsMediaGroup && fileGroup.length > 1,
        delete_after_upload: deleteAfterUpload,
      };
      if (isNumeric) {
        params.chat_id = parseInt(trimmed, 10);
      } else {
        params.source_identifier = trimmed;
      }

      const payload = {
        task_type: 'upload',
        params,
      };

      await api.createTask(payload);
    }

    return fileGroups.length;
  }

  /**
   * 将文件列表拆分为媒体组
   * @param {Array} files - 文件列表
   * @returns {Array<Array>} 分组后的文件列表
   */
  _splitIntoMediaGroups(files) {
    const groups = [];
    for (let i = 0; i < files.length; i += this.mediaGroupSize) {
      groups.push(files.slice(i, i + this.mediaGroupSize));
    }
    return groups;
  }

  /**
   * 排序文件列表
   * @param {Array} files - 文件列表
   * @param {string} sortBy - 排序字段
   * @param {string} sortOrder - 排序方向
   * @returns {Array} 排序后的文件列表
   */
  sortFiles(files, sortBy, sortOrder) {
    const sorted = [...files].sort((a, b) => {
      // 目录始终排在文件前面
      if (a.is_directory !== b.is_directory) {
        return a.is_directory ? -1 : 1;
      }

      let comparison = 0;
      switch (sortBy) {
        case 'name':
          comparison = a.name.localeCompare(b.name);
          break;
        case 'size':
          comparison = (a.size || 0) - (b.size || 0);
          break;
        case 'date':
          comparison = new Date(a.modified_at || 0) - new Date(b.modified_at || 0);
          break;
      }

      return sortOrder === 'asc' ? comparison : -comparison;
    });
    return sorted;
  }

  /**
   * 切换排序
   * @param {string} currentSortBy - 当前排序字段
   * @param {string} currentSortOrder - 当前排序方向
   * @param {string} newSortBy - 新排序字段
   * @returns {{sortBy: string, sortOrder: string}} 新的排序状态
   */
  toggleSort(currentSortBy, currentSortOrder, newSortBy) {
    if (currentSortBy === newSortBy) {
      return { sortBy: currentSortBy, sortOrder: currentSortOrder === 'asc' ? 'desc' : 'asc' };
    } else {
      return { sortBy: newSortBy, sortOrder: 'asc' };
    }
  }

  /**
   * 获取文件图标
   * @param {object} file - 文件对象
   */
  getFileIcon(file) {
    if (file.is_directory) {
      return '📁';
    }

    const ext = file.name.split('.').pop().toLowerCase();
    const iconMap = {
      // 视频
      mp4: '🎬', mkv: '🎬', avi: '🎬', mov: '', webm: '🎬',
      // 图片
      jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '️', webp: '🖼️', bmp: '🖼️',
      // 音频
      mp3: '🎵', wav: '🎵', flac: '', aac: '🎵', ogg: '🎵',
      // 文档
      pdf: '📄', doc: '', docx: '📄', txt: '📄', md: '📄',
      // 压缩文件
      zip: '📦', rar: '📦', '7z': '📦', tar: '', gz: '📦',
    };

    return iconMap[ext] || '';
  }

  /**
   * 判断文件是否为媒体文件
   * @param {object} file - 文件对象
   */
  isMediaFile(file) {
    if (file.is_directory) return false;
    
    const ext = file.name.split('.').pop().toLowerCase();
    const mediaExts = ['mp4', 'mkv', 'avi', 'mov', 'webm', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'mp3', 'wav', 'flac', 'aac', 'ogg'];
    return mediaExts.includes(ext);
  }

  /**
   * 获取面包屑路径数组
   * @param {string} currentPath - 当前路径
   */
  getBreadcrumbs(currentPath) {
    if (currentPath === '/') {
      return [{ name: '根目录', path: '/' }];
    }

    const parts = currentPath.split('/').filter(Boolean);
    const breadcrumbs = [{ name: '根目录', path: '/' }];
    
    let path = '';
    for (const part of parts) {
      path += `/${part}`;
      breadcrumbs.push({
        name: part,
        path: path,
      });
    }

    return breadcrumbs;
  }

  /**
   * 格式化文件大小
   * @param {number} bytes - 字节数
   */
  formatFileSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = bytes;
    let unitIndex = 0;
    
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex++;
    }
    
    return `${size.toFixed(1)} ${units[unitIndex]}`;
  }

  /**
   * 格式化时间
   * @param {string} isoString - ISO 时间字符串
   */
  formatTime(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}

// 创建单例实例
const fileManager = new FileManager();

// 导出供 Alpine.js 使用
window.fileManager = fileManager;

#!/bin/bash
set -e

# ============================================================
# TRMD 一键部署脚本
# 用法: ./deploy.sh
# 支持通过环境变量覆盖 .env 配置:
#   IMAGE_TAG=v1.2.3 ./deploy.sh
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 1. 前置检查
info "检查环境依赖..."
command -v docker >/dev/null 2>&1 || { error "Docker 未安装，请先安装 Docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { error "Docker Compose 未安装"; exit 1; }

# 2. 确保日志目录存在（Docker 目录挂载 ./logs:/app/module/logs）
mkdir -p logs

# 3. 检查配置文件
if [ ! -f config/config.yaml ]; then
    error "config/config.yaml 不存在！"
    echo "请从模板创建: cp config/config.yaml.template config/config.yaml"
    echo "并编辑填入 Telegram API 凭证"
    exit 1
fi

# 4. 加载 .env（环境变量可覆盖）
if [ -f .env ]; then
    set -a
    . .env
    set +a
fi

# 5. 停止旧容器（忽略错误，首次部署时可能不存在）
info "停止旧容器..."
docker compose down 2>/dev/null || true

# 6. 拉取最新镜像
info "拉取镜像: ${IMAGE_NAME}:${IMAGE_TAG}..."
docker compose pull

# 7. 检测是否需要 Telegram 登录
NEED_LOGIN=false
if [ ! -d "sessions" ] || [ -z "$(ls -A sessions 2>/dev/null)" ]; then
    warn "检测到 sessions/ 目录为空，需要进行 Telegram 客户端登录"
    NEED_LOGIN=true
fi

# 8. 首次登录流程（交互式）
if [ "$NEED_LOGIN" = true ]; then
    echo ""
    info "=========================================="
    info "首次部署 - Telegram 客户端登录"
    info "=========================================="
    echo ""
    info "登录流程："
    echo "  1. 输入电话号码（带国际区号，如 +861500000000）"
    echo "  2. 确认号码（输入 y）"
    echo "  3. 输入收到的验证码（通过 SMS/Telegram App/电话发送）"
    echo "  4. 如开启了两步验证，输入密码"
    echo ""
    info "登录成功后，按 Ctrl+C 退出容器"
    echo ""
    read -p "按回车键开始登录流程..."

    # 以前台交互模式启动容器
    docker compose run --rm trmd

    echo ""
    info "登录完成！正在重启为后台模式..."
fi

# 9. 启动新容器（后台模式）
info "启动新容器..."
docker compose up -d

# 10. 等待后检查状态
sleep 3
if docker compose ps --status running 2>/dev/null | grep -q "trmd"; then
    info "部署成功！"
    info "容器状态: $(docker compose ps --status running 2>/dev/null | grep trmd)"
    echo ""
    info "查看日志: docker compose logs -f"
    info "重启服务: ./restart.sh"
    info "清理服务: ./cleanup.sh"
else
    warn "容器可能未正常启动，请检查日志:"
    echo "  docker compose logs"
    docker compose ps
fi
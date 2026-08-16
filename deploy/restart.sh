#!/bin/bash
set -e

# ============================================================
# TRMD 一键重启脚本
# 用法: ./restart.sh
# 停止旧容器 → 拉取最新镜像 → 启动新容器
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# 1. 确保日志目录存在（Docker 目录挂载 ./logs:/app/module/logs）
mkdir -p logs

# 2. 加载 .env
if [ -f .env ]; then
    set -a
    . .env
    set +a
fi

# 3. 停止旧容器
info "停止旧容器..."
docker compose down

# 4. 拉取最新镜像
info "拉取最新镜像..."
docker compose pull

# 5. 启动新容器
info "启动新容器..."
docker compose up -d

# 6. 检查状态
sleep 3
if docker compose ps --status running 2>/dev/null | grep -q "trmd"; then
    info "重启完成！"
    info "查看日志: docker compose logs -f"
else
    warn "容器可能未正常启动，请检查日志: docker compose logs"
fi
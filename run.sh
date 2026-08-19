#!/bin/bash
# ============================================================
# 企业微信客服系统 - 启动/测试/检查脚本
# 用法：
#   ./run.sh install   安装依赖
#   ./run.sh start     通过 uvicorn 启动服务
#   ./run.sh run       直接运行 main.py
#   ./run.sh test      运行集成测试
#   ./run.sh check     健康检查
# ============================================================

set -e

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# 颜色输出
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
NC="\033[0m"

echo_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 安装依赖
install() {
    echo_info "安装项目依赖..."
    pip3 install -r requirements.txt --break-system-packages
    echo_info "依赖安装完成"
}

# 通过 uvicorn 启动
start() {
    echo_info "通过 uvicorn 启动企业微信客服系统..."
    python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
}

# 直接运行 main.py
run() {
    echo_info "直接运行 main.py..."
    python3 app/main.py
}

# 运行测试
test() {
    echo_info "运行集成测试..."
    python3 -m unittest discover -s tests -v
}

# 健康检查
check() {
    echo_info "健康检查..."
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
    if [ "$response" = "200" ]; then
        echo_info "服务运行正常"
        curl -s http://localhost:8000/health | python3 -m json.tool
    else
        echo_error "服务未运行或不可达 (HTTP $response)"
        exit 1
    fi
}

# 使用说明
usage() {
    echo "企业微信客服系统"
    echo ""
    echo "用法: $0 <命令>"
    echo ""
    echo "命令:"
    echo "  install   安装项目依赖"
    echo "  start     通过 uvicorn 启动服务"
    echo "  run       直接运行 main.py"
    echo "  test      运行集成测试"
    echo "  check     健康检查"
    echo ""
}

# 主逻辑
case "$1" in
    install) install ;;
    start)   start ;;
    run)     run ;;
    test)    test ;;
    check)   check ;;
    *)       usage; exit 1 ;;
esac

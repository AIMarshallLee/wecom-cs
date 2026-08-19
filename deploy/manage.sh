#!/bin/bash
# wecom-cs 运维管理脚本
# 用法: ./deploy/manage.sh {start|stop|restart|status|logs|health}

set -e

# ============================================================
# 路径配置
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$SCRIPT_DIR/logs"
PLIST_SERVER="$SCRIPT_DIR/com.wecom-cs.server.plist"
PLIST_TUNNEL="$SCRIPT_DIR/com.wecom-cs.tunnel.plist"
PLIST_LOGROTATE="$SCRIPT_DIR/com.wecom-cs.logrotate.plist"
PLIST_SERVER_NAME="com.wecom-cs.server"
PLIST_TUNNEL_NAME="com.wecom-cs.tunnel"
PLIST_LOGROTATE_NAME="com.wecom-cs.logrotate"

# ============================================================
# 颜色输出
# ============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================
# 停止旧的（非 launchd 管理的）进程
# ============================================================

kill_old_processes() {
    info "检查并停止旧的进程..."

    # 停止旧的 uvicorn 进程
    local uvicorn_pids=$(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)
    if [ -n "$uvicorn_pids" ]; then
        warn "发现旧 uvicorn 进程: $uvicorn_pids，正在停止..."
        echo "$uvicorn_pids" | xargs kill 2>/dev/null || true
        sleep 2
        # 强制杀
        echo "$uvicorn_pids" | xargs kill -9 2>/dev/null || true
        info "旧 uvicorn 进程已停止"
    fi

    # 停止旧的 gunicorn 进程（非 launchd 管理）
    local gunicorn_pids=$(pgrep -f "gunicorn.*app.main:app" 2>/dev/null | head -5 || true)
    if [ -n "$gunicorn_pids" ]; then
        warn "发现旧 gunicorn 进程: $gunicorn_pids，正在停止..."
        echo "$gunicorn_pids" | xargs kill 2>/dev/null || true
        sleep 2
        echo "$gunicorn_pids" | xargs kill -9 2>/dev/null || true
        info "旧 gunicorn 进程已停止"
    fi

    # 停止旧的 expect 脚本 SSH 隧道
    local expect_pids=$(pgrep -f "ssh_reverse_tunnel" 2>/dev/null || true)
    if [ -n "$expect_pids" ]; then
        warn "发现旧 expect 隧道脚本: $expect_pids，正在停止..."
        echo "$expect_pids" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$expect_pids" | xargs kill -9 2>/dev/null || true
        info "旧 expect 隧道脚本已停止"
    fi

    # 停止旧的 SSH 隧道（非 autossh 管理的）
    local ssh_pids=$(pgrep -f "ssh.*-R 8000:localhost:8000.*120.79.227.94" 2>/dev/null || true)
    if [ -n "$ssh_pids" ]; then
        warn "发现旧 SSH 隧道进程: $ssh_pids，正在停止..."
        echo "$ssh_pids" | xargs kill 2>/dev/null || true
        sleep 1
        echo "$ssh_pids" | xargs kill -9 2>/dev/null || true
        info "旧 SSH 隧道进程已停止"
    fi
}

# ============================================================
# 重建 plist 文件（用 bash heredoc 确保 launchd 兼容）
# ============================================================

_rebuild_plists() {
    local dir="$LAUNCH_AGENTS_DIR"
    local proj="/Users/dasean/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/work-mode-projects/6a845ef8868ed2b3b328c7fd/wecom-cs"

    # 隧道 plist
    cat > "$dir/com.wecom-cs.tunnel.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wecom-cs.tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/autossh</string>
        <string>-M</string>
        <string>0</string>
        <string>-N</string>
        <string>-R</string>
        <string>8000:localhost:8000</string>
        <string>-o</string>
        <string>ServerAliveInterval=30</string>
        <string>-o</string>
        <string>ServerAliveCountMax=3</string>
        <string>-o</string>
        <string>ExitOnForwardFailure=yes</string>
        <string>-o</string>
        <string>StrictHostKeyChecking=no</string>
        <string>root@120.79.227.94</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AUTOSSH_GATETIME</key>
        <string>30</string>
        <key>AUTOSSH_POLL</key>
        <string>10</string>
        <key>HOME</key>
        <string>/Users/dasean</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>PLACEHOLDER/logs/tunnel.log</string>
    <key>StandardErrorPath</key>
    <string>PLACEHOLDER/logs/tunnel.err.log</string>
</dict>
</plist>
EOF
    sed -i '' "s|PLACEHOLDER|$proj/deploy|g" "$dir/com.wecom-cs.tunnel.plist"

    # 服务器 plist
    cat > "$dir/com.wecom-cs.server.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wecom-cs.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>PLACEHOLDER/deploy/start_server.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/dasean</string>
        <key>LOG_LEVEL</key>
        <string>INFO</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>PLACEHOLDER/deploy/logs/server.log</string>
    <key>StandardErrorPath</key>
    <string>PLACEHOLDER/deploy/logs/server.err.log</string>
</dict>
</plist>
EOF
    sed -i '' "s|PLACEHOLDER|$proj|g" "$dir/com.wecom-cs.server.plist"

    # 日志轮转 plist
    cat > "$dir/com.wecom-cs.logrotate.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wecom-cs.logrotate</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>PLACEHOLDER/deploy/logrotate.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>PLACEHOLDER/deploy/logs/rotate.log</string>
    <key>StandardErrorPath</key>
    <string>PLACEHOLDER/deploy/logs/rotate.err.log</string>
</dict>
</plist>
EOF
    sed -i '' "s|PLACEHOLDER|$proj|g" "$dir/com.wecom-cs.logrotate.plist"
}

# ============================================================
# 启动服务
# ============================================================

start_services() {
    info "启动运维加固服务..."

    mkdir -p "$LOG_DIR"
    mkdir -p "$LAUNCH_AGENTS_DIR"

    # 先停止旧的非 launchd 进程
    kill_old_processes

    # 先卸载已加载的服务（如果存在）
    launchctl bootout "gui/$UID/com.wecom-cs.tunnel" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.wecom-cs.server" 2>/dev/null || true
    launchctl bootout "gui/$UID/com.wecom-cs.logrotate" 2>/dev/null || true
    sleep 3

    # 复制 plist 到 LaunchAgents 目录
    cp "$PLIST_TUNNEL" "$LAUNCH_AGENTS_DIR/" 2>/dev/null || true
    cp "$PLIST_SERVER" "$LAUNCH_AGENTS_DIR/" 2>/dev/null || true
    cp "$PLIST_LOGROTATE" "$LAUNCH_AGENTS_DIR/" 2>/dev/null || true

    # 用 bash 重建 plist（避免 Write 工具创建的文件格式问题）
    _rebuild_plists

    # 加载隧道服务
    info "加载 SSH 隧道服务 (autossh)..."
    launchctl bootstrap "gui/$UID" "$LAUNCH_AGENTS_DIR/com.wecom-cs.tunnel.plist" 2>/dev/null || true
    info "SSH 隧道服务已加载"

    # 等隧道建立
    sleep 3

    # 加载服务器服务
    info "加载应用服务器 (gunicorn 2 workers)..."
    launchctl bootstrap "gui/$UID" "$LAUNCH_AGENTS_DIR/com.wecom-cs.server.plist" 2>/dev/null || true
    info "应用服务器已加载"

    # 加载日志轮转
    info "加载日志轮转任务 (每小时)..."
    launchctl bootstrap "gui/$UID" "$LAUNCH_AGENTS_DIR/com.wecom-cs.logrotate.plist" 2>/dev/null || true
    info "日志轮转任务已加载"

    # 等待服务启动
    sleep 5

    # 验证
    check_status
}

# ============================================================
# 停止服务
# ============================================================

stop_services() {
    info "停止运维加固服务..."

    launchctl bootout "gui/$UID/com.wecom-cs.server" 2>/dev/null || true
    info "应用服务器已停止"

    launchctl bootout "gui/$UID/com.wecom-cs.logrotate" 2>/dev/null || true
    info "日志轮转已停止"

    launchctl bootout "gui/$UID/com.wecom-cs.tunnel" 2>/dev/null || true
    info "SSH 隧道已停止"

    # 清理可能残留的进程
    kill_old_processes

    info "所有服务已停止"
}

# ============================================================
# 检查状态
# ============================================================

check_status() {
    echo ""
    echo "=========================================="
    echo "  wecom-cs 运维状态"
    echo "=========================================="
    echo ""

    # 服务器状态
    local server_pid=$(pgrep -f "gunicorn.*app.main:app" 2>/dev/null | head -1 || true)
    if [ -n "$server_pid" ]; then
        local worker_count=$(pgrep -f "gunicorn.*app.main:app" 2>/dev/null | wc -l | tr -d ' ')
        echo -e "  应用服务器:  ${GREEN}运行中${NC} (PID: $server_pid, workers: $worker_count)"
    else
        echo -e "  应用服务器:  ${RED}未运行${NC}"
    fi

    # 隧道状态
    local tunnel_pid=$(pgrep -f "autossh.*120.79.227.94" 2>/dev/null | head -1 || true)
    if [ -n "$tunnel_pid" ]; then
        local ssh_pid=$(pgrep -f "ssh.*-R 8000:localhost:8000.*120.79.227.94" 2>/dev/null | head -1 || true)
        echo -e "  SSH 隧道:    ${GREEN}运行中${NC} (autossh PID: $tunnel_pid, ssh PID: $ssh_pid)"
    else
        echo -e "  SSH 隧道:    ${RED}未运行${NC}"
    fi

    # 健康检查
    local health=$(curl -sf -m 5 http://localhost:8000/health 2>/dev/null || echo "")
    if [ -n "$health" ]; then
        echo -e "  健康检查:    ${GREEN}正常${NC} ($health)"
    else
        echo -e "  健康检查:    ${RED}失败${NC}"
    fi

    # 远程端口检查
    local remote_check=$(ssh -o BatchMode=yes -o ConnectTimeout=3 root@120.79.227.94 \
        "curl -sf -m 3 http://localhost:8000/health 2>/dev/null || echo FAIL" 2>/dev/null || echo "SSH_ERROR")
    if echo "$remote_check" | grep -q "ok\|healthy\|running" 2>/dev/null; then
        echo -e "  远程端口:    ${GREEN}可达${NC} (120.79.227.94:8000 → localhost:8000)"
    else
        echo -e "  远程端口:    ${RED}不可达${NC}"
    fi

    # 日志文件
    echo ""
    echo "  日志文件:"
    for logfile in server.log server.err.log tunnel.log tunnel.err.log; do
        local f="$LOG_DIR/$logfile"
        if [ -f "$f" ]; then
            local size=$(du -h "$f" | cut -f1)
            echo "    $logfile  ($size)"
        fi
    done

    echo ""
    echo "=========================================="
}

# ============================================================
# 查看日志
# ============================================================

tail_logs() {
    local logfile="${1:-server.log}"
    local f="$LOG_DIR/$logfile"
    if [ -f "$f" ]; then
        info "实时查看 $logfile (Ctrl+C 退出)..."
        tail -f "$f"
    else
        error "日志文件不存在: $logfile"
        echo "可用日志文件:"
        ls -1 "$LOG_DIR"/*.log 2>/dev/null || echo "  (无)"
    fi
}

# ============================================================
# 健康检查
# ============================================================

health_check() {
    info "执行健康检查..."

    # 本地检查
    local local_health=$(curl -sf -m 5 http://localhost:8000/health 2>/dev/null || echo "")
    if [ -n "$local_health" ]; then
        info "本地服务: 正常 ($local_health)"
    else
        error "本地服务: 不可达"
    fi

    # 远程检查（通过 SSH 隧道）
    local remote_check=$(ssh -o BatchMode=yes -o ConnectTimeout=5 root@120.79.227.94 \
        "curl -sf -m 3 http://localhost:8000/health 2>/dev/null || echo FAIL" 2>/dev/null || echo "SSH_ERROR")
    if echo "$remote_check" | grep -q "ok\|healthy\|running" 2>/dev/null; then
        info "远程隧道: 正常 (企业微信回调可达)"
    else
        error "远程隧道: 不可达"
        warn "企业微信消息可能无法收到"
    fi
}

# ============================================================
# 日志轮转
# ============================================================

rotate_logs() {
    info "执行日志轮转..."
    bash "$SCRIPT_DIR/logrotate.sh"
}

# ============================================================
# 主入口
# ============================================================

case "${1:-}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        stop_services
        sleep 3
        start_services
        ;;
    status)
        check_status
        ;;
    logs)
        tail_logs "${2:-server.log}"
        ;;
    health)
        health_check
        ;;
    rotate)
        rotate_logs
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs [文件名]|health|rotate}"
        echo ""
        echo "  start     启动服务（autossh 隧道 + gunicorn 服务器）"
        echo "  stop      停止所有服务"
        echo "  restart   重启所有服务"
        echo "  status    查看服务状态"
        echo "  logs      实时查看日志（默认 server.log）"
        echo "  health    健康检查（本地 + 远程）"
        echo "  rotate    手动触发日志轮转"
        exit 1
        ;;
esac

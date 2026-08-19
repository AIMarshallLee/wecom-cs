"""Gunicorn 生产配置

2 个 uvicorn worker，30s 超时，graceful 10s。
日志输出到 stdout，由 launchd 重定向到文件。
"""

import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

bind = "0.0.0.0:8000"
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"

# 超时设置
timeout = 30          # worker 处理单个请求超时
graceful_timeout = 10 # 优雅退出等待时间
keepalive = 5         # keep-alive 超时

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sμs'

# 进程管理
preload_app = True    # 预加载应用，减少 worker 启动时间
max_requests = 1000   # 每个 worker 处理 1000 个请求后重启（防内存泄漏）
max_requests_jitter = 50  # 随机抖动，避免所有 worker 同时重启

# 优雅重启
capture_output = True

# SSL（由 nginx 或 SSH 隧道处理，这里不配）
keyfile = None
certfile = None

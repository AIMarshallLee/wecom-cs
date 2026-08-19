#!/bin/bash
# Gunicorn 服务器启动脚本
# 由 launchd (com.wecom-cs.server) 调用

PROJECT_DIR="/Users/dasean/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/work-mode-projects/6a845ef8868ed2b3b328c7fd/wecom-cs"
PYTHON="/Users/dasean/Library/Application Support/TRAE SOLO CN/ModularData/ai-agent/vm/tools/opt/python@3.10/3.10.20_3/libexec/bin/python3"

cd "$PROJECT_DIR"
exec "$PYTHON" -m gunicorn app.main:app -c deploy/gunicorn_config.py

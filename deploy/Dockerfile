# syntax=docker/dockerfile:1
# WeCom Customer Service (wecom-cs) 镜像
# Python 3.10-slim + gunicorn (uvicorn worker)

FROM python:3.10-slim

# ============================================================
# 基础环境变量
# ============================================================
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# ============================================================
# 系统依赖（gcc 等用于编译 pycryptodome 等 C 扩展）
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        build-essential \
        libffi-dev \
        libssl-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ============================================================
# 先复制依赖文件，利用 Docker 层缓存
# ============================================================
COPY requirements.txt ./requirements.txt

# 安装 Python 依赖；gunicorn 未在 requirements.txt 中，单独安装
# uvicorn 已在 requirements.txt 中（worker_class=uvicorn.workers.UvicornWorker）
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ============================================================
# 复制项目文件
# ============================================================
COPY . .

# 非 root 运行（提升安全性）
RUN groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser --home-dir /app appuser \
    && chown -R appuser:appuser /app
USER appuser

# ============================================================
# 暴露端口并启动
# ============================================================
EXPOSE 8000

# 使用 gunicorn + uvicorn worker 启动
# 配置文件 deploy/gunicorn_config.py（bind=0.0.0.0:8000, workers=2）
CMD ["gunicorn", "-c", "deploy/gunicorn_config.py", "app.main:app"]

# 激光切割刀缝补偿 API —— 镜像
# Shapely 2.x 的官方 wheel 已内置 GEOS，无需通过 apt 安装 GEOS。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl 供容器 HEALTHCHECK 使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用构建层缓存
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 仅拷贝运行所需代码（见 .dockerignore）
COPY app ./app

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 默认单 worker；docker compose 中可通过 UVICORN_WORKERS 覆盖
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-1}"

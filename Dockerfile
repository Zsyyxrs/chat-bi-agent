# chat-bi-agent Streamlit demo image.
# 构建：docker build -t chat-bi-agent:local .
# 体积优化：psycopg2-binary / numpy / pandas / plotly / jieba 都有 manylinux wheels，
# python:3.11-slim 上无需 build-essential。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# 直接从官方 uv 镜像拷二进制，比 pip install uv 更快也更干净
COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /uvx /bin/

WORKDIR /app

# 先只拷 manifest + lock，让依赖层可以独立缓存
COPY pyproject.toml uv.lock ./

# 装第三方依赖（不装本项目自身），失败时报错而不是重解
# --no-cache 让 uv 不留 wheel 缓存在镜像里（省 ~600MB）
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# 再拷源码，装本项目
COPY src/ ./src/
COPY streamlit_app/ ./streamlit_app/
RUN uv sync --frozen --no-dev --no-cache

EXPOSE 8501

CMD ["streamlit", "run", "streamlit_app/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]

# chat-bi-agent Streamlit demo image.
# 构建：docker build -t chat-bi-agent:local .
# 体积优化：psycopg2-binary / numpy / pandas / plotly / jieba 都有 manylinux wheels，
# python:3.11-slim 上无需 build-essential。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# 直接从官方 uv 镜像拷二进制，比 pip install uv 更快也更干净
COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /uvx /bin/

WORKDIR /app

# 先只拷 manifest + lock，让依赖层可以独立缓存
COPY pyproject.toml uv.lock ./

# uv 默认并发下载数≈50，会把 Docker 内嵌 DNS(127.0.0.11)→宿主机解析器这条
# 转发链路的 UDP 查询打丢，表现为 `dns error / failed to lookup address
# information: Try again`(EAI_AGAIN)，随机卡在某个包上，重试仍失败。
# 降到 4 后实测稳定通过，代价只是依赖层多几十秒——只在改 uv.lock 时才重跑。
ENV UV_CONCURRENT_DOWNLOADS=4

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

# chat-bi-agent Streamlit demo image.
# 构建：docker build -t chat-bi-agent:local .
# 体积优化：psycopg2-binary / numpy / pandas / plotly / jieba 都有 manylinux wheels，
# python:3.11-slim 上无需 build-essential。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY streamlit_app/ ./streamlit_app/

RUN pip install --upgrade pip && pip install .

EXPOSE 8501

CMD ["streamlit", "run", "streamlit_app/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]

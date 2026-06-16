"""Langfuse 客户端初始化。

import 本模块时会从环境变量读取 key 并构造全局 client，
之后整个进程的 @observe 装饰器都会自动归属到这个 client。
"""

import os

from langfuse import Langfuse

_client: Langfuse | None = None


def get_client() -> Langfuse:
    """惰性构造全局 Langfuse client。"""
    global _client
    if _client is not None:
        return _client

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")

    # Bypass HTTP_PROXY for localhost — 否则 OTEL exporter 走代理到本地 Langfuse
    # 会因代理 Bad Gateway / timeout 阻塞后台导出线程，最终拖死主线程。
    existing_no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for host_lit in ("localhost", "127.0.0.1"):
        if host_lit not in existing_no_proxy:
            existing_no_proxy = f"{existing_no_proxy},{host_lit}" if existing_no_proxy else host_lit
    os.environ["NO_PROXY"] = existing_no_proxy
    os.environ["no_proxy"] = existing_no_proxy

    if not public_key or not secret_key:
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 未设置；"
            "请检查 .env 是否已填上 Task 1.2 中创建的 key"
        )

    _client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )
    return _client


def flush() -> None:
    """确保所有 trace 已发送（脚本退出前调用）。"""
    if _client is not None:
        _client.flush()

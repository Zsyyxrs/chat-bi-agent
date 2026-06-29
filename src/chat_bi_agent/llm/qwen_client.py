"""通义千问 (DashScope) chat + embedding 封装。

只暴露两个函数：chat() 和 embed()，输入输出都用纯 Python 类型，
不直接暴露 dashscope 的响应对象。所有调用失败抛 RuntimeError。
"""

import os
from dataclasses import dataclass

# 让 dashscope 绕过本地 HTTP(S)_PROXY（如 Clash/V2Ray 的 127.0.0.1:7897），
# 否则 dashscope.aliyuncs.com 的请求会被代理拦截抛 "InvalidParameter url error"。
# 必须在 `import dashscope` 之前设，dashscope SDK 启动时读这两个变量。
_DASHSCOPE_NO_PROXY = "dashscope.aliyuncs.com,aliyuncs.com"
for _key in ("NO_PROXY", "no_proxy"):
    _existing = os.environ.get(_key, "")
    if _DASHSCOPE_NO_PROXY not in _existing:
        os.environ[_key] = (
            f"{_existing},{_DASHSCOPE_NO_PROXY}" if _existing else _DASHSCOPE_NO_PROXY
        )

import dashscope  # noqa: E402
from dashscope import Generation, TextEmbedding  # noqa: E402
from langfuse import get_client, observe  # noqa: E402

from chat_bi_agent.config import (  # noqa: E402
    CHAT_MODEL,
    DEFAULT_TEMPERATURE,
    EMBED_DIM,
    EMBED_MODEL,
)

__all__ = ["CHAT_MODEL", "EMBED_MODEL", "EMBED_DIM", "ChatResult", "chat", "embed"]


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int


def _ensure_api_key() -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 环境变量未设置")
    dashscope.api_key = api_key


@observe(as_type="generation", name="qwen_chat")
def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
) -> ChatResult:
    """单轮聊天调用。低 temperature 适合 NL2SQL。"""
    _ensure_api_key()
    resp = Generation.call(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        result_format="message",
        temperature=temperature,
    )
    # resp = MultiModalConversation.call(
    #     model=CHAT_MODEL,
    #     messages=[
    #         {"role": "system", "content": system_prompt},
    #         {"role": "user", "content": user_prompt},
    #     ],
    #     temperature=temperature,
    # )
    if resp.status_code != 200:
        raise RuntimeError(f"qwen chat 调用失败: {resp.code} {resp.message}")
    choice = resp.output.choices[0]
    get_client().update_current_generation(
        model=CHAT_MODEL,
        model_parameters={"temperature": temperature},
        usage_details={
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
        },
    )
    # DashScope SDK 返回的 content 有两种格式（取决于 SDK / API 版本）：
    # 1. str（当前默认）：直接是文本
    # 2. list[dict]（旧 multi-modal 兼容格式）：[{"text": "..."}]
    # 在这里做兼容，无论哪种返回都能正确取文本，避免上游每次 SDK 升级都炸。
    raw_content = choice.message.content
    if isinstance(raw_content, list) and raw_content and isinstance(raw_content[0], dict):
        text_content = raw_content[0].get("text", "")
    elif isinstance(raw_content, str):
        text_content = raw_content
    else:
        text_content = ""
    return ChatResult(
        content=text_content,
        prompt_tokens=resp.usage.input_tokens,
        completion_tokens=resp.usage.output_tokens,
    )


@observe(as_type="embedding", name="qwen_embed")
def embed(texts: list[str]) -> list[list[float]]:
    """批量 embedding。返回 list of 1024-dim 向量。"""
    _ensure_api_key()
    resp = TextEmbedding.call(
        model=EMBED_MODEL,
        input=texts,
        dimension=EMBED_DIM,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"qwen embedding 调用失败: {resp.code} {resp.message}")
    # embedding 的 resp.usage 是 dict，只有 total_tokens；chat 的是对象有 input/output_tokens
    usage = getattr(resp, "usage", None) or {}
    input_tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
    get_client().update_current_generation(
        model=EMBED_MODEL,
        model_parameters={"dimension": EMBED_DIM, "batch_size": len(texts)},
        usage_details={"input": input_tokens, "output": 0},
    )
    # dashscope 返回的 embeddings 顺序与 input 一致
    return [item["embedding"] for item in resp.output["embeddings"]]

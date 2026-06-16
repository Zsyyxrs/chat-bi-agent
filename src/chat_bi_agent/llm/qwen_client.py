"""通义千问 (DashScope) chat + embedding 封装。

只暴露两个函数：chat() 和 embed()，输入输出都用纯 Python 类型，
不直接暴露 dashscope 的响应对象。所有调用失败抛 RuntimeError。
"""

import os
from dataclasses import dataclass

import dashscope
from dashscope import MultiModalConversation, TextEmbedding
from langfuse import get_client, observe

from chat_bi_agent.config import (
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
    # resp = Generation.call(
    #     model=CHAT_MODEL,
    #     messages=[
    #         {"role": "system", "content": system_prompt},
    #         {"role": "user", "content": user_prompt},
    #     ],
    #     result_format="message",
    #     temperature=temperature,
    # )
    resp = MultiModalConversation.call(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
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
    return ChatResult(
        # content=choice.message.content,
        content=choice.message.content[0]["text"],
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

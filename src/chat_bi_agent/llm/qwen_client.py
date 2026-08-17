"""通义千问 (DashScope) chat + embedding 封装。

只暴露两个函数：chat() 和 embed()，输入输出都用纯 Python 类型，
不直接暴露 dashscope 的响应对象。所有调用失败抛 RuntimeError。
"""

import os
import time
from dataclasses import dataclass

_DASHSCOPE_NO_PROXY = "dashscope.aliyuncs.com,aliyuncs.com"
for _key in ("NO_PROXY", "no_proxy"):
    _existing = os.environ.get(_key, "")
    if _DASHSCOPE_NO_PROXY not in _existing:
        os.environ[_key] = (
            f"{_existing},{_DASHSCOPE_NO_PROXY}" if _existing else _DASHSCOPE_NO_PROXY
        )

import dashscope  # noqa: E402
import requests  # noqa: E402
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


# DashScope SDK 读的是 `request_timeout`（dashscope.common.constants.REQUEST_TIMEOUT_KEYWORD），
# 不是 `timeout`——写错名字会被静默忽略，照样按 300s 默认值挂着。
REQUEST_TIMEOUT_SECONDS = 60
# 2026-08-17 上调：原为 2 次重试 + 线性退避 2s/4s，总计只扛得住约 6 秒的抖动。
# 当天有三轮跑批（P1 A/B 一轮、P2 两轮，合计 45+ 分钟与对应 LLM 花费）死在
# dashscope 的 DNS/连接瞬断上，每次都超过 6 秒。对「一轮 20 分钟起」的批量评测来说，
# 6 秒就放弃是明显失配：省下的几十秒抵不上报废一整轮。
# 现为 4 次重试 + 指数退避 2/4/8/16，总计约 30 秒。
MAX_TRANSIENT_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 2

# 只重试网络类瞬时故障；配额/鉴权错误重试没意义，必须快速失败
_TRANSIENT_EXC = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
)


def _call_with_retry(fn, **kwargs):
    """瞬时网络故障重试。

    34 题的 eval 跑 10 分钟，中间一次 read timeout 就把整轮结果全丢——
    真实代价是两轮跑批报废，所以这里兜一层。
    """
    last: Exception | None = None
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        try:
            return fn(**kwargs)
        except _TRANSIENT_EXC as e:
            last = e
            if attempt < MAX_TRANSIENT_RETRIES:
                # 指数退避而非线性：DNS/连接瞬断往往持续十几秒，线性 2/4/6 收敛太慢
                time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
    raise last


@observe(as_type="generation", name="qwen_chat")
def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
) -> ChatResult:
    """单轮聊天调用。低 temperature 适合 NL2SQL。"""
    _ensure_api_key()
    resp = _call_with_retry(
        Generation.call,
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        result_format="message",
        temperature=temperature,
        request_timeout=REQUEST_TIMEOUT_SECONDS,
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


# 超过 10 条会被 DashScope 拒掉：
#   InternalError.Algo.InvalidParameter: batch size is invalid,
#   it should not be larger than 10
EMBED_MAX_BATCH = 10


@observe(as_type="embedding", name="qwen_embed")
def embed(texts: list[str]) -> list[list[float]]:
    """批量 embedding。返回 list of 1024-dim 向量，顺序与入参一致。

    DashScope 单次最多收 10 条，这里自动切块，调用方不用关心上限。
    """
    if not texts:
        return []
    _ensure_api_key()

    vectors: list[list[float]] = []
    total_input_tokens = 0
    for start in range(0, len(texts), EMBED_MAX_BATCH):
        chunk = texts[start : start + EMBED_MAX_BATCH]
        resp = _call_with_retry(
            TextEmbedding.call,
            model=EMBED_MODEL,
            input=chunk,
            dimension=EMBED_DIM,
            request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"qwen embedding 调用失败: {resp.code} {resp.message}")
        # embedding 的 resp.usage 是 dict，只有 total_tokens；chat 的是对象有 input/output_tokens
        usage = getattr(resp, "usage", None) or {}
        total_input_tokens += usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
        # dashscope 返回的 embeddings 顺序与 input 一致
        vectors.extend(item["embedding"] for item in resp.output["embeddings"])

    get_client().update_current_generation(
        model=EMBED_MODEL,
        model_parameters={
            "dimension": EMBED_DIM,
            "batch_size": len(texts),
            "n_api_calls": (len(texts) + EMBED_MAX_BATCH - 1) // EMBED_MAX_BATCH,
        },
        usage_details={"input": total_input_tokens, "output": 0},
    )
    return vectors

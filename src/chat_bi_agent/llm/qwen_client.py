"""通义千问 (DashScope) chat + embedding 封装。

输入输出都用纯 Python 类型，不向外暴露 SDK 的响应对象。

两条路径用的是不同 SDK，失败语义也因此不同：
- `chat()` 走 **OpenAI 兼容端点**（原因见 DASHSCOPE_COMPATIBLE_BASE_URL 处注释），
  失败抛 openai 的异常（APIConnectionError / AuthenticationError / …）。
- `embed()` 仍走 dashscope 原生 TextEmbedding，失败抛 RuntimeError。

`preflight_chat_model()` 供跑批入口做启动自检：模型配错时在第 1 次真实调用前就报错，
而不是让一轮十几分钟的评测跑到一半才炸。
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
import openai  # noqa: E402
import requests  # noqa: E402
from dashscope import TextEmbedding  # noqa: E402
from langfuse import get_client  # noqa: E402

from chat_bi_agent.config import (  # noqa: E402
    CHAT_MODEL,
    DEFAULT_TEMPERATURE,
    EMBED_DIM,
    EMBED_MODEL,
)
from chat_bi_agent.obs.span_kind import observe_llm  # noqa: E402

__all__ = ["CHAT_MODEL", "EMBED_MODEL", "EMBED_DIM", "ChatResult", "chat", "embed"]


@dataclass
class ChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int


# DashScope 的 OpenAI 兼容端点。chat 走这里而非原生 Generation.call，因为阿里把不同
# 世代的模型放在不同原生端点上：qwen3.7-max 只在 text-generation 端点，
# qwen3.7-max-2026-06-08 只在 multimodal-generation 端点，两者互斥；而兼容端点四个
# 模型全部吃得下。且没有任何 API 能查「某模型该走哪个端点」，只能靠撞 400 试出来，
# 所以必须收敛到唯一一条能覆盖所有模型的路径。
DASHSCOPE_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_CHAT_CLIENT: "openai.OpenAI | None" = None


def _chat_client() -> "openai.OpenAI":
    """惰性构造并复用兼容端点客户端（复用连接池，避免每次调用重建）。"""
    global _CHAT_CLIENT
    if _CHAT_CLIENT is None:
        _CHAT_CLIENT = openai.OpenAI(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            base_url=DASHSCOPE_COMPATIBLE_BASE_URL,
            max_retries=0,  # 重试由 _call_with_retry 统一负责，避免两层退避叠加
        )
    return _CHAT_CLIENT


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
    # dashscope 侧（embed 仍走 TextEmbedding）
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    # openai 侧（chat 走兼容端点）。APITimeoutError 继承自 APIConnectionError，
    # 列前者只为可读性。**换 SDK 时漏掉这两行 = 重试静默失效**：调用照跑、异常照抛，
    # 只是不再重试，而这段重试注释里记着它救回过 45+ 分钟的跑批。
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,  # 5xx 属服务端瞬时故障；4xx（配额/鉴权）不在此列，快速失败
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


def _sub_token_count(details, field: str) -> int | None:
    """从 *_tokens_details 里取一个整数字段；缺失/非整数一律当没有。

    MagicMock 和 SDK 版本差异都可能让这里拿到非 int，写进 usage_details 会污染
    服务端统计，所以只认真正的 int。
    """
    value = getattr(details, field, None) if details is not None else None
    return value if isinstance(value, int) else None


def _usage_details(usage) -> dict[str, int]:
    """拼 Langfuse usage_details。

    兼容端点用 prompt_tokens/completion_tokens，与 dashscope 原生的
    input_tokens/output_tokens 不同名。照抄旧字段名不会报错，只会让 Langfuse
    的成本统计恒为 0——又一处静默失效，所以在此显式映射。

    **必须显式给 total**：2026-09-02 实测本地 Langfuse v3，服务端把所有非 total
    键求和当作 total。而 reasoning_tokens 是 completion 的子集、cached_tokens 是
    prompt 的子集，不给 total 的话 353 会变成 692——token 凭空翻倍且不报错。
    （改用嵌套 prompt_tokens_details 则不双计，但细分数据会被静默丢弃。）

    细分维度值得记：当前 flash 模型实测 reasoning 占 completion 的 97%，
    只看 output 完全看不出钱花在思维链上。
    """
    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    details: dict[str, int] = {
        "input": prompt_tokens,
        "output": completion_tokens,
        "total": prompt_tokens + completion_tokens,
    }
    cached = _sub_token_count(getattr(usage, "prompt_tokens_details", None), "cached_tokens")
    if cached is not None:
        details["input_cached_tokens"] = cached
    reasoning = _sub_token_count(
        getattr(usage, "completion_tokens_details", None), "reasoning_tokens"
    )
    if reasoning is not None:
        details["output_reasoning_tokens"] = reasoning
    return details


@observe_llm(as_type="generation", name="qwen_chat")
def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = DEFAULT_TEMPERATURE,
) -> ChatResult:
    """单轮聊天调用。低 temperature 适合 NL2SQL。"""
    _ensure_api_key()
    resp = _call_with_retry(
        _chat_client().chat.completions.create,
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    message = resp.choices[0].message
    get_client().update_current_generation(
        model=CHAT_MODEL,
        model_parameters={"temperature": temperature},
        usage_details=_usage_details(resp.usage),
    )
    # 推理模型（如 qwen3.7-max-2026-06-08）会另外给一个 reasoning_content 字段，
    # 里面是思维链。SQL 解析只要最终答案，thinking 不参与，故只取 content。
    text_content = message.content or ""
    return ChatResult(
        content=text_content,
        prompt_tokens=resp.usage.prompt_tokens,
        completion_tokens=resp.usage.completion_tokens,
    )


def preflight_chat_model() -> None:
    """启动自检：确认 CHAT_MODEL 在兼容端点上真的可调通。

    动机是 2026-08-26 那次——模型换成 qwen3.7-max-2026-06-08 后，失败发生在跑批
    第 1 题，此前已白跑了 schema embedding 建索引；而 DashScope 给的错误是
    "url error, please check url"，把人往 URL 配置方向引，实际与 URL 无关。
    一次几十 token 的探活换掉这些，很划算。

    失败一律转成 RuntimeError 并带上模型名与排查方向，不让原始错误信息误导人。
    """
    try:
        _ensure_api_key()
        _chat_client().chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": "ok"}],
            max_tokens=1,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise RuntimeError(
            f"CHAT_MODEL={CHAT_MODEL!r} 在兼容端点上不可调通：{type(exc).__name__}: {exc}\n"
            f"排查方向：模型名是否拼错、该模型是否对本账号开通、免费额度是否已耗尽。"
            f"（端点：{DASHSCOPE_COMPATIBLE_BASE_URL}）"
        ) from exc


# 超过 10 条会被 DashScope 拒掉：
#   InternalError.Algo.InvalidParameter: batch size is invalid,
#   it should not be larger than 10
EMBED_MAX_BATCH = 10


@observe_llm(as_type="embedding", name="qwen_embed")
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
        # embedding 的 resp.usage 是 dict，只有 total_tokens
        # （chat 已改走兼容端点，用的是 prompt_tokens/completion_tokens，与此无关）
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

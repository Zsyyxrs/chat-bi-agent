"""chat() 走 DashScope 的 OpenAI 兼容端点。

起因（2026-08-26）：把模型换成 qwen3.7-max-2026-06-08 后，跑批第 1 题就死在
`InvalidParameter: url error`。实测三条路径后确认，阿里把不同世代的模型放在不同
端点上，而 `Generation.call` 硬绑在 `/api/v1/services/aigc/text-generation/generation`：

    模型                        Generation  MultiModal  兼容端点
    qwen3.7-max-2026-06-08          ✗           ✓          ✓
    qwen3.7-max（历史 baseline）     ✓           ✗          ✓
    qwen-max / qwen-plus            ✓           ✗          ✓

前两条互斥——换任一原生路径都会废掉另一批模型，只有兼容端点能统一。且没有任何 API
能查「某模型该走哪个端点」，只能靠撞 400 试，所以必须收敛到一条路径。

`embed()` 仍走 dashscope TextEmbedding（工作正常，不在本次范围），因此重试的异常
白名单要同时覆盖两套 SDK 的异常类型。
"""

from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
import requests

from chat_bi_agent.llm import qwen_client


def _ok_completion(content="SELECT 1", prompt_tokens=11, completion_tokens=7):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return resp


@pytest.fixture
def create():
    """把兼容端点客户端换成 mock，交出 chat.completions.create 供断言。"""
    with (
        patch("chat_bi_agent.llm.qwen_client._chat_client") as cli,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        yield cli.return_value.chat.completions.create


def test_chat_returns_content_and_token_counts(create):
    create.return_value = _ok_completion()

    result = qwen_client.chat("sys", "user")

    assert result.content == "SELECT 1"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 7


def test_chat_sends_system_and_user_as_plain_strings(create):
    """兼容端点收的是纯字符串 content，不是 multimodal 的 [{"text": ...}]。"""
    create.return_value = _ok_completion()

    qwen_client.chat("你是 SQL 专家", "查询余额")

    messages = create.call_args.kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "你是 SQL 专家"},
        {"role": "user", "content": "查询余额"},
    ]


def test_chat_passes_timeout(create):
    """超时必须真传下去——参数名写错会静默走 SDK 默认值，正是本仓库栽过的坑。"""
    create.return_value = _ok_completion()

    qwen_client.chat("sys", "user")

    kwargs = create.call_args.kwargs
    assert kwargs["timeout"] == qwen_client.REQUEST_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "exc",
    [
        openai.APIConnectionError(request=httpx.Request("POST", "http://x")),
        openai.APITimeoutError(request=httpx.Request("POST", "http://x")),
    ],
)
def test_chat_retries_openai_transient_errors(exc, create):
    """换 SDK 后若不重映射异常类型，重试会**静默失效**——注释里记着它救回过 45+ 分钟跑批。"""
    create.side_effect = [exc, _ok_completion()]

    assert qwen_client.chat("sys", "user").content == "SELECT 1"
    assert create.call_count == 2


def test_chat_gives_up_after_retry_budget(create):
    create.side_effect = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    with pytest.raises(openai.APIConnectionError):
        qwen_client.chat("sys", "user")
    assert create.call_count == qwen_client.MAX_TRANSIENT_RETRIES + 1


def test_auth_error_is_not_retried(create):
    """鉴权错误重试没意义，必须快速失败（沿用原有取舍）。"""
    create.side_effect = openai.AuthenticationError(
        "bad key",
        response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
        body=None,
    )

    with pytest.raises(openai.AuthenticationError):
        qwen_client.chat("sys", "user")
    assert create.call_count == 1


def test_embed_still_retries_dashscope_transient_errors():
    """embed 仍走 dashscope，两套 SDK 的异常白名单必须并存。"""
    with (
        patch("chat_bi_agent.llm.qwen_client.TextEmbedding") as te,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        ok = MagicMock()
        ok.status_code = 200
        ok.usage = {"total_tokens": 1}
        ok.output = {"embeddings": [{"embedding": [0.1]}]}
        te.call.side_effect = [requests.exceptions.ReadTimeout("boom"), ok]

        assert qwen_client.embed(["a"]) == [[0.1]]
        assert te.call.call_count == 2


def test_preflight_raises_actionable_error_when_model_unreachable(create):
    """跑批前自检：模型配错时要当场报错并说清是哪个模型。

    2026-08-26 的实际教训——模型 ID 与端点不匹配时，失败发生在跑批第 1 题，
    此前已白跑了 schema embedding 建索引等一堆准备工作；而错误信息
    （"url error, please check url"）还把人往 URL 配置上引。
    """
    create.side_effect = openai.NotFoundError(
        "model not found",
        response=httpx.Response(404, request=httpx.Request("POST", "http://x")),
        body=None,
    )

    with pytest.raises(RuntimeError, match="qwen3|模型|CHAT_MODEL"):
        qwen_client.preflight_chat_model()


def test_preflight_passes_silently_when_model_works(create):
    create.return_value = _ok_completion(content="ok")

    qwen_client.preflight_chat_model()  # 不抛即通过

    assert create.call_count == 1

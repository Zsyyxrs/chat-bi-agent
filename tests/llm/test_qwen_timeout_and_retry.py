"""qwen_client 的超时参数与瞬时故障重试。

两个真实教训：
1. commit 93c5b96 传的是 `timeout=60`，但 DashScope SDK 读的是 `request_timeout`
   （见 dashscope.common.constants.REQUEST_TIMEOUT_KEYWORD）。参数名不对 = 静默失效，
   实跑照样按 300s 默认值挂着。没有契约测试就发现不了。
2. 34 题的 eval 跑 10 分钟，第 33 题一次瞬时超时就把整轮结果全丢——两轮跑批因此报废。
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from chat_bi_agent.llm import qwen_client


def _ok_chat_resp():
    resp = MagicMock()
    resp.status_code = 200
    resp.output.choices = [MagicMock(message=MagicMock(content="SELECT 1"))]
    resp.usage = MagicMock(input_tokens=1, output_tokens=1)
    return resp


def _ok_embed_resp(texts):
    resp = MagicMock()
    resp.status_code = 200
    resp.usage = {"total_tokens": len(texts)}
    resp.output = {"embeddings": [{"embedding": [0.1]} for _ in texts]}
    return resp


def test_chat_passes_request_timeout_not_timeout():
    """必须用 SDK 认的 request_timeout；写成 timeout 会被静默忽略。"""
    with (
        patch("chat_bi_agent.llm.qwen_client.Generation") as gen,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
    ):
        gen.call.return_value = _ok_chat_resp()
        qwen_client.chat("sys", "user")
        kw = gen.call.call_args.kwargs
        assert "request_timeout" in kw, "SDK 只认 request_timeout"
        assert kw["request_timeout"] == qwen_client.REQUEST_TIMEOUT_SECONDS


def test_embed_passes_request_timeout():
    """embed 此前压根没设超时，走 300s 默认值。"""
    with (
        patch("chat_bi_agent.llm.qwen_client.TextEmbedding") as te,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
    ):
        te.call.side_effect = lambda **kw: _ok_embed_resp(kw["input"])
        qwen_client.embed(["a"])
        assert te.call.call_args.kwargs["request_timeout"] == qwen_client.REQUEST_TIMEOUT_SECONDS


def test_chat_retries_transient_timeout_then_succeeds():
    with (
        patch("chat_bi_agent.llm.qwen_client.Generation") as gen,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        gen.call.side_effect = [requests.exceptions.ReadTimeout("boom"), _ok_chat_resp()]
        out = qwen_client.chat("sys", "user")
        assert out.content == "SELECT 1"
        assert gen.call.call_count == 2


def test_embed_retries_transient_timeout():
    with (
        patch("chat_bi_agent.llm.qwen_client.TextEmbedding") as te,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        te.call.side_effect = [
            requests.exceptions.ConnectionError("boom"),
            _ok_embed_resp(["a"]),
        ]
        assert len(qwen_client.embed(["a"])) == 1
        assert te.call.call_count == 2


def test_retry_gives_up_after_budget():
    with (
        patch("chat_bi_agent.llm.qwen_client.Generation") as gen,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        gen.call.side_effect = requests.exceptions.ReadTimeout("boom")
        with pytest.raises(requests.exceptions.ReadTimeout):
            qwen_client.chat("sys", "user")
        assert gen.call.call_count == qwen_client.MAX_TRANSIENT_RETRIES + 1


def test_quota_error_is_not_retried():
    """配额/鉴权错误重试没意义，必须快速失败。"""
    resp = MagicMock()
    resp.status_code = 429
    resp.code = "AllocationQuota.FreeTierOnly"
    resp.message = "Free quota exhausted."
    with (
        patch("chat_bi_agent.llm.qwen_client.Generation") as gen,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
        patch("chat_bi_agent.llm.qwen_client.time.sleep"),
    ):
        gen.call.return_value = resp
        with pytest.raises(RuntimeError, match="quota"):
            qwen_client.chat("sys", "user")
        assert gen.call.call_count == 1

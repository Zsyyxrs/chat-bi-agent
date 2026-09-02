"""usage_details 的形状必须防住 Langfuse 服务端的求和规则。

2026-09-02 实测（本地 Langfuse v3）：服务端把 usage_details 里**所有非 total 键
求和**当作 total。而 reasoning_tokens 是 completion 的子集、cached_tokens 是
prompt 的子集，所以：

    {input, output, 细分键}          → total 353 变 692，token 凭空翻倍
    {input, output, 嵌套 details}    → 不双计，但细分数据被静默丢弃
    {input, output, total, 细分键}   → total 正确且细分保留   ← 只有这个对

两种错法都不报错，只是数字悄悄错——同本文件既有注释里那个「照抄旧字段名会让成本
恒为 0」的坑是同一类。因此显式 total 是硬要求，不是可选优化。
"""

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.llm import qwen_client


def _completion(prompt_tokens=11, completion_tokens=342, cached=5, reasoning=334):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="SELECT 1"))]
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.prompt_tokens_details = (
        MagicMock(cached_tokens=cached) if cached is not None else None
    )
    usage.completion_tokens_details = (
        MagicMock(reasoning_tokens=reasoning) if reasoning is not None else None
    )
    resp.usage = usage
    return resp


@pytest.fixture
def captured():
    """跑一次 chat()，交出传给 update_current_generation 的 usage_details。"""
    def _run(resp):
        with (
            patch("chat_bi_agent.llm.qwen_client._chat_client") as cli,
            patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
            patch("chat_bi_agent.llm.qwen_client.get_client") as gc,
            patch("chat_bi_agent.llm.qwen_client.time.sleep"),
        ):
            cli.return_value.chat.completions.create.return_value = resp
            qwen_client.chat("sys", "user")
            return gc.return_value.update_current_generation.call_args.kwargs["usage_details"]
    return _run


def test_显式_total_防住服务端求和(captured):
    ud = captured(_completion())
    assert ud["input"] == 11
    assert ud["output"] == 342
    # 没有显式 total 的话服务端会把细分键一并加进去 → 692
    assert ud["total"] == 353, "必须显式给 total，否则细分键会被服务端计入总量"


def test_细分维度被记录(captured):
    ud = captured(_completion(cached=5, reasoning=334))
    assert ud["input_cached_tokens"] == 5
    assert ud["output_reasoning_tokens"] == 334


def test_细分字段缺失时不写入空键(captured):
    ud = captured(_completion(cached=None, reasoning=None))
    assert "input_cached_tokens" not in ud
    assert "output_reasoning_tokens" not in ud
    assert ud["total"] == 353

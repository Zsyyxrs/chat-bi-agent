"""qwen_client.embed 的分批逻辑。

DashScope TextEmbedding 单次调用最多 10 条（超了报
`InternalError.Algo.InvalidParameter: batch size is invalid, it should not be
larger than 10`）。embed() 必须自己切块，调用方不该关心这个上限——
SchemaLoader.build_index() 正好卡在 10 条表文档上，MetricRouter 要 embed 32 条
alias，都会踩到。
"""

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.llm import qwen_client


def _fake_resp(texts):
    """按输入条数造一个 DashScope 风格的成功响应，向量里编码原文序号。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.usage = {"total_tokens": len(texts)}
    resp.output = {"embeddings": [{"embedding": [float(t)]} for t in texts]}
    return resp


@pytest.fixture
def mock_dashscope():
    with (
        patch("chat_bi_agent.llm.qwen_client.TextEmbedding") as mock_te,
        patch("chat_bi_agent.llm.qwen_client._ensure_api_key"),
        patch("chat_bi_agent.llm.qwen_client.get_client"),
    ):
        mock_te.call.side_effect = lambda **kw: _fake_resp(kw["input"])
        yield mock_te


def test_embed_under_limit_makes_single_call(mock_dashscope):
    out = qwen_client.embed([str(i) for i in range(10)])
    assert mock_dashscope.call.call_count == 1
    assert len(out) == 10


def test_embed_over_limit_is_chunked(mock_dashscope):
    """32 条 alias（MetricRouter 的真实规模）应切成 4 批，每批 <= 10。"""
    out = qwen_client.embed([str(i) for i in range(32)])
    assert mock_dashscope.call.call_count == 4
    for call in mock_dashscope.call.call_args_list:
        assert len(call.kwargs["input"]) <= 10
    assert len(out) == 32


def test_embed_preserves_input_order_across_chunks(mock_dashscope):
    """跨批次结果必须按原顺序拼回——错位会让 alias 对上错误的 metric。"""
    texts = [str(i) for i in range(25)]
    out = qwen_client.embed(texts)
    assert [v[0] for v in out] == [float(i) for i in range(25)]


def test_embed_empty_input_makes_no_call(mock_dashscope):
    out = qwen_client.embed([])
    assert out == []
    assert mock_dashscope.call.call_count == 0

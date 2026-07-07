"""langfuse_feedback.submit_user_feedback 单测：mock Langfuse client。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chat_bi_agent.llm.langfuse_feedback import submit_user_feedback


def test_empty_trace_id_returns_false():
    assert submit_user_feedback(trace_id="", value=1.0) is False


def test_bad_value_raises():
    with pytest.raises(ValueError):
        submit_user_feedback(trace_id="abc", value=0.5)


def test_success_calls_create_score_with_expected_args():
    mock_client = MagicMock()
    with patch("chat_bi_agent.llm.langfuse_setup.get_client", return_value=mock_client):
        ok = submit_user_feedback(trace_id="t1", value=1.0, comment="looks right")
    assert ok is True
    mock_client.create_score.assert_called_once()
    _, kwargs = mock_client.create_score.call_args
    assert kwargs["trace_id"] == "t1"
    assert kwargs["name"] == "user_feedback"
    assert kwargs["value"] == 1.0
    assert kwargs["comment"] == "looks right"
    mock_client.flush.assert_called_once()


def test_thumbs_down_uses_value_zero():
    mock_client = MagicMock()
    with patch("chat_bi_agent.llm.langfuse_setup.get_client", return_value=mock_client):
        submit_user_feedback(trace_id="t1", value=0.0)
    _, kwargs = mock_client.create_score.call_args
    assert kwargs["value"] == 0.0


def test_client_init_failure_returns_false():
    with patch(
        "chat_bi_agent.llm.langfuse_setup.get_client",
        side_effect=RuntimeError("no keys"),
    ):
        assert submit_user_feedback(trace_id="t1", value=1.0) is False


def test_create_score_error_returns_false():
    mock_client = MagicMock()
    mock_client.create_score.side_effect = RuntimeError("api down")
    with patch("chat_bi_agent.llm.langfuse_setup.get_client", return_value=mock_client):
        assert submit_user_feedback(trace_id="t1", value=1.0) is False

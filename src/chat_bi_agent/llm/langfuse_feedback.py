"""用户反馈落到 Langfuse trace 的封装。

设计原则：
- **不阻塞主流程**：Langfuse 不可用 / trace_id 无效 → 只 log 不 raise
- **一次只落一个 trace**：调用方拿 P1AgentResult.trace_id 直接传进来
- **score name 固定为 "user_feedback"**：值 1.0 = 👍，0.0 = 👎，与 bootstrap_prod_pool
  --source langfuse 里过滤逻辑对齐

用法：
    from chat_bi_agent.llm.langfuse_feedback import submit_user_feedback
    ok = submit_user_feedback(trace_id="abc123", value=1.0, comment="正确回答了 owner 关系")
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SCORE_NAME = "user_feedback"


def submit_user_feedback(
    trace_id: str,
    value: float,
    comment: str | None = None,
) -> bool:
    """把用户反馈作为 score 挂到指定 Langfuse trace。

    Args:
        trace_id: agent.run() 返回的 trace_id
        value: 1.0 = 👍 pass, 0.0 = 👎 fail
        comment: 可选自由文本

    Returns:
        True = 成功提交，False = 失败（Langfuse 未配置 / trace_id 空 / API 错）
    """
    if not trace_id:
        logger.warning("submit_user_feedback: trace_id 为空，跳过")
        return False
    if value not in (0.0, 1.0):
        raise ValueError(f"value 必须是 0.0 或 1.0，收到 {value!r}")

    try:
        from chat_bi_agent.llm.langfuse_setup import get_client
    except Exception as e:  # pragma: no cover
        logger.warning(f"submit_user_feedback: langfuse_setup 不可用 ({e})")
        return False

    try:
        client = get_client()
    except Exception as e:
        logger.warning(f"submit_user_feedback: Langfuse client 初始化失败 ({e})")
        return False

    try:
        client.create_score(
            trace_id=trace_id,
            name=SCORE_NAME,
            value=value,
            data_type="NUMERIC",
            comment=comment or "",
        )
        client.flush()
        return True
    except Exception as e:
        logger.warning(f"submit_user_feedback: create_score 失败 ({e})")
        return False

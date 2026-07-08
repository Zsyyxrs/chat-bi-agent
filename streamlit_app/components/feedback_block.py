"""共享反馈按钮组件：👍/👎 → Langfuse score(user_feedback) → 挂到当前 trace。

设计：
- 三个 tab（P1/P2/P3）共用一个 render_feedback_block(result_obj, tab_key)
- 每 trace 一次反馈就够（同会话记 st.session_state["<tab>_feedback"][trace_id]）
- SQL/report 失败或无 trace_id 时不显示按钮
- score name 统一 user_feedback（bootstrap_prod_pool 用它过滤 → 只有 P1 trace 会
  被拉进 Q-SQL 池，P2/P3 的 user_feedback 服务于回归测试集/满意度看板）
"""

from __future__ import annotations

import streamlit as st

from chat_bi_agent.llm.langfuse_feedback import submit_user_feedback


def render_feedback_block(
    trace_id: str | None,
    tab_key: str,
    *,
    is_valid: bool = True,
    invalid_hint: str = "结果无效，反馈按钮不可用",
) -> None:
    """在结果块底部渲染反馈按钮。

    Args:
        trace_id: agent.run() 返回的 langfuse trace_id
        tab_key: "p1" / "p2" / "p3"，用于 button key 避免重复
        is_valid: 结果是否有效（例如 SQL 未失败、report 非空）——False 时只显示提示
        invalid_hint: is_valid=False 时的说明文本
    """
    if not is_valid:
        st.caption(f"💡 {invalid_hint}")
        return
    if not trace_id:
        st.caption("💡 Langfuse trace_id 缺失，反馈按钮不可用（检查 Langfuse 是否已配置）")
        return

    session_key = f"{tab_key}_last_feedback"
    feedback_map: dict = st.session_state.setdefault(session_key, {})
    already = feedback_map.get(trace_id)
    if already:
        st.caption("👍 已标记（进入 pool 候选）" if already == "1" else "👎 已标记（进入回归集）")
        return

    st.markdown("**这条结果对你有帮助吗？**")
    col_up, col_down, _ = st.columns([1, 1, 6])
    with col_up:
        if st.button("👍 有用", key=f"{tab_key}_thumb_up_{trace_id}"):
            if submit_user_feedback(trace_id, value=1.0, comment=f"ui {tab_key} thumbs up"):
                feedback_map[trace_id] = "1"
                st.success("反馈已记录")
                st.rerun()
            else:
                st.warning("反馈提交失败（Langfuse 未就绪？）")
    with col_down:
        if st.button("👎 不对", key=f"{tab_key}_thumb_down_{trace_id}"):
            if submit_user_feedback(trace_id, value=0.0, comment=f"ui {tab_key} thumbs down"):
                feedback_map[trace_id] = "0"
                st.success("反馈已记录，将纳入回归测试集")
                st.rerun()
            else:
                st.warning("反馈提交失败（Langfuse 未就绪？）")

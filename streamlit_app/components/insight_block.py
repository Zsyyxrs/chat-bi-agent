"""文字洞察块（markdown）。"""

import streamlit as st


def render_insight_block(text: str | None, *, title: str = "分析洞察") -> None:
    st.markdown(f"##### {title}")
    if not text:
        st.info("（无叙事输出）")
        return
    st.markdown(text)

"""Chat-BI Agent Streamlit UI 入口。

启动：
    streamlit run streamlit_app/app.py
"""

from dotenv import load_dotenv

load_dotenv()

import streamlit as st  # noqa: E402

from streamlit_app.tabs.p1_nl2sql import render_p1_tab  # noqa: E402
from streamlit_app.tabs.p2_analysis import render_p2_tab  # noqa: E402
from streamlit_app.tabs.p3_rca import render_p3_tab  # noqa: E402

st.set_page_config(
    page_title="Chat-BI Agent",
    page_icon="🏦",
    layout="wide",
)


def _ensure_call_counter() -> dict:
    if "dashscope_call_counter" not in st.session_state:
        st.session_state["dashscope_call_counter"] = {"count": 0}
    return st.session_state["dashscope_call_counter"]


def _render_sidebar(counter: dict) -> None:
    with st.sidebar:
        st.markdown("### Chat-BI Agent")
        st.caption("银行 BI 自助分析 Demo")
        st.markdown("---")
        st.markdown("#### 本会话用量")
        st.metric("DashScope 调用次数", counter.get("count", 0))
        st.caption("注意：免费额度有限，避免短时间内大量提问。")
        st.markdown("---")
        st.markdown("#### 三个阶段")
        st.markdown("- **P1**：NL → SQL\n- **P2**：多步分析\n- **P3**：RCA 归因")


def main() -> None:
    counter = _ensure_call_counter()
    _render_sidebar(counter)

    st.title("Chat-BI Agent")
    tab1, tab2, tab3 = st.tabs(["P1 · NL2SQL", "P2 · 多步分析", "P3 · RCA 归因"])
    with tab1:
        render_p1_tab(counter)
    with tab2:
        render_p2_tab(counter)
    with tab3:
        render_p3_tab(counter)


main()

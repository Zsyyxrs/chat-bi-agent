"""SQL 代码块渲染。"""

import streamlit as st


def render_sql_block(sql: str | None, *, title: str = "SQL") -> None:
    st.markdown(f"##### {title}")
    if not sql:
        st.info("（无 SQL 输出）")
        return
    st.code(sql, language="sql")

"""数据表渲染，同时返回 DataFrame 供下游图表复用。"""

import pandas as pd
import streamlit as st


def render_dataframe_block(
    rows: list[dict] | None,
    *,
    title: str = "结果数据",
) -> pd.DataFrame | None:
    st.markdown(f"##### {title}")
    if not rows:
        st.info("（无数据）")
        return None
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)
    st.caption(f"共 {len(df)} 行 × {len(df.columns)} 列")
    return df

"""图表块：自动推断 chart_type + 用户下拉覆盖。"""

import pandas as pd
import streamlit as st

from chat_bi_agent.viz.chart_inference import ChartSpec, infer_chart_spec
from chat_bi_agent.viz.plotly_renderer import render

_CHART_OPTIONS: list[str] = ["自动", "line", "bar", "pie", "scatter", "kpi", "table"]


def _render_kpi(df: pd.DataFrame, spec: ChartSpec) -> None:
    # KPI：1 行 N 数值列展示为多张 st.metric 卡片
    cols = st.columns(min(len(df.columns), 4))
    for i, col in enumerate(df.columns):
        val = df[col].iloc[0]
        if isinstance(val, float):
            val_str = f"{val:,.2f}"
        elif isinstance(val, int):
            val_str = f"{val:,}"
        else:
            val_str = str(val)
        cols[i % len(cols)].metric(label=col, value=val_str)


def render_chart_block(df: pd.DataFrame | None, *, key: str) -> None:
    st.markdown("##### 图表")
    if df is None or df.empty:
        st.info("（无数据可绘制）")
        return

    auto_spec = infer_chart_spec(df)
    choice = st.selectbox(
        "图表类型",
        options=_CHART_OPTIONS,
        index=0,
        key=f"{key}_chart_type",
        help=f"自动推断：{auto_spec.chart_type}",
    )

    if choice == "自动":
        spec = auto_spec
    else:
        spec = ChartSpec(
            chart_type=choice,  # type: ignore[arg-type]
            x=auto_spec.x,
            y=auto_spec.y,
            group=auto_spec.group,
        )

    if spec.chart_type == "table":
        st.caption("（按表格展示，见上方数据表）")
        return

    if spec.chart_type == "kpi":
        if df.shape[0] != 1:
            st.warning("KPI 仅适用于单行结果，已降级为表格")
            return
        _render_kpi(df, spec)
        return

    try:
        fig = render(df, spec)
    except Exception as e:
        st.warning(f"无法以 {spec.chart_type} 类型渲染，已降级为表格：{e}")
        return
    if fig is None:
        st.caption("（无图可绘）")
        return
    st.plotly_chart(fig, use_container_width=True)

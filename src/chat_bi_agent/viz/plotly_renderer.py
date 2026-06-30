"""Render a pandas DataFrame as a plotly Figure given a ChartSpec.

kpi/table 不返回 Figure（None），由 streamlit 层用 st.metric / st.dataframe 渲染。
"""

import pandas as pd
import plotly.graph_objects as go

from chat_bi_agent.viz.chart_inference import ChartSpec

SAMPLE_THRESHOLD = 5000


def _maybe_sample(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) > SAMPLE_THRESHOLD:
        return df.sample(SAMPLE_THRESHOLD, random_state=42).reset_index(drop=True)
    return df


def render(df: pd.DataFrame, spec: ChartSpec) -> go.Figure | None:
    if spec.chart_type in ("kpi", "table"):
        return None

    df = _maybe_sample(df)
    fig = go.Figure()

    if spec.chart_type == "line":
        fig.add_trace(go.Scatter(x=df[spec.x], y=df[spec.y], mode="lines+markers", name=spec.y))
        if spec.group and spec.group in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df[spec.x],
                    y=df[spec.group],
                    mode="lines+markers",
                    name=spec.group,
                )
            )

    elif spec.chart_type == "bar":
        fig.add_trace(go.Bar(x=df[spec.x], y=df[spec.y], name=spec.y))
        if spec.group and spec.group in df.columns:
            fig.add_trace(go.Bar(x=df[spec.x], y=df[spec.group], name=spec.group))
            fig.update_layout(barmode="group")

    elif spec.chart_type == "scatter":
        fig.add_trace(go.Scatter(x=df[spec.x], y=df[spec.y], mode="markers"))

    elif spec.chart_type == "pie":
        fig.add_trace(go.Pie(labels=df[spec.x], values=df[spec.y]))

    if spec.title:
        fig.update_layout(title=spec.title)

    return fig

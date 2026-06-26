"""Unit tests for plotly_renderer.render()."""
import pandas as pd
import plotly.graph_objects as go

from chat_bi_agent.viz.chart_inference import ChartSpec
from chat_bi_agent.viz.plotly_renderer import SAMPLE_THRESHOLD, render


def test_render_line_returns_figure_with_trace():
    df = pd.DataFrame({
        "stat_date": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        "balance": [100.0, 200.0],
    })
    fig = render(df, ChartSpec(chart_type="line", x="stat_date", y="balance"))
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_render_bar_returns_figure():
    df = pd.DataFrame({"channel": ["A", "B"], "amount": [10, 20]})
    fig = render(df, ChartSpec(chart_type="bar", x="channel", y="amount"))
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_render_scatter_returns_figure():
    df = pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]})
    fig = render(df, ChartSpec(chart_type="scatter", x="x", y="y"))
    assert isinstance(fig, go.Figure)
    assert fig.data[0].type == "scatter"


def test_render_pie_returns_figure():
    df = pd.DataFrame({"channel": ["A", "B", "C"], "amount": [10, 20, 30]})
    fig = render(df, ChartSpec(chart_type="pie", x="channel", y="amount"))
    assert isinstance(fig, go.Figure)
    assert fig.data[0].type == "pie"


def test_render_kpi_returns_none():
    df = pd.DataFrame({"total": [42.0]})
    fig = render(df, ChartSpec(chart_type="kpi", y="total"))
    assert fig is None


def test_render_table_returns_none():
    df = pd.DataFrame({"a": [1, 2]})
    fig = render(df, ChartSpec(chart_type="table"))
    assert fig is None


def test_render_samples_large_dataframe(monkeypatch):
    big = pd.DataFrame({
        "x": list(range(SAMPLE_THRESHOLD + 100)),
        "y": list(range(SAMPLE_THRESHOLD + 100)),
    })
    fig = render(big, ChartSpec(chart_type="scatter", x="x", y="y"))
    assert isinstance(fig, go.Figure)
    # 采样后点数应 == SAMPLE_THRESHOLD
    assert len(fig.data[0].x) == SAMPLE_THRESHOLD


def test_render_grouped_bar_produces_multiple_traces():
    df = pd.DataFrame({
        "channel": ["A", "B"],
        "deposit": [10, 20],
        "loan": [5, 15],
    })
    fig = render(
        df,
        ChartSpec(chart_type="bar", x="channel", y="deposit", group="loan"),
    )
    assert isinstance(fig, go.Figure)
    # 双数值列 → 两个 trace
    assert len(fig.data) == 2

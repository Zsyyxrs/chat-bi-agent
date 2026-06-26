"""Unit tests for chart_inference."""
import pandas as pd

from chat_bi_agent.viz.chart_inference import ChartSpec, infer_chart_spec


def test_empty_df_returns_table():
    df = pd.DataFrame()
    spec = infer_chart_spec(df)
    assert spec.chart_type == "table"


def test_single_value_returns_kpi():
    df = pd.DataFrame({"total_balance": [1234567.0]})
    spec = infer_chart_spec(df)
    assert spec.chart_type == "kpi"
    assert spec.y == "total_balance"


def test_one_row_multi_numeric_returns_kpi():
    df = pd.DataFrame({"sum": [100.0], "avg": [50.0], "count": [2]})
    spec = infer_chart_spec(df)
    assert spec.chart_type == "kpi"


def test_datetime_plus_numeric_returns_line():
    df = pd.DataFrame({
        "stat_date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "balance": [100.0, 150.0, 120.0],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "line"
    assert spec.x == "stat_date"
    assert spec.y == "balance"


def test_datetime_string_column_with_date_name_returns_line():
    """启发式：列名命中 date/time/month/day 也判 datetime."""
    df = pd.DataFrame({
        "month": ["2026-01", "2026-02", "2026-03"],
        "amount": [10.0, 20.0, 30.0],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "line"
    assert spec.x == "month"


def test_one_cat_one_numeric_small_returns_bar():
    df = pd.DataFrame({
        "channel": ["手机银行", "网银", "柜面", "ATM"],
        "amount": [100.0, 80.0, 50.0, 30.0],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "bar"
    assert spec.x == "channel"
    assert spec.y == "amount"


def test_one_cat_one_numeric_large_returns_bar():
    df = pd.DataFrame({
        "branch": [f"网点{i}" for i in range(20)],
        "balance": list(range(20)),
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "bar"


def test_one_cat_multi_numeric_returns_bar_grouped():
    df = pd.DataFrame({
        "channel": ["A", "B", "C"],
        "deposit": [10, 20, 30],
        "loan": [5, 15, 25],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "bar"
    assert spec.x == "channel"
    assert spec.group is not None  # 多数值列分组


def test_two_numeric_no_cat_returns_scatter():
    df = pd.DataFrame({
        "age": [25, 30, 35, 40, 45],
        "balance": [10.0, 20.0, 30.0, 40.0, 50.0],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "scatter"


def test_unrecognized_shape_returns_table():
    # 3 类别列，无数值 → 没有可绘制规则，兜底 table
    df = pd.DataFrame({
        "a": ["x", "y", "z"],
        "b": ["p", "q", "r"],
        "c": ["m", "n", "o"],
    })
    spec = infer_chart_spec(df)
    assert spec.chart_type == "table"


def test_chartspec_is_dataclass():
    spec = ChartSpec(chart_type="bar", x="dim", y="val")
    assert spec.x == "dim"
    assert spec.group is None

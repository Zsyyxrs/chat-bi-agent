"""Rule-based chart type inference from a pandas DataFrame."""

from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype, is_numeric_dtype

ChartType = Literal["line", "bar", "scatter", "kpi", "pie", "table"]

_DATETIME_NAME_HINTS = ("date", "time", "month", "day", "year", "week")


@dataclass
class ChartSpec:
    chart_type: ChartType
    x: str | None = None
    y: str | None = None
    group: str | None = None
    title: str | None = None


def _classify_columns(
    df: pd.DataFrame,
) -> dict[str, Literal["datetime", "numeric", "categorical"]]:
    out: dict[str, Literal["datetime", "numeric", "categorical"]] = {}
    for col in df.columns:
        series = df[col]
        if is_datetime64_any_dtype(series):
            out[col] = "datetime"
            continue
        if is_numeric_dtype(series):
            out[col] = "numeric"
            continue
        name_lower = str(col).lower()
        if any(h in name_lower for h in _DATETIME_NAME_HINTS):
            out[col] = "datetime"
            continue
        out[col] = "categorical"
    return out


def infer_chart_spec(df: pd.DataFrame) -> ChartSpec:
    if df.empty or df.shape[1] == 0:
        return ChartSpec(chart_type="table")

    n_rows = df.shape[0]
    types = _classify_columns(df)
    numeric_cols = [c for c, t in types.items() if t == "numeric"]
    datetime_cols = [c for c, t in types.items() if t == "datetime"]
    cat_cols = [c for c, t in types.items() if t == "categorical"]

    # Rule 1: 1 行 → KPI
    if n_rows == 1:
        if numeric_cols:
            return ChartSpec(chart_type="kpi", y=numeric_cols[0])
        return ChartSpec(chart_type="table")

    # Rule 2: datetime + numeric → line
    if datetime_cols and numeric_cols:
        x = datetime_cols[0]
        y = numeric_cols[0]
        group = numeric_cols[1] if len(numeric_cols) > 1 else None
        return ChartSpec(chart_type="line", x=x, y=y, group=group)

    # Rule 3: 1 类别 + 1 数值 → bar
    if len(cat_cols) == 1 and len(numeric_cols) == 1:
        return ChartSpec(chart_type="bar", x=cat_cols[0], y=numeric_cols[0])

    # Rule 4: 1 类别 + ≥2 数值 → bar 多系列
    if len(cat_cols) == 1 and len(numeric_cols) >= 2:
        return ChartSpec(
            chart_type="bar",
            x=cat_cols[0],
            y=numeric_cols[0],
            group=numeric_cols[1],
        )

    # Rule 5: 2 数值无类别 → scatter
    if not cat_cols and not datetime_cols and len(numeric_cols) == 2:
        return ChartSpec(chart_type="scatter", x=numeric_cols[0], y=numeric_cols[1])

    # 兜底
    return ChartSpec(chart_type="table")

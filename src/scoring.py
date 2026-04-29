"""フェーズ1: 定量スコアリングロジック.

Lightプランの2年分のヒストリカルデータを前提とする。サンプル数が
少ないため、統計的な z-score ではなく単純平均との差分で評価する。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _to_float(value: Any) -> float | None:
    if value in (None, "", "－", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def seasonality_progress(statements: pd.DataFrame, metric: str = "NetSales") -> dict[str, Any]:
    """過去の同一四半期における通期進捗率と比較し、今期進捗の強さを評価する.

    Args:
        statements: J-Quants `/fins/statements` のレスポンスを DataFrame 化したもの.
        metric: 評価対象の指標. NetSales / OperatingProfit / Profit のいずれか.

    Returns:
        current_quarter: 今期の決算区分 ("1Q"|"2Q"|"3Q")
        current_progress: 今期の累積進捗率 (実績 / 会社通期予想)
        historical_mean: 過去同四半期における累積進捗率 (実績 / FY実績) の平均
        historical_n: 過去サンプル数 (Lightプランは最大2)
        delta: current_progress - historical_mean. 正なら例年比で進捗良好.
    """
    if statements.empty:
        raise ValueError("statements is empty")

    df = statements[statements["TypeOfDocument"].str.contains("FinancialStatements", na=False)].copy()
    df = df.sort_values("DisclosedDate")
    if df.empty:
        raise ValueError("決算短信レコードが見つからない")

    latest = df.iloc[-1]
    current_period = latest["TypeOfCurrentPeriod"]
    if current_period == "FY":
        raise ValueError("最新開示が通期決算のため進捗評価対象外")

    current_actual = _to_float(latest.get(metric))
    current_forecast = _to_float(latest.get(f"Forecast{metric}"))
    if current_actual is None or not current_forecast:
        raise ValueError(f"{metric} の実績または会社予想が欠損")
    current_progress = current_actual / current_forecast

    historical_ratios: list[float] = []
    quarter_rows = df[df["TypeOfCurrentPeriod"] == current_period]
    fy_rows = df[df["TypeOfCurrentPeriod"] == "FY"]
    for _, q_row in quarter_rows.iloc[:-1].iterrows():
        fy_end = q_row.get("CurrentFiscalYearEndDate")
        if not fy_end:
            continue
        match = fy_rows[fy_rows["CurrentFiscalYearEndDate"] == fy_end]
        if match.empty:
            continue
        q_val = _to_float(q_row.get(metric))
        fy_val = _to_float(match.iloc[0].get(metric))
        if q_val is None or not fy_val:
            continue
        historical_ratios.append(q_val / fy_val)

    historical_mean = sum(historical_ratios) / len(historical_ratios) if historical_ratios else None
    delta = current_progress - historical_mean if historical_mean is not None else None

    return {
        "metric": metric,
        "current_quarter": current_period,
        "current_progress": current_progress,
        "historical_mean": historical_mean,
        "historical_n": len(historical_ratios),
        "delta": delta,
    }


if __name__ == "__main__":
    from jquants_client import JQuantsClient

    client = JQuantsClient()
    stmts = client.statements(code="7203")
    for metric in ("NetSales", "OperatingProfit", "Profit"):
        try:
            result = seasonality_progress(stmts, metric=metric)
        except ValueError as exc:
            print(f"[{metric}] skip: {exc}")
            continue
        print(f"[{metric}] {result}")

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


def valuation_score(statements: pd.DataFrame, daily_quotes: pd.DataFrame) -> dict[str, Any]:
    """PEG／ヒストリカルPER／配当利回りで割安度を評価する.

    Lightプランは株価・財務とも直近2年分が上限のため、ヒストリカル中央値も
    その範囲で算出する。値はあくまで開示時点の Forecast を用い、株式分割の
    遡及調整は行わない（投資家が当時実際に観測した値を再現する）.

    Returns:
        current_per: 現在の予想PER (latest Close / ForecastEPS)
        historical_per_median: 過去2年の予想PER中央値
        per_below_historical: current_per < historical_per_median
        eps_growth_rate: (今期予想EPS - 前期実績EPS) / 前期実績EPS
        peg: current_per / (eps_growth_rate * 100). 成長率<=0 なら None
        peg_under_one: peg < 1.0
        current_dividend_yield: ForecastDividendPerShareAnnual / Close
        historical_dividend_yield_mean: 過去2年の配当利回り平均
        yield_above_historical: 現在の利回りが過去平均より高いか（下値支持シグナル）
    """
    if statements.empty or daily_quotes.empty:
        raise ValueError("statements or daily_quotes is empty")

    fs = statements[statements["TypeOfDocument"].str.contains("FinancialStatements", na=False)].copy()
    fs = fs.sort_values("DisclosedDate").reset_index(drop=True)
    if fs.empty:
        raise ValueError("決算短信レコードが見つからない")

    quotes = daily_quotes.sort_values("Date").reset_index(drop=True)

    latest_stmt = fs.iloc[-1]
    forecast_eps = _to_float(latest_stmt.get("ForecastEarningsPerShare"))
    forecast_div = _to_float(latest_stmt.get("ForecastDividendPerShareAnnual"))
    latest_close = _to_float(quotes.iloc[-1].get("Close"))

    current_per = latest_close / forecast_eps if latest_close and forecast_eps and forecast_eps > 0 else None
    current_div_yield = forecast_div / latest_close if latest_close and forecast_div is not None else None

    fy_rows = fs[fs["TypeOfCurrentPeriod"] == "FY"]
    prev_eps = _to_float(fy_rows.iloc[-1].get("EarningsPerShare")) if not fy_rows.empty else None
    eps_growth_rate: float | None = None
    if forecast_eps is not None and prev_eps and prev_eps > 0:
        eps_growth_rate = (forecast_eps - prev_eps) / prev_eps

    peg: float | None = None
    if current_per and eps_growth_rate is not None and eps_growth_rate > 0:
        peg = current_per / (eps_growth_rate * 100)

    historical_pers: list[float] = []
    historical_yields: list[float] = []
    for _, row in quotes.iterrows():
        date = row["Date"]
        applicable = fs[fs["DisclosedDate"] <= date]
        if applicable.empty:
            continue
        ref = applicable.iloc[-1]
        close = _to_float(row.get("Close"))
        eps = _to_float(ref.get("ForecastEarningsPerShare"))
        div = _to_float(ref.get("ForecastDividendPerShareAnnual"))
        if close and eps and eps > 0:
            historical_pers.append(close / eps)
        if close and div is not None:
            historical_yields.append(div / close)

    historical_per_median = float(pd.Series(historical_pers).median()) if historical_pers else None
    historical_div_yield_mean = float(pd.Series(historical_yields).mean()) if historical_yields else None

    return {
        "current_per": current_per,
        "historical_per_median": historical_per_median,
        "per_below_historical": current_per < historical_per_median
        if current_per is not None and historical_per_median is not None
        else None,
        "eps_growth_rate": eps_growth_rate,
        "peg": peg,
        "peg_under_one": peg is not None and peg < 1.0,
        "current_dividend_yield": current_div_yield,
        "historical_dividend_yield_mean": historical_div_yield_mean,
        "yield_above_historical": current_div_yield > historical_div_yield_mean
        if current_div_yield is not None and historical_div_yield_mean is not None
        else None,
    }


if __name__ == "__main__":
    from jquants_client import JQuantsClient

    client = JQuantsClient()
    code = "7203"
    stmts = client.statements(code=code)
    quotes = client.daily_quotes(code=code)

    print("=== Seasonality progress ===")
    for metric in ("NetSales", "OperatingProfit", "Profit"):
        try:
            result = seasonality_progress(stmts, metric=metric)
        except ValueError as exc:
            print(f"[{metric}] skip: {exc}")
            continue
        print(f"[{metric}] {result}")

    print("\n=== Valuation ===")
    try:
        print(valuation_score(stmts, quotes))
    except ValueError as exc:
        print(f"skip: {exc}")

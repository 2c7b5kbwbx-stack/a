"""決算当日の直前モメンタムチェック CLI.

設計の絶対ルール: 土日のスコアがどれほど優秀であっても、決算前の数日〜数週間で
株価が異常急騰している場合は「期待の織り込み済み」としてエントリーを見送る.

J-Quants Light は引け後配信のため、当日寄り後・大引け前の判定では
直近営業日終値ベースの確認となる。決算日 14:30〜14:50 の手動実行を想定.

使い方:
    python src/preflight_check.py 7203
    python src/preflight_check.py 7203 --threshold 8.0 --windows 5 10 20
"""

from __future__ import annotations

import argparse
from typing import Any

from jquants_client import JQuantsClient


def momentum_check(
    code: str,
    threshold_pct: float = 10.0,
    windows: tuple[int, ...] = (5, 10, 20),
) -> dict[str, Any]:
    """直近 N 営業日のリターンを算出し、急騰なら NO-GO 判定を返す.

    Args:
        code: 銘柄コード
        threshold_pct: 急騰判定の閾値 (%). 任意のウィンドウで超過すれば NO-GO.
        windows: 比較する営業日数のタプル.

    Returns:
        decision: "GO" | "NO-GO"
        flagged_windows: 閾値超過したウィンドウ
        windows: 全ウィンドウのリターン詳細
    """
    client = JQuantsClient()
    quotes = client.daily_quotes(code=code)
    if quotes.empty:
        raise ValueError(f"no daily quotes for {code}")

    quotes = quotes.sort_values("Date").reset_index(drop=True)
    latest = quotes.iloc[-1]
    latest_close = float(latest["Close"])

    window_results: list[dict[str, Any]] = []
    flagged: list[int] = []
    for w in windows:
        if len(quotes) <= w:
            continue
        past = quotes.iloc[-(w + 1)]
        past_close = float(past["Close"])
        if past_close <= 0:
            continue
        ret_pct = (latest_close - past_close) / past_close * 100
        is_flagged = ret_pct >= threshold_pct
        if is_flagged:
            flagged.append(w)
        window_results.append(
            {
                "window_days": w,
                "from_date": str(past["Date"].date()),
                "to_date": str(latest["Date"].date()),
                "from_close": past_close,
                "to_close": latest_close,
                "return_pct": ret_pct,
                "flagged": is_flagged,
            }
        )

    return {
        "code": code,
        "decision": "NO-GO" if flagged else "GO",
        "threshold_pct": threshold_pct,
        "flagged_windows": flagged,
        "windows": window_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="決算当日の直前モメンタムチェック")
    parser.add_argument("code", help="銘柄コード (例: 7203)")
    parser.add_argument("--threshold", type=float, default=10.0, help="急騰判定閾値 (%) デフォルト 10.0")
    parser.add_argument("--windows", type=int, nargs="+", default=[5, 10, 20], help="参照する営業日数")
    args = parser.parse_args()

    result = momentum_check(args.code, threshold_pct=args.threshold, windows=tuple(args.windows))

    print(f"=== {result['code']} 決算前モメンタムチェック ===")
    print(f"判定: {result['decision']}  (閾値 +{result['threshold_pct']}%)")
    print()
    print(f"{'window':>8}  {'from':>12}  {'to':>12}  {'from_close':>12}  {'to_close':>12}  {'return%':>10}  flag")
    for w in result["windows"]:
        flag_mark = "[!]" if w["flagged"] else "   "
        print(
            f"{w['window_days']:>8}  {w['from_date']:>12}  {w['to_date']:>12}  "
            f"{w['from_close']:>12.1f}  {w['to_close']:>12.1f}  {w['return_pct']:>10.2f}  {flag_mark}"
        )
    if result["decision"] == "NO-GO":
        print()
        print("[!] 期待の織り込み済みと判定。土日スコアに関わらずエントリー見送り.")


if __name__ == "__main__":
    main()

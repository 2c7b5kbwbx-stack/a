"""フェーズ2-①: アナリストレポートからの期待値ペナルティ判定.

Claude API でレポートテキストを解析し、自社株買い／増配の織り込みと
全体センチメントを JSON で抽出。市場の期待が膨らみすぎている銘柄に
減点（ペナルティ）を与える。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """あなたは日本株のセルサイド・アナリストレポートを解析し、
市場が織り込んでいる「期待値」を JSON で抽出する専門家です。
以下の観点を厳密に評価し、レポート本文に明示的・暗黙的に示されているもののみを True と判定します。
推測や一般論で True にしてはいけません。

- buyback_expected: 自社株買いの実施が予測・期待されているか
- dividend_increase_expected: 増配が予測・期待されているか
- sentiment_score: 総合センチメント (0.0=極めて弱気, 0.5=中立, 1.0=極めて強気)
- rationale: 上記判定の根拠を 1〜2 文で簡潔に
"""


class ExpectationFlags(BaseModel):
    buyback_expected: bool = Field(description="自社株買いが予測・期待されているか")
    dividend_increase_expected: bool = Field(description="増配が予測・期待されているか")
    sentiment_score: float = Field(ge=0.0, le=1.0, description="総合センチメント (0=弱気, 1=強気)")
    rationale: str = Field(description="判定根拠 (1〜2 文)")


def analyze_report(report_text: str, broker: str, client: anthropic.Anthropic | None = None) -> dict[str, Any]:
    """単一レポートを解析しフラグとセンチメントを返す."""
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"以下は {broker} のアナリストレポートです。指定スキーマで JSON 出力してください。\n\n---\n{report_text}\n---",
            }
        ],
        output_format=ExpectationFlags,
    )
    parsed: ExpectationFlags = response.parsed_output
    return {
        "broker": broker,
        "buyback_expected": parsed.buyback_expected,
        "dividend_increase_expected": parsed.dividend_increase_expected,
        "sentiment_score": parsed.sentiment_score,
        "rationale": parsed.rationale,
    }


def expectation_penalty(
    reports_dir: str | Path,
    sentiment_high_threshold: float = 0.75,
    sentiment_penalty: float = 30.0,
    flag_penalty: float = 20.0,
) -> dict[str, Any]:
    """指定ディレクトリ内の全レポートを解析し、期待値ペナルティ合計を返す.

    ファイル名規則:
        <broker>_<銘柄コード>_<日付>.txt  例: mizuho_7203_20260420.txt
    `<broker>` は最初の `_` までを証券会社名として扱う.

    減点ロジック:
        - センチメント >= sentiment_high_threshold のレポートごとに `sentiment_penalty` 減点
        - buyback_expected が True のレポートごとに `flag_penalty` 減点
        - dividend_increase_expected が True のレポートごとに `flag_penalty` 減点
    """
    reports_path = Path(reports_dir)
    files = sorted(p for p in reports_path.glob("*.txt") if p.is_file())
    if not files:
        raise ValueError(f"no .txt reports in {reports_dir}")

    client = anthropic.Anthropic()
    results: list[dict[str, Any]] = []
    total_penalty = 0.0

    for path in files:
        broker = path.stem.split("_", 1)[0]
        text = path.read_text(encoding="utf-8")
        analysis = analyze_report(text, broker=broker, client=client)
        results.append({"file": path.name, **analysis})

        if analysis["sentiment_score"] >= sentiment_high_threshold:
            total_penalty += sentiment_penalty
        if analysis["buyback_expected"]:
            total_penalty += flag_penalty
        if analysis["dividend_increase_expected"]:
            total_penalty += flag_penalty

    return {
        "total_penalty": total_penalty,
        "report_count": len(results),
        "results": results,
    }


if __name__ == "__main__":
    import json

    reports_dir = os.environ.get("REPORTS_DIR", "./reports")
    summary = expectation_penalty(reports_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

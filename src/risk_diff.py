"""フェーズ2-③: リスクファクター差分 (Diff) 検知.

前回と今回のアナリストレポート/説明資料を Claude API で比較し、
為替・採用・設備投資などのトーン変化を抽出。ネガティブ方向への
変化は来期ガイダンス弱気の前兆として減点する.
"""

from __future__ import annotations

from typing import Any, Literal

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """あなたは日本株のレポート/決算説明資料を比較し、リスクファクターのトーン変化を
抽出する専門家です。以下のルールを厳守してください:

- topic: リスク領域 (例: 為替, 採用, 設備投資, 受注, コスト, 競合, 規制, 顧客集中度)
- direction:
    - worsened: 既存リスクが悪化/警戒強化
    - improved: 既存リスクが改善/解消
    - new: 今回新たに登場したネガティブ要因
    - removed: 前回あったネガティブ要因が今回消えた
- severity: 0.0 (軽微) 〜 1.0 (重大)
- evidence: 判定根拠となる前回・今回それぞれの該当箇所 (それぞれ 1 文程度)

両方のテキストに明示的な記述があるもののみ抽出。推測や一般論は禁止。
変化が無い項目は出力しないこと。
"""


class RiskDiff(BaseModel):
    topic: str = Field(description="リスク領域")
    direction: Literal["worsened", "improved", "new", "removed"]
    severity: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(description="前回/今回の該当箇所")


class RiskDiffList(BaseModel):
    diffs: list[RiskDiff]


def detect_risk_diff(
    previous_text: str,
    current_text: str,
    client: anthropic.Anthropic | None = None,
) -> list[RiskDiff]:
    """前回と今回のテキストを比較し、リスクトーンの変化を抽出する."""
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
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
                "content": (
                    "以下、前回 (previous) と今回 (current) のレポートです。"
                    "リスクファクターのトーン変化を指定スキーマで JSON 出力してください。\n\n"
                    f"=== previous ===\n{previous_text}\n\n=== current ===\n{current_text}"
                ),
            }
        ],
        output_format=RiskDiffList,
    )
    parsed: RiskDiffList = response.parsed_output
    return parsed.diffs


def risk_diff_penalty(
    previous_text: str,
    current_text: str,
    weight: float = 25.0,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    """ネガティブ方向の差分 (worsened / new) を減点としてスコア化する.

    Returns:
        diffs: 全差分リスト
        penalty: ネガティブ差分の severity 合計 × weight
    """
    diffs = detect_risk_diff(previous_text, current_text, client=client)
    negative_severity = sum(d.severity for d in diffs if d.direction in ("worsened", "new"))
    return {
        "diffs": [d.model_dump() for d in diffs],
        "negative_severity_sum": negative_severity,
        "penalty": negative_severity * weight,
    }


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    if len(sys.argv) < 3:
        print("usage: python risk_diff.py <previous.txt> <current.txt>")
        raise SystemExit(1)
    prev = Path(sys.argv[1]).read_text(encoding="utf-8")
    cur = Path(sys.argv[2]).read_text(encoding="utf-8")
    result = risk_diff_penalty(prev, cur)
    print(json.dumps(result, ensure_ascii=False, indent=2))

"""フェーズ2-②: 決算説明資料 PDF からの先行 KPI 抽出.

J-Quants では取得できない非財務 KPI (受注残・ARR・MAU・客単価など) を
Claude API で抽出し、前期 PDF と突き合わせて成長率を算出する.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """あなたは日本企業の決算説明資料を解析し、非財務 KPI を構造化する専門家です。
以下のルールを厳守してください:

- 売上高/営業利益/純利益/EPS/配当などの財務数値は抽出しない (J-Quants で取得済みのため)
- 受注残・ARR・MAU/DAU・契約社数・客単価・店舗数・稼働率などの先行指標を中心に抽出
- 数値は資料に明示されているもののみ。推計値・他社比較値は抽出しない
- 単位は資料記載のまま (例: "億円", "千件", "%", "店舗")
- period は資料の対象決算期 (例: "2026Q1", "2026FY")
"""


class KPI(BaseModel):
    metric: str = Field(description="KPI 名 (例: 受注残, ARR, 月間アクティブユーザー)")
    value: float = Field(description="数値")
    unit: str = Field(description="単位 (例: 億円, 千件, %)")
    period: str = Field(description="対象期 (例: 2026Q1)")


class KPIList(BaseModel):
    kpis: list[KPI]


def _pdf_block(pdf_path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }


def extract_kpis(pdf_path: str | Path, client: anthropic.Anthropic | None = None) -> list[KPI]:
    """単一 PDF から先行 KPI を抽出する."""
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
                "content": [
                    _pdf_block(Path(pdf_path)),
                    {"type": "text", "text": "この決算説明資料から非財務 KPI を抽出し、指定スキーマで JSON 出力してください。"},
                ],
            }
        ],
        output_format=KPIList,
    )
    parsed: KPIList = response.parsed_output
    return parsed.kpis


def kpi_growth(
    latest_pdf: str | Path,
    previous_pdf: str | Path,
    client: anthropic.Anthropic | None = None,
) -> list[dict[str, Any]]:
    """前期と当期の PDF を比較し、共通 KPI の成長率を返す.

    指標名の正規化は `metric` を小文字化＋空白除去で行う簡易マッチング.
    単位が異なる場合は成長率を None とする.
    """
    client = client or anthropic.Anthropic()
    latest = extract_kpis(latest_pdf, client=client)
    previous = extract_kpis(previous_pdf, client=client)

    def _key(name: str) -> str:
        return "".join(name.lower().split())

    prev_map = {_key(k.metric): k for k in previous}
    diffs: list[dict[str, Any]] = []
    for cur in latest:
        prev = prev_map.get(_key(cur.metric))
        if prev is None:
            continue
        same_unit = cur.unit == prev.unit
        growth = (cur.value - prev.value) / prev.value if same_unit and prev.value else None
        diffs.append(
            {
                "metric": cur.metric,
                "unit": cur.unit,
                "latest_period": cur.period,
                "latest_value": cur.value,
                "previous_period": prev.period,
                "previous_value": prev.value,
                "growth_rate": growth,
                "unit_mismatch": not same_unit,
            }
        )
    return diffs


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: python earnings_kpi.py <latest_pdf> <previous_pdf>")
        raise SystemExit(1)
    diffs = kpi_growth(sys.argv[1], sys.argv[2])
    print(json.dumps(diffs, ensure_ascii=False, indent=2))

"""J-Quants API クライアント (Lightプラン対応)

Lightプランで利用可能なエンドポイントのみを実装する。
データ遡及範囲は直近2年（=8四半期）に制限される点に注意。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.jquants.com/v1"


@dataclass
class JQuantsClient:
    refresh_token: str = field(default_factory=lambda: os.environ.get("JQUANTS_REFRESH_TOKEN", ""))
    _id_token: str | None = None
    _id_token_expires_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.refresh_token:
            raise RuntimeError("JQUANTS_REFRESH_TOKEN is not set")

    def _get_id_token(self) -> str:
        # IDトークンは24時間有効。期限の5分前に再発行。
        if self._id_token and time.time() < self._id_token_expires_at - 300:
            return self._id_token
        resp = requests.post(
            f"{BASE_URL}/token/auth_refresh",
            params={"refreshtoken": self.refresh_token},
            timeout=30,
        )
        resp.raise_for_status()
        self._id_token = resp.json()["idToken"]
        self._id_token_expires_at = time.time() + 24 * 3600
        return self._id_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._get_id_token()}"}
        merged: dict[str, Any] = {}
        pagination_key: str | None = None
        while True:
            req_params = dict(params or {})
            if pagination_key:
                req_params["pagination_key"] = pagination_key
            resp = requests.get(f"{BASE_URL}{path}", params=req_params, headers=headers, timeout=60)
            resp.raise_for_status()
            body = resp.json()
            for key, value in body.items():
                if key == "pagination_key":
                    continue
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                else:
                    merged[key] = value
            pagination_key = body.get("pagination_key")
            if not pagination_key:
                break
        return merged

    def listed_info(self, code: str | None = None) -> pd.DataFrame:
        """上場銘柄一覧を取得。"""
        params = {"code": code} if code else None
        body = self._get("/listed/info", params=params)
        return pd.DataFrame(body.get("info", []))

    def statements(self, code: str) -> pd.DataFrame:
        """個別銘柄の財務情報（四半期）を取得。Lightは直近2年分。"""
        body = self._get("/fins/statements", params={"code": code})
        df = pd.DataFrame(body.get("statements", []))
        if not df.empty:
            df["DisclosedDate"] = pd.to_datetime(df["DisclosedDate"])
        return df

    def announcement(self) -> pd.DataFrame:
        """翌営業日の決算発表予定銘柄を取得。"""
        body = self._get("/fins/announcement")
        return pd.DataFrame(body.get("announcement", []))

    def daily_quotes(self, code: str, from_: str | None = None, to: str | None = None) -> pd.DataFrame:
        """日次株価（四本値）を取得。Lightは直近2年分。"""
        params: dict[str, Any] = {"code": code}
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        body = self._get("/prices/daily_quotes", params=params)
        df = pd.DataFrame(body.get("daily_quotes", []))
        if not df.empty:
            df["Date"] = pd.to_datetime(df["Date"])
        return df


if __name__ == "__main__":
    # 疎通確認: 7203 (トヨタ) の銘柄情報と直近の財務を取得
    client = JQuantsClient()
    info = client.listed_info(code="7203")
    print("=== Listed Info ===")
    print(info.to_string(index=False))
    stmts = client.statements(code="7203")
    print(f"\n=== Statements (rows={len(stmts)}) ===")
    if not stmts.empty:
        cols = ["DisclosedDate", "TypeOfDocument", "NetSales", "OperatingProfit", "Profit"]
        available = [c for c in cols if c in stmts.columns]
        print(stmts[available].head(10).to_string(index=False))

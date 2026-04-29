"""Notion から過去メモを抽出するモジュール.

Notion の全文検索 API で銘柄コードを含むページを取得し、対象データベース
配下のものに限定して本文テキストを返す。
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from notion_client import Client

load_dotenv()


def _normalize_id(notion_id: str) -> str:
    return notion_id.replace("-", "").lower()


def _extract_page_text(client: Client, page_id: str) -> str:
    parts: list[str] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            btype = block.get("type")
            content = block.get(btype, {})
            if not isinstance(content, dict):
                continue
            for rt in content.get("rich_text", []):
                txt = rt.get("plain_text") if isinstance(rt, dict) else None
                if txt:
                    parts.append(txt)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return "\n".join(parts)


def fetch_memos(stock_code: str) -> list[dict[str, str]]:
    """指定銘柄コードを含むメモを Notion DB から取得.

    Returns:
        [{"page_id": str, "title": str, "text": str}, ...]
    """
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        raise RuntimeError("NOTION_TOKEN / NOTION_DATABASE_ID is not set")

    client = Client(auth=token)
    target_db = _normalize_id(db_id)

    memos: list[dict[str, str]] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"query": stock_code, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.search(**kwargs)
        for result in resp.get("results", []):
            if result.get("object") != "page":
                continue
            parent = result.get("parent", {})
            parent_db = parent.get("database_id")
            if not parent_db or _normalize_id(parent_db) != target_db:
                continue
            title = ""
            for prop in result.get("properties", {}).values():
                if prop.get("type") == "title":
                    title = "".join(rt.get("plain_text", "") for rt in prop.get("title", []))
                    break
            memos.append(
                {
                    "page_id": result["id"],
                    "title": title,
                    "text": _extract_page_text(client, result["id"]),
                }
            )
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    return memos


if __name__ == "__main__":
    code = os.environ.get("TEST_STOCK_CODE", "7203")
    for memo in fetch_memos(code):
        print(f"--- {memo['title']} ({memo['page_id']}) ---")
        print(memo["text"][:300])
        print()

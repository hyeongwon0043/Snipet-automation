#!/usr/bin/env python3
"""Publish today's Notion records as a GCS Pulse daily snippet.

This production-compatible variant uses only endpoints currently exposed by
https://api.1000.school/openapi.json. It reuses the Notion parsing and safety
limits from sync_notion_daily_draft.py, but publishes the organised result via
POST /daily-snippets because the private /daily-snippets/draft endpoint is not
yet available on the production API.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from urllib.parse import urlencode

from sync_notion_daily_draft import (
    DEFAULT_GCS_API_URL,
    DEFAULT_NOTION_VERSION,
    NOTION_API_URL,
    SEOUL,
    SyncError,
    bounded_env_int,
    build_notion_source,
    gcs_headers,
    list_changed_notion_pages,
    notion_headers,
    page_title,
    render_blocks,
    request_json,
    required_env,
)


def build_notion_page_source(
    *, token: str, version: str, page_id: str, target_date, max_characters: int
) -> tuple[str, int]:
    _, page = request_json(
        "GET",
        f"{NOTION_API_URL}/pages/{page_id}",
        headers=notion_headers(token, version),
    )
    edited_raw = str(page.get("last_edited_time", ""))
    if not edited_raw:
        raise SyncError("Notion page response did not include last_edited_time")
    try:
        edited_at = datetime.fromisoformat(edited_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyncError("Notion returned an invalid last_edited_time") from exc
    if edited_at.astimezone(SEOUL).date() != target_date:
        return "", 0

    lines = render_blocks(token=token, version=version, parent_id=page_id)
    if not lines:
        return "", 0
    source = f"## {page_title(page)}\n" + "\n".join(lines)
    if len(source) > max_characters:
        source = source[:max_characters].rstrip() + "\n\n[Notion source truncated for safety]"
    return source, 1


def run() -> int:
    notion_token = required_env("NOTION_TOKEN")
    gcs_token = required_env("GCS_API_TOKEN")
    notion_page_id = os.environ.get("NOTION_PAGE_ID", "").strip()
    notion_data_source_id = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()
    if not notion_page_id and not notion_data_source_id:
        raise SyncError("Set NOTION_PAGE_ID or NOTION_DATA_SOURCE_ID")
    notion_version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION).strip()
    gcs_api_url = os.environ.get("GCS_API_URL", DEFAULT_GCS_API_URL).rstrip("/")
    max_pages = bounded_env_int("NOTION_MAX_PAGES", 20, minimum=1, maximum=100)
    max_characters = bounded_env_int(
        "NOTION_MAX_CHARACTERS", 20_000, minimum=500, maximum=100_000
    )
    target_date = datetime.now(SEOUL).date()
    page_data_url = (
        f"{gcs_api_url}/daily-snippets/page-data?"
        f"{urlencode({'date': target_date.isoformat()})}"
    )

    _, page_data = request_json(
        "GET", page_data_url, headers=gcs_headers(gcs_token)
    )
    if page_data.get("snippet"):
        print("A daily snippet already exists; leaving it unchanged.")
        return 0

    if notion_page_id:
        source, source_count = build_notion_page_source(
            token=notion_token,
            version=notion_version,
            page_id=notion_page_id,
            target_date=target_date,
            max_characters=max_characters,
        )
    else:
        pages = list_changed_notion_pages(
            token=notion_token,
            version=notion_version,
            data_source_id=notion_data_source_id,
            target_date=target_date,
            max_pages=max_pages,
        )
        source = build_notion_source(
            token=notion_token,
            version=notion_version,
            pages=pages,
            max_characters=max_characters,
        )
        source_count = len(pages)
    if not source:
        print("No non-empty Notion records were edited today; no snippet created.")
        return 0

    _, organized = request_json(
        "POST",
        f"{gcs_api_url}/daily-snippets/organize",
        headers=gcs_headers(gcs_token),
        payload={"content": source},
    )
    snippet_content = str(organized.get("organized_content", "")).strip()
    if not snippet_content:
        raise SyncError("GCS Pulse returned empty organised content")

    _, latest_page_data = request_json(
        "GET", page_data_url, headers=gcs_headers(gcs_token)
    )
    if latest_page_data.get("snippet"):
        print("A daily snippet was submitted during this run; leaving it unchanged.")
        return 0

    _, created = request_json(
        "POST",
        f"{gcs_api_url}/daily-snippets",
        headers=gcs_headers(gcs_token),
        payload={"content": snippet_content},
    )
    snippet_id = created.get("id")
    if not isinstance(snippet_id, int):
        raise SyncError("GCS Pulse returned an unexpected create response")

    print(f"Created daily snippet {snippet_id} from {source_count} Notion page(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SyncError as exc:
        print(f"Notion daily snippet sync failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

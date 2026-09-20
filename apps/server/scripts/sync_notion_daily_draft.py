#!/usr/bin/env python3
"""Create a private GCS Pulse daily-snippet draft from today's Notion entries.

The script is designed for a scheduled GitHub Actions run.  It reads only
pages in one shared Notion data source that were edited today in Asia/Seoul,
asks the existing GCS Pulse organiser to structure the source text, then
creates a *private* daily draft.  It never replaces an existing draft or a
saved daily snippet.

Required environment variables:
  NOTION_TOKEN            Notion integration token with read-content access
  NOTION_DATA_SOURCE_ID   The Notion data source containing daily records
  GCS_API_TOKEN           A GCS Pulse personal API token for the author

Optional environment variables:
  GCS_API_URL             Defaults to https://api.1000.school
  NOTION_VERSION          Defaults to the current supported API version
  NOTION_MAX_PAGES        Safety cap; defaults to 20 pages per run
  NOTION_MAX_CHARACTERS   Safety cap; defaults to 20000 source characters
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


NOTION_API_URL = "https://api.notion.com/v1"
DEFAULT_GCS_API_URL = "https://api.1000.school"
DEFAULT_NOTION_VERSION = "2026-03-11"
try:
    SEOUL = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError:
    # Windows Python installations can omit the IANA timezone database. Korea
    # does not observe DST, so the fixed offset is a safe local fallback.
    SEOUL = timezone(timedelta(hours=9))


class SyncError(RuntimeError):
    """A configuration or upstream API error safe to show in CI logs."""


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SyncError(f"Missing required environment variable: {name}")
    return value


def bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise SyncError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SyncError(f"{name} must be between {minimum} and {maximum}")
    return value


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **headers}
    if body is not None:
        request_headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS APIs
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
            error_json = json.loads(error_body)
            detail = error_json.get("detail") or error_json.get("message") or error_json.get("code")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = None
        message = f"{method} {url} returned HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise SyncError(message) from exc
    except URLError as exc:
        raise SyncError(f"Could not reach {url}: {exc.reason}") from exc


def notion_headers(token: str, version: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
    }


def list_changed_notion_pages(
    *,
    token: str,
    version: str,
    data_source_id: str,
    target_date: date,
    max_pages: int,
) -> list[dict[str, Any]]:
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=SEOUL)
    end = start + timedelta(days=1)
    pages: list[dict[str, Any]] = []
    cursor: str | None = None

    while len(pages) < max_pages:
        query: dict[str, Any] = {
            "filter": {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "on_or_after": start.isoformat(),
                    "before": end.isoformat(),
                },
            },
            "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
            "page_size": min(100, max_pages - len(pages)),
        }
        if cursor:
            query["start_cursor"] = cursor

        _, response = request_json(
            "POST",
            f"{NOTION_API_URL}/data_sources/{data_source_id}/query",
            headers=notion_headers(token, version),
            payload=query,
        )
        pages.extend(page for page in response.get("results", []) if page.get("object") == "page")
        cursor = response.get("next_cursor")
        if not response.get("has_more") or not cursor:
            break

    return pages[:max_pages]


def plain_text(items: Iterable[dict[str, Any]]) -> str:
    return "".join(str(item.get("plain_text", "")) for item in items)


def page_title(page: dict[str, Any]) -> str:
    for property_value in page.get("properties", {}).values():
        if property_value.get("type") == "title":
            title = plain_text(property_value.get("title", []))
            if title.strip():
                return title.strip()
    return "Untitled Notion page"


def block_to_text(block: dict[str, Any]) -> str:
    block_type = block.get("type", "")
    value = block.get(block_type, {})
    text = plain_text(value.get("rich_text", []))

    if block_type.startswith("heading_") and text:
        try:
            level = int(block_type.rsplit("_", 1)[1])
        except ValueError:
            level = 2
        return f"{'#' * min(level + 1, 6)} {text}"
    if block_type == "bulleted_list_item" and text:
        return f"- {text}"
    if block_type == "numbered_list_item" and text:
        return f"1. {text}"
    if block_type == "to_do" and text:
        return f"- [{'x' if value.get('checked') else ' '}] {text}"
    if block_type == "quote" and text:
        return f"> {text}"
    if block_type == "code" and text:
        language = value.get("language", "")
        return f"```{language}\n{text}\n```"
    if block_type == "table_row":
        cells = [plain_text(cell) for cell in value.get("cells", [])]
        return " | ".join(cell for cell in cells if cell)
    if block_type == "child_page":
        return value.get("title", "")
    return text


def list_block_children(
    *,
    token: str,
    version: str,
    block_id: str,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, str | int] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        _, response = request_json(
            "GET",
            f"{NOTION_API_URL}/blocks/{block_id}/children?{urlencode(params)}",
            headers=notion_headers(token, version),
        )
        blocks.extend(response.get("results", []))
        cursor = response.get("next_cursor")
        if not response.get("has_more") or not cursor:
            return blocks


def render_blocks(
    *,
    token: str,
    version: str,
    parent_id: str,
    depth: int = 0,
    max_depth: int = 4,
) -> list[str]:
    lines: list[str] = []
    for block in list_block_children(token=token, version=version, block_id=parent_id):
        rendered = block_to_text(block).strip()
        if rendered:
            lines.append(rendered)
        if block.get("has_children") and depth < max_depth:
            lines.extend(
                render_blocks(
                    token=token,
                    version=version,
                    parent_id=block["id"],
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )
    return lines


def build_notion_source(
    *,
    token: str,
    version: str,
    pages: list[dict[str, Any]],
    max_characters: int,
) -> str:
    sections: list[str] = []
    for page in pages:
        content_lines = render_blocks(token=token, version=version, parent_id=page["id"])
        if content_lines:
            sections.append(f"## {page_title(page)}\n" + "\n".join(content_lines))

    source = "\n\n".join(sections).strip()
    if len(source) > max_characters:
        source = source[:max_characters].rstrip() + "\n\n[Notion source truncated for safety]"
    return source


def gcs_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def run() -> int:
    notion_token = required_env("NOTION_TOKEN")
    data_source_id = required_env("NOTION_DATA_SOURCE_ID")
    gcs_token = required_env("GCS_API_TOKEN")
    notion_version = os.environ.get("NOTION_VERSION", DEFAULT_NOTION_VERSION).strip()
    gcs_api_url = os.environ.get("GCS_API_URL", DEFAULT_GCS_API_URL).rstrip("/")
    max_pages = bounded_env_int("NOTION_MAX_PAGES", 20, minimum=1, maximum=100)
    max_characters = bounded_env_int("NOTION_MAX_CHARACTERS", 20_000, minimum=500, maximum=100_000)
    target_date = datetime.now(SEOUL).date()

    # Check first: do not fetch or send Notion content when the student has
    # already started a draft or submitted today's final entry.
    _, page_data = request_json(
        "GET",
        f"{gcs_api_url}/daily-snippets/page-data?{urlencode({'date': target_date.isoformat()})}",
        headers=gcs_headers(gcs_token),
    )
    if page_data.get("snippet"):
        print("A daily snippet already exists; leaving it unchanged.")
        return 0
    if page_data.get("draft"):
        print("A daily draft already exists; leaving it unchanged.")
        return 0

    pages = list_changed_notion_pages(
        token=notion_token,
        version=notion_version,
        data_source_id=data_source_id,
        target_date=target_date,
        max_pages=max_pages,
    )
    source = build_notion_source(
        token=notion_token,
        version=notion_version,
        pages=pages,
        max_characters=max_characters,
    )
    if not source:
        print("No non-empty Notion records were edited today; no draft created.")
        return 0

    _, organized = request_json(
        "POST",
        f"{gcs_api_url}/daily-snippets/organize",
        headers=gcs_headers(gcs_token),
        payload={"content": source},
    )
    draft_content = str(organized.get("organized_content", "")).strip()
    if not draft_content:
        raise SyncError("GCS Pulse returned an empty organised draft")

    try:
        request_json(
            "POST",
            f"{gcs_api_url}/daily-snippets/draft",
            headers=gcs_headers(gcs_token),
            payload={"content": draft_content},
        )
    except SyncError as exc:
        # A second scheduled/manual run may race this one.  The server's
        # create-only endpoint is the source of truth for protecting edits.
        if "HTTP 409" not in str(exc):
            raise
        print("A daily draft was created by another run; leaving it unchanged.")
        return 0

    print(f"Created one private daily draft from {len(pages)} Notion page(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SyncError as exc:
        print(f"Notion daily draft sync failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

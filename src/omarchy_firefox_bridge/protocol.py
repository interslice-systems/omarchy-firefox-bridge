"""Validation for native and local bridge protocol objects."""
from __future__ import annotations

import base64
import binascii
import re

TITLE_LIMIT = 1024
DISPLAY_URL_LIMIT = 4096
FAVICON_LIMIT = 64 * 1024
TAB_KEYS = frozenset(
    {"tabId", "windowId", "index", "title", "displayUrl", "favicon", "active"}
)
RASTER_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)
DATA_URL = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$")


class ProtocolError(ValueError):
    """A bridge message violates the public contract."""


def valid_request_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[\x20-\x7e]{1,64}", value))


def _non_negative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{name} must be a non-negative integer")
    return value


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError(f"{name} must be a positive integer")
    return value


def _favicon(value: object) -> str:
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ProtocolError("favicon must be a string")
    match = DATA_URL.fullmatch(value)
    if not match or match.group(1).lower() not in RASTER_TYPES:
        raise ProtocolError("favicon must be a bounded raster data URL")
    if not match.group(2) or len(match.group(2)) % 4 != 0:
        raise ProtocolError("favicon base64 must be non-empty and padded")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProtocolError("favicon base64 is invalid") from error
    if len(decoded) > FAVICON_LIMIT:
        raise ProtocolError("favicon exceeds 65536 decoded bytes")
    return value


def validate_snapshot(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ProtocolError("tabs must be an array")
    validated: list[dict] = []
    seen_tab_ids: set[int] = set()
    seen_indexes: set[tuple[int, int]] = set()
    active_counts: dict[int, int] = {}
    for raw in value:
        if not isinstance(raw, dict) or frozenset(raw) != TAB_KEYS:
            raise ProtocolError("tab has missing or unknown fields")
        title = raw["title"]
        display_url = raw["displayUrl"]
        active = raw["active"]
        if not isinstance(title, str) or len(title) > TITLE_LIMIT:
            raise ProtocolError("title exceeds 1024 code points or is not a string")
        if not isinstance(display_url, str) or len(display_url) > DISPLAY_URL_LIMIT:
            raise ProtocolError("displayUrl exceeds 4096 code points or is not a string")
        if not isinstance(active, bool):
            raise ProtocolError("active must be a boolean")
        tab_id = _positive_integer(raw["tabId"], "tabId")
        window_id = _positive_integer(raw["windowId"], "windowId")
        index = _non_negative_integer(raw["index"], "index")
        if tab_id in seen_tab_ids or (window_id, index) in seen_indexes:
            raise ProtocolError("tab IDs and window indexes must be unique")
        seen_tab_ids.add(tab_id)
        seen_indexes.add((window_id, index))
        active_counts[window_id] = active_counts.get(window_id, 0) + int(active)
        validated.append(
            {
                "tabId": tab_id,
                "windowId": window_id,
                "index": index,
                "title": title,
                "displayUrl": display_url,
                "favicon": _favicon(raw["favicon"]),
                "active": active,
            }
        )
    if any(count != 1 for count in active_counts.values()):
        raise ProtocolError("each Firefox window must have exactly one active tab")
    validated.sort(key=lambda tab: (tab["windowId"], tab["index"]))
    return validated


def snapshot_ids(tabs: list[dict]) -> frozenset[tuple[int, int]]:
    return frozenset((tab["windowId"], tab["tabId"]) for tab in tabs)


def validate_activation(window_id: object, tab_id: object) -> tuple[int, int]:
    return _positive_integer(window_id, "windowId"), _positive_integer(tab_id, "tabId")


URL_LIMIT = 4096
TOPLEVEL_TITLE_LIMIT = 1024
GROUP_TITLE_LIMIT = 64

# Firefox's nine fixed tab group colours. Note "grey", not "gray": Firefox uses
# the Chromium-compatible spelling in the WebExtension API.
GROUP_COLORS = frozenset(
    {"blue", "cyan", "grey", "green", "orange", "pink", "purple", "red", "yellow"}
)

HTTP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
# The charset wsid_session_name guarantees: no ASCII controls or whitespace and
# none of . : / # -- note that * and ? DO survive it, which is safe here only
# because the extension filters group titles literally rather than as globs.
GROUP_TITLE = re.compile(r"^[^\x00-\x20\x7f.:/#]+$")


def _bounded_text(value: object, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ProtocolError(f"{name} must be a string of 1 to {limit} code points")
    if CONTROL_CHARACTERS.search(value):
        raise ProtocolError(f"{name} must not contain C0 controls or DEL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProtocolError(f"{name} must be well-formed Unicode") from error
    return value


def _open_group(value: object) -> dict:
    if not isinstance(value, dict):
        raise ProtocolError("group must be an object")
    keys = frozenset(value)
    if keys not in (frozenset({"title"}), frozenset({"title", "color"})):
        raise ProtocolError("group has missing or unknown fields")
    title = _bounded_text(value["title"], GROUP_TITLE_LIMIT, "group.title")
    if not GROUP_TITLE.fullmatch(title):
        raise ProtocolError("group.title is outside the canonical workspace charset")
    group = {"title": title}
    if "color" in value:
        color = value["color"]
        if not isinstance(color, str) or color not in GROUP_COLORS:
            raise ProtocolError("group.color must be one of the nine Firefox colours")
        group["color"] = color
    return group


def validate_open_request(value: object) -> dict:
    """Coarse validation only. The extension's WHATWG URL parser is authoritative."""
    if not isinstance(value, dict):
        raise ProtocolError("open request must be an object")
    keys = frozenset(value)
    if keys not in (
        frozenset({"url", "toplevelTitle"}),
        frozenset({"url", "toplevelTitle", "group"}),
    ):
        raise ProtocolError("open request has missing or unknown fields")
    url = _bounded_text(value["url"], URL_LIMIT, "url")
    if not HTTP_SCHEME.match(url):
        raise ProtocolError("url must use the http or https scheme")
    request = {
        "url": url,
        "toplevelTitle": _bounded_text(
            value["toplevelTitle"], TOPLEVEL_TITLE_LIMIT, "toplevelTitle"
        ),
    }
    if "group" in value:
        request["group"] = _open_group(value["group"])
    return request

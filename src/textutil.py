"""Text and URL helpers (no network, no third-party API calls)."""

from __future__ import annotations

import calendar
import html as html_lib
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser

# Tracking/noise query parameters stripped during URL normalization.
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "spm",
        "scm",
        "ref",
        "ref_src",
        "referrer",
        "_hsenc",
        "_hsmi",
        "si",
        "share_token",
    }
)

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_MARKS = "。！？!?；;\n"

# Characters allowed by XML 1.0; scraped HTML occasionally carries control bytes
# that make the feed writer raise.
_XML_INVALID_RE = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)

DEFAULT_TRUNCATE_SUFFIX = "…"


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").replace("\u3000", " ")).strip()


def sanitize_xml(text: str) -> str:
    """Drop characters that are illegal in XML 1.0 (NULs, most control bytes, lone surrogates)."""
    if not text:
        return ""
    return _XML_INVALID_RE.sub("", text)


def strip_html(fragment: str) -> str:
    """Convert an RSS summary HTML fragment into plain text."""
    if not fragment:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", fragment)
    text = _TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    return collapse_whitespace(text)


def normalize_url(url: str) -> str:
    """Normalize a URL for de-duplication.

    Lowercases scheme/host, removes default ports, drops fragments and tracking
    parameters, sorts the remaining query parameters and strips trailing slashes.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    query = urlencode(sorted(pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking_param(name: str) -> bool:
    lowered = (name or "").lower()
    return lowered in TRACKING_PARAMS or lowered.startswith("utm_")


def truncate(text: str, limit: int, suffix: str = DEFAULT_TRUNCATE_SUFFIX) -> str:
    """Truncate to at most ``limit`` characters, preferring a sentence boundary."""
    if not text:
        return ""
    text = text.strip()
    limit = int(limit)
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    cut = text[: limit - len(suffix)]
    window_start = int(len(cut) * 0.6)
    best = -1
    for mark in _SENTENCE_MARKS:
        best = max(best, cut.rfind(mark, window_start))
    if best > 0:
        cut = cut[: best + 1]
    return cut.strip() + suffix


def parse_datetime(value: Any) -> Optional[datetime]:
    """Normalize struct_time / epoch / string into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, time.struct_time):
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    if isinstance(value, tuple) and len(value) >= 6:
        try:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = date_parser.parse(raw, fuzzy=False)
        except (ValueError, OverflowError, TypeError):
            return None
        return as_utc(parsed)
    return None


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def entry_datetime(entry: dict) -> Optional[datetime]:
    """Pick a publication timestamp out of a feedparser entry."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = parse_datetime(entry.get(key))
        if parsed:
            return parsed
    for key in ("published", "updated", "created", "pubDate"):
        parsed = parse_datetime(entry.get(key))
        if parsed:
            return parsed
    return parse_datetime(entry.get("dc_date"))

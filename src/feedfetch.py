"""RSS/Atom fetching and entry parsing. A failing source never aborts the run."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable
from urllib.parse import urljoin

import feedparser

from .http import get as http_get
from .models import Article
from .textutil import entry_datetime, normalize_url, strip_html, truncate

logger = logging.getLogger(__name__)

FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
    "text/xml;q=0.9, */*;q=0.8"
)
SUMMARY_CHARS = 1000


def fetch_feed(feed_cfg: dict, extract_cfg: dict, limit: int) -> list[Article]:
    """Fetch one source and return up to ``limit`` newest entries."""
    name = str(feed_cfg.get("name") or feed_cfg.get("url") or "unknown")
    url = str(feed_cfg.get("url") or "")
    weight = float(feed_cfg.get("weight", 1.0) or 1.0)
    if not url:
        logger.warning("Feed %s has no url, skipped", name)
        return []

    response = http_get(
        url,
        timeout=extract_cfg.get("timeout", 15),
        retries=extract_cfg.get("retries", 2),
        user_agent=extract_cfg.get("user_agent", ""),
        accept=FEED_ACCEPT,
    )
    if response is None:
        logger.warning("Feed %s could not be downloaded, skipped", name)
        return []

    parsed = feedparser.parse(response.content)
    entries = list(getattr(parsed, "entries", []) or [])
    if not entries:
        logger.warning("Feed %s produced no entries (bozo=%s), skipped", name, getattr(parsed, "bozo", None))
        return []

    articles: list[Article] = []
    for entry in entries:
        link = _entry_link(entry)
        if not link:
            continue
        # Some feeds publish root-relative links; resolve them against the feed URL.
        link = urljoin(url, link)
        articles.append(
            Article(
                title=strip_html(str(entry.get("title") or "")) or "(untitled)",
                link=link,
                normalized_link=normalize_url(link),
                source_name=name,
                source_weight=weight,
                source_url=url,
                published=entry_datetime(entry),
                summary=_entry_summary(entry),
            )
        )

    articles.sort(key=lambda item: item.sort_key(), reverse=True)
    logger.info("Feed %s: %d entries parsed, keeping %d", name, len(articles), min(limit, len(articles)))
    return articles[: max(1, int(limit))]


def fetch_all(cfg: dict) -> list[Article]:
    """Fetch every configured source concurrently (bounded by extract.max_workers)."""
    feeds: list[dict] = cfg.get("feeds") or []
    if not feeds:
        logger.warning("No feeds configured")
        return []

    extract_cfg = cfg["extract"]
    per_source = int(cfg["max_items_per_source"])
    workers = max(1, int(extract_cfg.get("max_workers", 5)))
    collected: list[Article] = []

    with ThreadPoolExecutor(max_workers=min(workers, len(feeds)), thread_name_prefix="feed") as pool:
        futures = {
            pool.submit(_safe_fetch, feed, extract_cfg, per_source): feed for feed in feeds
        }
        for future, feed in futures.items():
            try:
                collected.extend(future.result())
            except Exception as exc:  # defensive: _safe_fetch already guards
                logger.warning("Feed %s failed unexpectedly: %s", feed.get("name"), exc)

    logger.info("Collected %d raw entries from %d sources", len(collected), len(feeds))
    return collected


def _safe_fetch(feed_cfg: dict, extract_cfg: dict, limit: int) -> list[Article]:
    try:
        return fetch_feed(feed_cfg, extract_cfg, limit)
    except Exception as exc:
        logger.warning("Feed %s crashed: %s", feed_cfg.get("name"), exc)
        return []


def _entry_link(entry: dict) -> str:
    link = str(entry.get("link") or "").strip()
    if link:
        return link
    for candidate in entry.get("links") or []:
        if isinstance(candidate, dict) and candidate.get("href"):
            if candidate.get("rel", "alternate") == "alternate":
                return str(candidate["href"]).strip()
    return ""


def _entry_summary(entry: dict) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw:
        contents: Iterable = entry.get("content") or []
        if isinstance(contents, list) and contents and isinstance(contents[0], dict):
            raw = contents[0].get("value") or ""
    return truncate(strip_html(str(raw)), SUMMARY_CHARS)

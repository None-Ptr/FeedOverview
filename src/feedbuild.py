"""RSS 2.0 generation with feedgen."""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from feedgen.feed import FeedGenerator

from .models import Article
from .textutil import sanitize_xml

logger = logging.getLogger(__name__)

AI_LABEL = "AI 精简版"
_PARAGRAPH_SPLIT_RE = re.compile(r"\n+")


def _esc(text: str) -> str:
    """Escape for HTML after removing characters XML cannot represent."""
    return html.escape(sanitize_xml(text or ""))


def build_note(article: Article) -> str:
    note = (
        "AI-condensed version. This text was produced by an LLM from the original article and "
        "may omit details. The original link at the end is the only authoritative source."
    )
    if article.refine_failed:
        note += " (Refinement failed for this item, so the body is a truncated excerpt of the original.)"
    elif not article.is_refined:
        note += " (Refinement was skipped, so the body is an excerpt of the original.)"
    return note


def build_description(article: Article) -> str:
    """HTML description: AI label, recommendation reason, body, source metadata, original link."""
    parts: list[str] = [f"<p><strong>【{AI_LABEL}】</strong> {_esc(build_note(article))}</p>"]

    if article.reason:
        parts.append(f"<p><strong>Why it is here:</strong> {_esc(article.reason)}</p>")

    for block in _PARAGRAPH_SPLIT_RE.split(article.effective_content or ""):
        block = block.strip()
        if block:
            parts.append(f"<p>{_esc(block)}</p>")

    published = article.published.strftime("%Y-%m-%d %H:%M UTC") if article.published else "unknown"
    parts.append(
        f"<p><strong>Source:</strong> {_esc(article.source_name)} | "
        f"<strong>Published:</strong> {published}</p>"
    )

    link_text = _esc(article.link)
    link_href = html.escape(sanitize_xml(article.link), quote=True)
    parts.append(f'<p><strong>Original link:</strong> <a href="{link_href}">{link_text}</a></p>')
    return "".join(parts)


def build_feed(articles: list[Article], cfg: dict, now: Optional[datetime] = None) -> FeedGenerator:
    now = now or datetime.now(timezone.utc)
    feed_link = sanitize_xml(str(cfg.get("feed_link") or "https://example.com/"))

    generator = FeedGenerator()
    generator.id(feed_link)
    generator.title(sanitize_xml(str(cfg.get("feed_title") or "Curated Feed")))
    generator.link(href=feed_link, rel="alternate")
    generator.description(sanitize_xml(str(cfg.get("feed_description") or "")))
    generator.language(sanitize_xml(str(cfg.get("feed_language") or "en")))
    generator.lastBuildDate(now)
    generator.generator("feed-aggregator")

    for article in articles:
        link = sanitize_xml(article.link)
        # feedgen prepends by default, which would reverse our ranking order.
        entry = generator.add_entry(order="append")
        entry.id(link)
        entry.guid(link, permalink=True)
        entry.title(sanitize_xml(article.effective_title))
        entry.link(href=link, rel="alternate")
        entry.description(build_description(article))
        entry.pubDate(article.published or now)
        if article.source_url:
            try:
                entry.source(url=sanitize_xml(article.source_url), title=sanitize_xml(article.source_name))
            except (AttributeError, TypeError):  # older/newer feedgen API differences
                pass

    return generator


def write_feed(articles: list[Article], cfg: dict, path: Optional[str] = None, now: Optional[datetime] = None) -> Path:
    """Write the feed atomically so a crash never leaves a half-written file."""
    out = Path(path or cfg.get("output_path") or "docs/feed.xml")
    out.parent.mkdir(parents=True, exist_ok=True)

    payload = build_feed(articles, cfg, now=now).rss_str(pretty=True)
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not data.lstrip().startswith(b"<?xml"):
        data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + data

    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    logger.info("Wrote %d entries to %s", len(articles), out)
    return out

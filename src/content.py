"""Article body extraction with trafilatura, falling back to the RSS summary."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

from trafilatura import extract

from .http import get as http_get
from .models import Article
from .textutil import truncate

logger = logging.getLogger(__name__)

HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def fetch_html(url: str, extract_cfg: dict) -> Optional[str]:
    """Download the page HTML as text."""
    response = http_get(
        url,
        timeout=extract_cfg.get("timeout", 15),
        retries=extract_cfg.get("retries", 2),
        user_agent=extract_cfg.get("user_agent", ""),
        accept=HTML_ACCEPT,
    )
    if response is None:
        return None
    if not response.encoding:
        response.encoding = response.apparent_encoding
    return response.text


def extract_text(html: str, url: str, extract_cfg: dict) -> Optional[str]:
    """Run trafilatura on raw HTML; returns None when nothing could be extracted.

    ``fast`` was renamed to ``no_fallback`` in newer trafilatura releases, so we
    try the documented argument first and retry with the new name.
    """
    fast = bool(extract_cfg.get("fast", True))
    common = {
        "url": url,
        "include_comments": False,
        "include_tables": False,
        "favor_recall": bool(extract_cfg.get("favor_recall", True)),
    }
    try:
        return extract(html, fast=fast, **common)
    except TypeError:
        try:
            return extract(html, no_fallback=fast, **common)
        except Exception as exc:  # noqa: BLE001 - extraction must never break the run
            logger.warning("trafilatura failed for %s: %s", url, exc)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("trafilatura failed for %s: %s", url, exc)
        return None


def enrich_article(article: Article, extract_cfg: dict) -> Article:
    """Attach the extracted body.

    When the page body cannot be extracted the article is flagged as failed and
    left without content; it is then dropped instead of being published as an
    RSS-summary stub (``summary`` is only ever a preview, not an article body).
    """
    max_chars = int(extract_cfg.get("max_content_chars", 8000))
    min_chars = int(extract_cfg.get("min_content_chars", 80))

    text: Optional[str] = None
    try:
        html = fetch_html(article.link, extract_cfg)
        if html:
            text = extract_text(html, article.link, extract_cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Body fetch crashed for %s: %s", article.link, exc)
        text = None

    if text and len(text.strip()) >= min_chars:
        article.content = truncate(text.strip(), max_chars)
        article.extraction_failed = False
        return article

    article.content = ""
    article.extraction_failed = True
    logger.warning("No extractable body for %s, dropping the article", article.link)
    return article


def enrich_all(articles: Iterable[Article], extract_cfg: dict) -> None:
    """Extract bodies concurrently. Failures are per-article and non-fatal."""
    items = list(articles)
    if not items:
        return
    workers = max(1, min(int(extract_cfg.get("max_workers", 5)), len(items)))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
        futures = {pool.submit(enrich_article, article, extract_cfg): article for article in items}
        for future, article in futures.items():
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Extraction task failed for %s: %s", article.link, exc)
                article.content = ""
                article.extraction_failed = True


def has_body(article: Article) -> bool:
    return bool(article.content.strip()) and not article.extraction_failed

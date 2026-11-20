"""Link de-duplication: in-batch dedupe plus a 30-day persistent seen cache."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import Article
from .textutil import as_utc, normalize_url

logger = logging.getLogger(__name__)

SEEN_VERSION = 1


def dedupe_articles(articles: Iterable[Article], already_seen: set[str] | None = None) -> list[Article]:
    """Keep the first occurrence of each normalized link and drop known links."""
    seen = already_seen or set()
    kept: list[Article] = []
    batch: set[str] = set()
    dropped_duplicates = 0
    dropped_seen = 0

    for article in articles:
        key = article.normalized_link or normalize_url(article.link)
        if not key:
            continue
        if key in seen:
            dropped_seen += 1
            continue
        if key in batch:
            dropped_duplicates += 1
            continue
        batch.add(key)
        article.normalized_link = key
        kept.append(article)

    logger.info(
        "Dedupe: %d kept, %d already seen, %d duplicated in this batch",
        len(kept),
        dropped_seen,
        dropped_duplicates,
    )
    return kept


class SeenStore:
    """Persistent ``normalized_link -> first seen at`` map, pruned by age."""

    def __init__(self, path: str | os.PathLike[str] = "cache/seen.json", retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self._seen: dict[str, datetime] = {}
        self._dirty = False

    def load(self) -> "SeenStore":
        if not self.path.is_file():
            logger.info("Seen cache %s does not exist yet, starting empty", self.path)
            return self
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Seen cache unreadable (%s), starting empty: %s", self.path, exc)
            return self

        raw = payload.get("seen", {}) if isinstance(payload, dict) else {}
        if isinstance(raw, list):  # tolerate a bare list of links
            raw = {link: datetime.now(timezone.utc).isoformat() for link in raw}
        if not isinstance(raw, dict):
            logger.warning("Seen cache has an unexpected shape, starting empty")
            return self

        for link, stamp in raw.items():
            moment = _parse_stamp(stamp)
            if moment is not None:
                self._seen[str(link)] = moment
        logger.info("Loaded %d seen links from %s", len(self._seen), self.path)
        return self

    @property
    def links(self) -> set[str]:
        return set(self._seen)

    def __contains__(self, link: str) -> bool:
        return link in self._seen

    def is_seen(self, link: str) -> bool:
        return link in self._seen

    def mark(self, link: str, when: datetime | None = None) -> None:
        if not link:
            return
        self._seen[link] = as_utc(when or datetime.now(timezone.utc))
        self._dirty = True

    def mark_all(self, links: Iterable[str]) -> None:
        moment = datetime.now(timezone.utc)
        for link in links:
            self.mark(link, moment)

    def prune(self, now: datetime | None = None) -> int:
        now = as_utc(now or datetime.now(timezone.utc))
        cutoff = now - timedelta(days=self.retention_days)
        before = len(self._seen)
        self._seen = {link: stamp for link, stamp in self._seen.items() if stamp >= cutoff}
        removed = before - len(self._seen)
        if removed:
            self._dirty = True
            logger.info("Pruned %d seen links older than %d days", removed, self.retention_days)
        return removed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SEEN_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seen": {
                link: stamp.isoformat(timespec="seconds")
                for link, stamp in sorted(self._seen.items())
            },
        }
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".seen-",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(handle.name, self.path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise
        self._dirty = False
        logger.info("Saved %d seen links to %s", len(self._seen), self.path)


def _parse_stamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return as_utc(parsed)
    return None

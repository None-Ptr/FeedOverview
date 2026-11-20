"""In-batch de-duplication and the persistent seen cache."""

import json
from datetime import datetime, timedelta, timezone

from src.dedup import SeenStore, dedupe_articles

from tests.helpers import make_article


class TestDedupe:
    def test_removes_duplicates_within_batch(self):
        articles = [
            make_article(link="https://example.com/a?utm_source=rss"),
            make_article(link="https://example.com/a"),
            make_article(link="https://example.com/b"),
        ]
        kept = dedupe_articles(articles)
        assert [item.link for item in kept] == [
            "https://example.com/a?utm_source=rss",
            "https://example.com/b",
        ]

    def test_skips_already_seen_links(self):
        seen = {"https://example.com/old"}
        articles = [make_article(link="https://example.com/old"), make_article(link="https://example.com/new")]
        kept = dedupe_articles(articles, seen)
        assert [item.link for item in kept] == ["https://example.com/new"]

    def test_ignores_articles_without_link(self):
        assert dedupe_articles([make_article(link="")]) == []


class TestSeenStore:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "cache" / "seen.json"
        store = SeenStore(path, retention_days=30)
        store.load()
        store.mark("https://example.com/a")
        store.save()

        reloaded = SeenStore(path, retention_days=30).load()
        assert "https://example.com/a" in reloaded
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1

    def test_prunes_entries_older_than_retention(self, tmp_path):
        path = tmp_path / "seen.json"
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        store = SeenStore(path, retention_days=30)
        store.load()
        store.mark("https://example.com/fresh", now - timedelta(days=2))
        store.mark("https://example.com/stale", now - timedelta(days=45))

        removed = store.prune(now)

        assert removed == 1
        assert "https://example.com/fresh" in store
        assert "https://example.com/stale" not in store

    def test_corrupt_cache_starts_empty(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text("{not json", encoding="utf-8")
        store = SeenStore(path).load()
        assert store.links == set()

    def test_missing_cache_starts_empty(self, tmp_path):
        store = SeenStore(tmp_path / "nope" / "seen.json").load()
        assert store.links == set()

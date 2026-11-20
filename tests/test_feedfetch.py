"""RSS/Atom parsing edge cases, with the network mocked out."""

import src.feedfetch as feedfetch_module
from src.feedfetch import fetch_feed

EXTRACT_CFG = {"timeout": 1, "retries": 0, "user_agent": "test", "max_content_chars": 8000}

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <link>https://example.com/</link>
    <item>
      <title>Relative link</title>
      <link>/posts/relative</link>
      <pubDate>Fri, 13 Nov 2026 10:00:00 +0000</pubDate>
      <description>&lt;p&gt;Summary one&lt;/p&gt;</description>
    </item>
    <item>
      <title></title>
      <link>https://example.com/no-title</link>
      <pubDate>Thu, 12 Nov 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>No link at all</title>
      <pubDate>Wed, 11 Nov 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Older</title>
      <link>https://example.com/older</link>
      <pubDate>Mon, 09 Nov 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

FEED_CFG = {"name": "Example", "url": "https://example.com/rss", "weight": 2.0}


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content


def _serve(monkeypatch, content: bytes):
    monkeypatch.setattr(feedfetch_module, "http_get", lambda *args, **kwargs: FakeResponse(content))


class TestFetchFeed:
    def test_resolves_relative_links(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        articles = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10)
        assert "https://example.com/posts/relative" in [article.link for article in articles]

    def test_untitled_entries_get_a_placeholder(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        articles = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10)
        titles = {article.title for article in articles}
        assert "(untitled)" in titles

    def test_entries_without_link_are_skipped(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        articles = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10)
        assert all(article.link for article in articles)
        assert len(articles) == 3

    def test_sorted_newest_first_and_limited(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        articles = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=2)
        assert len(articles) == 2
        assert articles[0].link.endswith("/posts/relative")
        assert articles[0].published > articles[1].published

    def test_source_metadata_is_attached(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        article = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=1)[0]
        assert article.source_name == "Example"
        assert article.source_weight == 2.0
        assert article.source_url == "https://example.com/rss"

    def test_html_summary_is_stripped(self, monkeypatch):
        _serve(monkeypatch, RSS.encode("utf-8"))
        article = fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10)[0]
        assert article.summary == "Summary one"

    def test_download_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(feedfetch_module, "http_get", lambda *args, **kwargs: None)
        assert fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10) == []

    def test_html_page_is_not_a_feed(self, monkeypatch):
        _serve(monkeypatch, b"<html><body>just a page</body></html>")
        assert fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10) == []

    def test_empty_body(self, monkeypatch):
        _serve(monkeypatch, b"")
        assert fetch_feed(FEED_CFG, EXTRACT_CFG, limit=10) == []

    def test_feed_without_url(self, monkeypatch):
        assert fetch_feed({"name": "x"}, EXTRACT_CFG, limit=10) == []

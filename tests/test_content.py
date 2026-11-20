"""trafilatura extraction; articles without a body are marked for removal."""

import src.content as content_module
from src.content import enrich_article, has_body

from tests.helpers import make_article

SUMMARY = "RSS summary that must never be published as the article body."


class TestExtraction:
    def test_uses_extracted_text(self, cfg, monkeypatch):
        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: "<html>page</html>")
        monkeypatch.setattr(
            content_module,
            "extract",
            lambda html, **kwargs: "A long extracted article body. " * 10,
        )
        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])

        assert article.content.startswith("A long extracted article body.")
        assert article.extraction_failed is False
        assert has_body(article) is True

    def test_drops_article_when_extract_returns_none(self, cfg, monkeypatch):
        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: "<html>page</html>")
        monkeypatch.setattr(content_module, "extract", lambda html, **kwargs: None)

        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])

        assert article.content == ""
        assert article.extraction_failed is True
        assert has_body(article) is False
        assert SUMMARY not in article.content

    def test_drops_article_when_extract_returns_short_text(self, cfg, monkeypatch):
        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: "<html>page</html>")
        monkeypatch.setattr(content_module, "extract", lambda html, **kwargs: "too short")

        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])

        assert article.content == ""
        assert article.extraction_failed is True

    def test_drops_article_when_page_download_fails(self, cfg, monkeypatch):
        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: None)
        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])
        assert article.content == ""
        assert article.extraction_failed is True

    def test_drops_article_when_extract_raises(self, cfg, monkeypatch):
        def boom(html, **kwargs):
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: "<html>page</html>")
        monkeypatch.setattr(content_module, "extract", boom)

        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])
        assert article.content == ""
        assert article.extraction_failed is True

    def test_body_is_truncated_to_config_limit(self, cfg, monkeypatch):
        monkeypatch.setattr(content_module, "fetch_html", lambda url, extract_cfg: "<html>page</html>")
        monkeypatch.setattr(content_module, "extract", lambda html, **kwargs: "word " * 5000)

        cfg["extract"]["max_content_chars"] = 300
        article = enrich_article(make_article(summary=SUMMARY), cfg["extract"])

        assert len(article.content) <= 300

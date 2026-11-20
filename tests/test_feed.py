"""RSS 2.0 output: structure, AI label, original link, empty feed."""

import xml.etree.ElementTree as ET

from src.categories import Category
from src.feedbuild import write_feed

from tests.helpers import make_article


def _parse(path):
    return ET.parse(path).getroot()


class TestFeedOutput:
    def test_writes_one_item_with_required_fields(self, cfg, tmp_path):
        article = make_article(
            title="Refined title",
            link="https://example.com/post",
            content="Original body.",
        )
        article.refined_content = "Condensed body."
        article.reason = "Relevant to the interest profile"

        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"))
        root = _parse(out)

        assert root.tag == "rss"
        assert root.get("version") == "2.0"
        items = root.findall("./channel/item")
        assert len(items) == 1

        item = items[0]
        assert item.findtext("title") == "Refined title"
        assert item.findtext("link") == "https://example.com/post"
        assert item.findtext("guid") == "https://example.com/post"
        assert item.findtext("pubDate")

        description = item.findtext("description")
        assert "AI 精简版" in description
        assert "Condensed body." in description
        assert "https://example.com/post" in description
        assert "Relevant to the interest profile" in description

    def test_keeps_original_title_when_refinement_skipped(self, cfg, tmp_path):
        article = make_article(title="Original headline", content="Body text.", link="https://example.com/x")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"))
        item = _parse(out).find("./channel/item")
        assert item.findtext("title") == "Original headline"
        assert "AI 精简版" in item.findtext("description")

    def test_source_element_points_at_the_feed(self, cfg, tmp_path):
        article = make_article(
            link="https://example.com/post",
            source_url="https://example.com/rss",
            source="Example Feed",
        )
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"))
        source_el = _parse(out).find("./channel/item/source")
        assert source_el is not None
        assert source_el.get("url") == "https://example.com/rss"
        assert source_el.text == "Example Feed"

    def test_entry_order_matches_ranking(self, cfg, tmp_path):
        """feedgen prepends by default; the feed must keep our ranked order."""
        ranked = [
            make_article(title="best", link="https://example.com/1", content="b"),
            make_article(title="second", link="https://example.com/2", content="b"),
            make_article(title="third", link="https://example.com/3", content="b"),
        ]
        out = write_feed(ranked, cfg, path=str(tmp_path / "feed.xml"))
        titles = [item.findtext("title") for item in _parse(out).findall("./channel/item")]
        assert titles == ["best", "second", "third"]

    def test_category_feed_carries_title_and_category(self, cfg, tmp_path):
        article = make_article(
            link="https://example.com/post",
            category="Science",
            category_label="科学",
        )
        category = Category("Science", "科学")
        out = write_feed(
            [article],
            cfg,
            path=str(tmp_path / "feeds" / "science.xml"),
            title="Test Feed · 科学",
            description="科学 (Science)",
            category=category,
        )
        root = _parse(out)
        assert root.findtext("./channel/title") == "Test Feed · 科学"
        assert root.findtext("./channel/description") == "科学 (Science)"
        assert root.findtext("./channel/category") == "科学"
        assert root.findtext("./channel/item/category") == "科学"

    def test_empty_feed_is_still_valid(self, cfg, tmp_path):
        out = write_feed([], cfg, path=str(tmp_path / "feed.xml"))
        root = _parse(out)
        assert root.findtext("./channel/title") == "Test Feed"
        assert root.findall("./channel/item") == []

    def test_escapes_html_in_body(self, cfg, tmp_path):
        article = make_article(title="A <b>title</b>", content="1 < 2 & 3 > 2", link="https://example.com/e")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"))
        root = _parse(out)
        assert root.findtext("./channel/item/title") == "A <b>title</b>"
        description = root.find("./channel/item/description").text
        assert "1 &lt; 2 &amp; 3 &gt; 2" in description
        assert "<b>" not in description

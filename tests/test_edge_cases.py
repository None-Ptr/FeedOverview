"""Edge cases across the pipeline: hostile text, odd URLs, malformed LLM output."""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import src.main as main_module
from src.dedup import SeenStore, dedupe_articles
from src.feedbuild import write_feed
from src.rank import parse_ranking_response
from src.refine import refine_articles
from src.textutil import normalize_url, sanitize_xml, truncate

from tests.helpers import FakeLLM, make_article

LONG_BODY = "This is a reasonably long original body used for edge case tests. " * 4
NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


class TestHostileFeedContent:
    def test_control_characters_are_stripped(self, cfg, tmp_path):
        article = make_article(content="before\x00middle\x0bafter", link="https://example.com/ctl")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)

        description = ET.parse(out).getroot().find("./channel/item/description").text
        assert "beforemiddleafter" in description
        assert "\x00" not in description
        assert "\x0b" not in description

    def test_control_characters_in_title(self, cfg, tmp_path):
        article = make_article(title="Bad\x00title", link="https://example.com/t", content=LONG_BODY)
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)
        assert ET.parse(out).getroot().findtext("./channel/item/title") == "Badtitle"

    def test_emoji_and_unicode_survive(self, cfg, tmp_path):
        article = make_article(title="书评 📚 — naïve café", content="内容 \U0001f600 保留", link="https://example.com/u")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)
        root = ET.parse(out).getroot()
        assert root.findtext("./channel/item/title") == "书评 📚 — naïve café"
        assert "内容" in root.find("./channel/item/description").text

    def test_missing_publish_date_uses_build_time(self, cfg, tmp_path):
        article = make_article(link="https://example.com/nodate", content=LONG_BODY)
        article.published = None
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)
        assert "01 May 2026" in ET.parse(out).getroot().findtext("./channel/item/pubDate")

    def test_very_long_title_is_written(self, cfg, tmp_path):
        article = make_article(title="T" * 2000, content=LONG_BODY, link="https://example.com/long")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)
        assert len(ET.parse(out).getroot().findtext("./channel/item/title")) == 2000

    def test_html_in_title_is_escaped(self, cfg, tmp_path):
        article = make_article(title="<script>x</script>", content=LONG_BODY, link="https://example.com/h")
        out = write_feed([article], cfg, path=str(tmp_path / "feed.xml"), now=NOW)
        root = ET.parse(out).getroot()
        assert root.findtext("./channel/item/title") == "<script>x</script>"
        assert "<script>" not in (root.find("./channel/item/description").text or "")


class TestSanitizeXml:
    def test_keeps_allowed_whitespace(self):
        assert sanitize_xml("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_removes_control_bytes(self):
        assert sanitize_xml("a\x00b\x01c\x1fd") == "abcd"

    def test_keeps_unicode(self):
        assert sanitize_xml("汉语 é 🎉") == "汉语 é 🎉"

    def test_empty(self):
        assert sanitize_xml("") == ""


class TestUrlAndTextEdges:
    def test_ipv6_host_with_default_port(self):
        assert normalize_url("https://[::1]:443/a/") == "https://[::1]/a"

    def test_userinfo_preserved(self):
        assert normalize_url("https://user:pw@example.com/a") == "https://user:pw@example.com/a"

    def test_percent_encoding_is_kept(self):
        assert normalize_url("https://example.com/a%20b") == "https://example.com/a%20b"

    def test_duplicate_query_params_sorted(self):
        assert normalize_url("https://example.com/?b=2&b=1&a=3") == "https://example.com/?a=3&b=1&b=2"

    def test_truncate_limit_shorter_than_suffix(self):
        assert truncate("abcdef", 1) == "a"

    def test_truncate_never_exceeds_limit(self):
        for limit in (5, 40, 41, 200):
            assert len(truncate("word " * 100, limit)) <= limit


class TestRankParsingEdges:
    def test_out_of_range_index_ignored(self):
        data = {"order": [{"index": 99, "score": 9, "reason": "x"}, {"index": 0, "score": 5, "reason": "ok"}]}
        assert parse_ranking_response(data, batch_size=2) == {0: (5.0, "ok")}

    def test_non_numeric_entries_skipped(self):
        data = {"order": [{"index": "abc", "score": 9}, {"index": 0, "score": "high"}, {"index": 1, "score": 7}]}
        assert parse_ranking_response(data, batch_size=2) == {1: (7.0, "")}

    def test_score_is_clamped(self):
        data = {"order": [{"index": 0, "score": 42, "reason": "a"}, {"index": 1, "score": -5, "reason": "b"}]}
        parsed = parse_ranking_response(data, batch_size=2)
        assert parsed[0][0] == 10.0 and parsed[1][0] == 0.0

    def test_duplicate_index_last_wins(self):
        data = {"order": [{"index": 0, "score": 1}, {"index": 0, "score": 8}]}
        assert parse_ranking_response(data, batch_size=1) == {0: (8.0, "")}

    def test_offset_maps_to_global_indices(self):
        data = {"order": [{"index": 0, "score": 6, "reason": "r"}]}
        assert parse_ranking_response(data, batch_size=2, offset=4) == {4: (6.0, "r")}

    def test_global_indices_are_accepted(self):
        data = {"order": [{"index": 5, "score": 6, "reason": "r"}]}
        assert parse_ranking_response(data, batch_size=2, offset=4) == {5: (6.0, "r")}

    def test_missing_order_key(self):
        assert parse_ranking_response({"items": []}, batch_size=2) is None

    def test_order_not_a_list(self):
        assert parse_ranking_response({"order": "0,1,2"}, batch_size=2) is None

    def test_empty_order(self):
        assert parse_ranking_response({"order": []}, batch_size=2) is None

    def test_none_payload(self):
        assert parse_ranking_response(None, batch_size=2) is None


class TestRefineEdges:
    def test_non_string_refined_falls_back(self, cfg):
        article = make_article(content=LONG_BODY, link="https://example.com/r1")
        refine_articles([article], cfg, FakeLLM(lambda messages: {"refined": ["a", "b"]}))
        assert article.refine_failed is True
        assert article.refined_content

    def test_blank_refined_falls_back(self, cfg):
        article = make_article(content=LONG_BODY, link="https://example.com/r2")
        refine_articles([article], cfg, FakeLLM(lambda messages: {"refined": "     "}))
        assert article.refine_failed is True

    def test_overlong_title_is_ignored(self, cfg):
        cfg["llm"]["refine"]["keep_title"] = False
        article = make_article(title="Original", content=LONG_BODY, link="https://example.com/r3")
        llm = FakeLLM(lambda messages: {"refined": "A refined body that is long enough.", "title": "T" * 500})
        refine_articles([article], cfg, llm)
        assert article.effective_title == "Original"

    def test_non_string_title_is_ignored(self, cfg):
        cfg["llm"]["refine"]["keep_title"] = False
        article = make_article(title="Original", content=LONG_BODY, link="https://example.com/r4")
        llm = FakeLLM(lambda messages: {"refined": "A refined body that is long enough.", "title": 42})
        refine_articles([article], cfg, llm)
        assert article.effective_title == "Original"

    def test_empty_article_list_is_a_noop(self, cfg):
        refine_articles([], cfg, FakeLLM(available=False))


class TestDedupeEdges:
    def test_case_and_slash_variants_collapse(self):
        articles = [
            make_article(link="HTTPS://Example.com/Post/"),
            make_article(link="https://example.com/Post"),
        ]
        assert len(dedupe_articles(articles)) == 1

    def test_legacy_list_cache_is_loaded(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"seen": ["https://a.com/1", "https://a.com/2"]}), encoding="utf-8")
        store = SeenStore(path).load()
        assert store.links == {"https://a.com/1", "https://a.com/2"}

    def test_bad_timestamps_are_skipped(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"seen": {"https://a.com/bad": "nonsense", "https://a.com/ok": 1767225600}}), encoding="utf-8")
        store = SeenStore(path).load()
        assert "https://a.com/bad" not in store
        assert "https://a.com/ok" in store

    def test_future_timestamp_is_kept(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text(json.dumps({"seen": {"https://a.com/future": "2099-01-01T00:00:00Z"}}), encoding="utf-8")
        store = SeenStore(path).load()
        assert store.prune(datetime(2026, 1, 1, tzinfo=timezone.utc)) == 0
        assert "https://a.com/future" in store

    def test_empty_json_object(self, tmp_path):
        path = tmp_path / "seen.json"
        path.write_text("{}", encoding="utf-8")
        assert SeenStore(path).load().links == set()


class TestPipelineEdges:
    def test_no_feeds_configured_writes_empty_feed(self, cfg, tmp_path, monkeypatch):
        cfg["output_path"] = str(tmp_path / "feed.xml")
        cfg["seen_path"] = str(tmp_path / "seen.json")
        cfg["feeds"] = []
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        assert main_module.run(cfg) == 0
        assert ET.parse(cfg["output_path"]).getroot().findall("./channel/item") == []

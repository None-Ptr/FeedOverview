"""URL normalization, truncation, sentence splitting and HTML stripping."""

from src.textutil import collapse_whitespace, normalize_url, split_sentences, strip_html, truncate


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self):
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_removes_fragment(self):
        assert normalize_url("https://example.com/a#section") == "https://example.com/a"

    def test_removes_tracking_params(self):
        url = "https://example.com/a?utm_source=rss&utm_medium=feed&fbclid=abc&id=7"
        assert normalize_url(url) == "https://example.com/a?id=7"

    def test_sorts_query_params(self):
        assert normalize_url("https://example.com/a?b=2&a=1") == "https://example.com/a?a=1&b=2"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/post/") == "https://example.com/post"
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_removes_default_port(self):
        assert normalize_url("http://example.com:80/a") == "http://example.com/a"
        assert normalize_url("https://example.com:443/a") == "https://example.com/a"

    def test_keeps_relative_url_untouched(self):
        assert normalize_url("/only/path") == "/only/path"

    def test_empty_input(self):
        assert normalize_url("") == ""


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_respects_hard_limit(self):
        text = "x" * 200
        assert len(truncate(text, 50)) <= 50

    def test_prefers_sentence_boundary(self):
        text = "First sentence is here. " * 10
        result = truncate(text, 60)
        assert len(result) <= 60
        assert result.endswith("…")


class TestSplitSentences:
    def test_splits_on_end_punctuation(self):
        assert split_sentences("One here. Two there! Three?") == ["One here.", "Two there!", "Three?"]

    def test_keeps_abbreviations_inside_the_sentence(self):
        assert split_sentences("Dr. Lee spoke to the U.S. team.") == ["Dr. Lee spoke to the U.S. team."]

    def test_closing_quote_stays_with_the_sentence(self):
        assert split_sentences('She said, "Done." Then she left.') == [
            'She said, "Done."',
            "Then she left.",
        ]

    def test_units_rejoin_to_the_original(self):
        text = "  First  sentence here.  Second one! Third?  "
        joined = " ".join(split_sentences(text))
        assert collapse_whitespace(joined) == collapse_whitespace(text)

    def test_text_without_punctuation_is_one_unit(self):
        assert split_sentences("A headline without punctuation") == ["A headline without punctuation"]

    def test_empty(self):
        assert split_sentences("") == []


class TestStripHtml:
    def test_removes_tags_and_scripts(self):
        html = "<p>Hello <b>world</b></p><script>alert(1)</script>"
        assert strip_html(html) == "Hello world"

    def test_unescapes_entities(self):
        assert strip_html("<p>a &amp; b</p>") == "a & b"

    def test_empty(self):
        assert strip_html("") == ""

"""Category parsing, slugs, file paths and grouping."""

from src.categories import (
    Category,
    category_feed_path,
    derive_categories,
    group_articles,
    normalize_categories,
    slugify,
)

from tests.helpers import make_article


class TestNormalize:
    def test_accepts_mappings_and_pairs(self):
        categories = normalize_categories(
            [
                {"key": "News", "label": "新闻"},
                ["Science", "科学"],
                {"key": "Tech"},
            ]
        )
        assert categories == [
            Category("News", "新闻"),
            Category("Science", "科学"),
            Category("Tech", "Tech"),
        ]

    def test_drops_invalid_and_duplicate_keys(self):
        categories = normalize_categories(
            [{"key": ""}, {"key": "News", "label": "A"}, {"key": "news", "label": "B"}, "x"]
        )
        assert categories == [Category("News", "A")]

    def test_derive_keeps_first_seen_order(self):
        assert [category.key for category in derive_categories(["Books", "", "Books", "Ideas"])] == [
            "Books",
            "Ideas",
        ]


class TestPaths:
    def test_slug_is_lowercase_and_safe(self):
        assert slugify("Books") == "books"
        assert slugify("Higher Ed") == "higher-ed"
        assert slugify("!!!") == "category"

    def test_category_feed_lives_next_to_combined_feed(self):
        path = category_feed_path("docs/feed.xml", "Science")
        assert path.as_posix() == "docs/feeds/science.xml"


class TestGrouping:
    def test_groups_in_declared_order_and_fills_category(self):
        categories = [Category("News", "新闻"), Category("Science", "科学")]
        news = make_article(link="https://example.com/news", category="News")
        science = make_article(link="https://example.com/science", category="Science")
        unknown = make_article(link="https://example.com/unknown", category="Other")

        groups = group_articles([science, unknown, news], categories)

        assert list(groups) == ["News", "Science"]
        assert [article.link for article in groups["News"]] == [
            "https://example.com/unknown",
            "https://example.com/news",
        ]
        assert [article.link for article in groups["Science"]] == ["https://example.com/science"]
        # Unknown categories fall back to the first declared one instead of being lost.
        assert unknown.category == "News"

    def test_empty_categories_stay_present(self):
        groups = group_articles([], [Category("News", "新闻")])
        assert groups == {"News": []}

"""Default config fixture used across tests."""

import pytest

from src.categories import Category


@pytest.fixture
def cfg() -> dict:
    return {
        "feed_title": "Test Feed",
        "feed_link": "https://example.github.io/feed/",
        "feed_description": "Test description",
        "feed_language": "en",
        "output_path": "docs/feed.xml",
        "seen_path": "cache/seen.json",
        "seen_retention_days": 30,
        "max_items": 5,
        "max_items_per_source": 3,
        "max_items_per_category": 5,
        "max_candidates_per_category": 10,
        "max_candidates_per_source": 2,
        "categories": [Category("News", "新闻")],
        "interest": "Python and developer tooling",
        "feeds": [{"name": "Example", "url": "https://example.com/rss", "weight": 1.0}],
        "extract": {
            "timeout": 5,
            "retries": 0,
            "max_workers": 2,
            "user_agent": "test-agent",
            "fast": True,
            "favor_recall": True,
            "max_content_chars": 4000,
            "min_content_chars": 80,
        },
        "llm": {
            "model": "test-model",
            "temperature": 0.2,
            "max_summary_chars": 500,
            "ranking_criteria": "Relevance and freshness.",
            "rank_batch_size": 2,
            "timeout": 30,
            "refine": {
                "enabled": True,
                "max_input_chars": 200,
                "max_output_chars": 80,
                "concurrency": 2,
                "style": "Be concise.",
                "keep_title": True,
            },
        },
    }

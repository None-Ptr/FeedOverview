"""Shared test helpers (kept out of conftest so tests can import them directly)."""

from datetime import datetime, timezone

from src.models import Article
from src.textutil import normalize_url


def make_article(
    title: str = "Sample article",
    link: str = "https://example.com/post",
    *,
    source: str = "Example",
    weight: float = 1.0,
    source_url: str = "",
    category: str = "",
    category_label: str = "",
    published: datetime | None = None,
    summary: str = "Summary text.",
    content: str = "",
) -> Article:
    return Article(
        title=title,
        link=link,
        normalized_link=normalize_url(link),
        source_name=source,
        source_weight=weight,
        source_url=source_url,
        category=category,
        category_label=category_label,
        published=published or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        summary=summary,
        content=content,
    )


class FakeLLM:
    """Minimal stand-in for LLMClient."""

    def __init__(self, responder=None, available: bool = True, reason: str | None = None):
        self.responder = responder
        self.available = available
        self.unavailable_reason = None if available else (reason or "no key")
        self.calls: list[list[dict]] = []

    def chat_json(self, messages, *, retries=1, temperature=None):
        self.calls.append(messages)
        if self.responder is None:
            return None
        return self.responder(messages)

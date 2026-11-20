"""Category model and helpers for the per-category feeds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .models import Article

UNCATEGORIZED_KEY = "Uncategorized"
UNCATEGORIZED_LABEL = "未分类"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Category:
    """One output feed: a stable English key plus a human-readable label."""

    key: str
    label: str


def slugify(key: str) -> str:
    """Turn a category key into a safe file name ("Books" -> "books")."""
    slug = _SLUG_RE.sub("-", (key or "").strip().lower()).strip("-")
    return slug or "category"


def category_feed_path(output_path: str | Path, key: str) -> Path:
    """Category feeds live next to the combined feed, under ``feeds/``."""
    return Path(output_path).parent / "feeds" / f"{slugify(key)}.xml"


def normalize_categories(raw: object) -> list[Category]:
    """Accept ``{key, label}`` mappings or ``[key, label]`` pairs, drop bad rows."""
    if not isinstance(raw, list):
        return []

    categories: list[Category] = []
    seen: set[str] = set()
    for item in raw:
        key = label = ""
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            label = str(item.get("label") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 1:
            key = str(item[0] or "").strip()
            label = str(item[1] or "").strip() if len(item) >= 2 else ""
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        categories.append(Category(key=key, label=label or key))
    return categories


def derive_categories(keys: Iterable[str]) -> list[Category]:
    """Build categories from feed declarations, preserving first-seen order."""
    categories: list[Category] = []
    seen: set[str] = set()
    for raw in keys:
        key = str(raw or "").strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        categories.append(Category(key=key, label=key))
    return categories


def find_category(categories: Sequence[Category], value: str) -> Category | None:
    """Resolve a feed's category case-insensitively against the declared list."""
    lowered = str(value or "").strip().lower()
    if not lowered:
        return None
    for category in categories:
        if category.key.lower() == lowered:
            return category
    return None


def group_articles(articles: Iterable[Article], categories: Sequence[Category]) -> dict[str, list[Article]]:
    """Group articles by category, keeping the declared category order.

    Articles with an unknown or missing category fall back to the first declared
    category so that nothing is silently dropped by the output stage.
    """
    groups: dict[str, list[Article]] = {category.key: [] for category in categories}
    if not groups:
        return groups

    fallback = categories[0].key
    for article in articles:
        key = article.category if article.category in groups else fallback
        article.category = key
        groups[key].append(article)
    return groups

"""Configuration loading: defaults + config.yaml deep merge + env overrides."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .categories import (
    UNCATEGORIZED_KEY,
    UNCATEGORIZED_LABEL,
    Category,
    derive_categories,
    find_category,
    normalize_categories,
)

logger = logging.getLogger(__name__)

DEFAULT_CRITERIA = (
    "1. Relevance to the reader's interests; closer match scores higher.\n"
    "2. Information density and originality: primary sources, real data and concrete "
    "conclusions beat rehashed posts and marketing copy.\n"
    "3. Practical value for developers: hands-on methods, tools and lessons beat platitudes.\n"
    "4. Freshness: newer articles win when everything else is equal.\n"
    "5. Diversity: keep only the most valuable article per topic."
)

DEFAULTS: dict[str, Any] = {
    "feed_title": "Curated Tech Feed",
    "feed_link": "https://example.com/",
    "feed_description": "Multiple RSS sources aggregated, ranked and condensed by an LLM.",
    "feed_language": "en",
    "output_path": "docs/feed.xml",
    "seen_path": "cache/seen.json",
    "seen_retention_days": 30,
    # Combined feed cap; per-category caps live below.
    "max_items": 200,
    "max_items_per_source": 20,
    "max_items_per_category": 6,
    "max_candidates_per_category": 12,
    # Declared as {key, label} mappings or [key, label] pairs. When omitted,
    # categories are derived from the feeds in first-seen order.
    "categories": [],
    "interest": "",
    "feeds": [],
    "extract": {
        "timeout": 15,
        "retries": 2,
        "max_workers": 5,
        "user_agent": (
            "Mozilla/5.0 (compatible; FeedAggregator/1.0; "
            "+https://github.com/) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "fast": True,
        "favor_recall": True,
        "max_content_chars": 8000,
        "min_content_chars": 80,
    },
    "llm": {
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "max_summary_chars": 1200,
        "ranking_criteria": DEFAULT_CRITERIA,
        "rank_batch_size": 40,
        "timeout": 90,
        "refine": {
            "enabled": True,
            "max_input_chars": 4000,
            "max_output_chars": 800,
            "concurrency": 3,
            "style": (
                "Write concise, objective prose. Keep key data and conclusions, drop "
                "redundant wording and repetition. Never change facts or add information "
                "that is not in the source."
            ),
            "keep_title": True,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | os.PathLike[str] = "config.yaml") -> dict:
    """Load config; a missing file simply means "use the built-in defaults"."""
    config_path = Path(path)
    raw: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file must contain a mapping: {config_path}")
        raw = loaded
    else:
        logger.warning("Config file %s not found, falling back to built-in defaults", config_path)

    cfg = _deep_merge(DEFAULTS, raw)
    _apply_env_overrides(cfg)
    _validate(cfg)
    return cfg


def _apply_env_overrides(cfg: dict) -> None:
    """LLM credentials come from the environment only, never from the repo."""
    llm = cfg["llm"]
    llm["api_key"] = os.environ.get("LLM_API_KEY", "").strip()
    llm["base_url"] = os.environ.get("LLM_BASE_URL", "").strip() or None
    env_model = os.environ.get("LLM_MODEL", "").strip()
    if env_model:
        llm["model"] = env_model


def _validate(cfg: dict) -> None:
    feeds = cfg.get("feeds") or []
    if not isinstance(feeds, list):
        raise ValueError("feeds must be a list")

    cleaned: list[dict] = []
    for index, feed in enumerate(feeds):
        if not isinstance(feed, dict) or not feed.get("url"):
            logger.warning("Feed entry #%d has no url, skipped: %r", index, feed)
            continue
        cleaned.append(
            {
                # ``title`` is accepted as an alias so feed lists exported from other
                # tools can be pasted in unchanged.
                "name": str(feed.get("name") or feed.get("title") or feed["url"]),
                "url": str(feed["url"]),
                "weight": float(feed.get("weight", 1.0) or 1.0),
                "category": str(feed.get("category") or "").strip(),
            }
        )

    cfg["feeds"], cfg["categories"] = _assign_categories(
        cleaned, normalize_categories(cfg.get("categories"))
    )

    cfg["max_items"] = max(1, int(cfg.get("max_items", 200)))
    cfg["max_items_per_source"] = max(1, int(cfg.get("max_items_per_source", 20)))
    cfg["max_items_per_category"] = max(1, int(cfg.get("max_items_per_category", 6)))
    cfg["max_candidates_per_category"] = max(1, int(cfg.get("max_candidates_per_category", 12)))
    cfg["seen_retention_days"] = max(1, int(cfg.get("seen_retention_days", 30)))
    if not cfg.get("output_path"):
        raise ValueError("output_path must not be empty")


def _assign_categories(feeds: list[dict], declared: list[Category]) -> tuple[list[dict], list[Category]]:
    """Resolve every feed's category against the declared list and store it back."""
    categories = declared or derive_categories(feed["category"] for feed in feeds)
    if not categories:
        categories = [Category(UNCATEGORIZED_KEY, UNCATEGORIZED_LABEL)]

    uncategorized: Category | None = None
    for feed in feeds:
        category = find_category(categories, feed["category"])
        if category is None:
            if uncategorized is None:
                uncategorized = find_category(categories, UNCATEGORIZED_KEY)
                if uncategorized is None:
                    uncategorized = Category(UNCATEGORIZED_KEY, UNCATEGORIZED_LABEL)
                    categories.append(uncategorized)
            logger.warning(
                "Feed %s has category %r which is not declared; grouping it under %s",
                feed["name"],
                feed["category"],
                UNCATEGORIZED_KEY,
            )
            category = uncategorized
        feed["category"] = category.key
        feed["category_label"] = category.label

    return feeds, categories

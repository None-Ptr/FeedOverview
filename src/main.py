"""Pipeline entry point: ``python -m src.main``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter

from .categories import (
    UNCATEGORIZED_KEY,
    UNCATEGORIZED_LABEL,
    Category,
    category_feed_path,
    group_articles,
)
from .config import load_config
from .content import enrich_all, has_body
from .dedup import SeenStore, dedupe_articles
from .feedbuild import write_feed
from .feedfetch import fetch_all
from .llm import LLMClient
from .rank import rank_articles, select_for_llm
from .refine import refine_articles
from .site import write_index

logger = logging.getLogger("feed_aggregator")


def setup_logging(level: str = "INFO") -> None:
    logging.Formatter.converter = time.gmtime  # UTC timestamps in Actions logs
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
        force=True,
    )
    for noisy in ("urllib3", "trafilatura", "httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _categories(cfg: dict) -> list[Category]:
    categories = list(cfg.get("categories") or [])
    if not categories:
        categories = [Category(UNCATEGORIZED_KEY, UNCATEGORIZED_LABEL)]
    return categories


def _source_counts(cfg: dict, categories: list[Category]) -> dict[str, int]:
    counted = Counter(feed.get("category") for feed in cfg.get("feeds") or [])
    return {category.key: counted.get(category.key, 0) for category in categories}


def _publish(cfg: dict, categories: list[Category], selections: dict[str, list]) -> list:
    """Write one feed per category, the combined feed, and the index page."""
    combined: list = []
    for category in categories:
        selected = selections.get(category.key) or []
        base_description = cfg.get("feed_description") or ""
        description = f"{category.label} ({category.key})"
        if base_description:
            description = f"{description} — {base_description}"
        write_feed(
            selected,
            cfg,
            path=str(category_feed_path(cfg["output_path"], category.key)),
            title=f"{cfg.get('feed_title') or 'Curated Feed'} · {category.label}",
            description=description,
            category=category,
        )
        combined.extend(selected)

    combined = combined[: int(cfg.get("max_items", 200))]
    write_feed(combined, cfg)
    write_index(
        categories,
        cfg,
        item_counts={category.key: len(selections.get(category.key) or []) for category in categories},
        source_counts=_source_counts(cfg, categories),
    )
    return combined


def run(cfg: dict) -> int:
    """Execute the whole pipeline once. Returns a process exit code."""
    llm = LLMClient(cfg["llm"])
    if llm.available:
        logger.info("LLM enabled (model=%s)", llm.model)
    else:
        logger.warning(
            "LLM disabled (%s): ranking falls back to time + source weight, refinement is skipped",
            llm.unavailable_reason,
        )

    categories = _categories(cfg)
    seen = SeenStore(cfg["seen_path"], int(cfg["seen_retention_days"])).load()

    raw_articles = fetch_all(cfg)
    candidates = dedupe_articles(raw_articles, seen.links)

    if not candidates:
        logger.info("No new articles after de-duplication; writing empty but valid feeds")
        _publish(cfg, categories, {})
        seen.prune()
        seen.save()
        return 0

    groups = group_articles(candidates, categories)
    candidate_limit = int(
        cfg.get("max_candidates_per_category", cfg.get("max_candidates_for_llm", 18))
    )
    per_source_limit = int(cfg.get("max_candidates_per_source", 2))
    item_limit = int(cfg.get("max_items_per_category", 6))

    selections: dict[str, list] = {}
    for category in categories:
        items = groups.get(category.key) or []
        if not items:
            selections[category.key] = []
            logger.info("Category %s: no new articles", category.key)
            continue

        if len(items) > candidate_limit:
            items = select_for_llm(items, candidate_limit, per_source_limit)
        logger.info("Category %s: %d candidates", category.key, len(items))

        enrich_all(items, cfg["extract"])

        # Articles whose body could not be extracted are dropped rather than
        # published as RSS-summary stubs. They are not marked as seen, so a
        # transient block gets another chance on the next run.
        extracted = [article for article in items if has_body(article)]
        if len(extracted) < len(items):
            logger.info(
                "Category %s: dropped %d of %d articles without an extractable body",
                category.key,
                len(items) - len(extracted),
                len(items),
            )
        if not extracted:
            selections[category.key] = []
            logger.warning("Category %s: nothing could be extracted", category.key)
            continue

        ranked = rank_articles(extracted, cfg, llm, category=category)
        selected = ranked[:item_limit]
        refine_articles(selected, cfg, llm)
        selections[category.key] = selected

        # Everything that entered the pipeline is considered seen, so the next
        # run never re-processes (or re-bills) the same links.
        for article in extracted:
            seen.mark(article.normalized_link)

        logger.info("Category %s: %d of %d selected", category.key, len(selected), len(ranked))

    published = _publish(cfg, categories, selections)
    seen.prune()
    seen.save()

    counts = ", ".join(
        f"{category.key}={len(selections.get(category.key) or [])}" for category in categories
    )
    logger.info(
        "Done: %d feeds -> %d candidates -> %d published (%s)",
        len(cfg.get("feeds") or []),
        len(candidates),
        len(published),
        counts,
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate RSS feeds, rank and condense with an LLM.")
    parser.add_argument(
        "--config",
        default=os.environ.get("FEED_CONFIG", "config.yaml"),
        help="Path to config.yaml (default: %(default)s, or $FEED_CONFIG)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Logging level (default: %(default)s, or $LOG_LEVEL)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)

    try:
        cfg = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - config errors are fatal but must be readable
        logger.error("Failed to load config %s: %s", args.config, exc)
        return 1

    try:
        return run(cfg)
    except Exception:  # noqa: BLE001 - keep the previous feed intact on a fatal error
        logger.exception("Pipeline crashed; the previous feed was left untouched")
        return 1


if __name__ == "__main__":
    sys.exit(main())

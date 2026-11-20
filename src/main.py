"""Pipeline entry point: ``python -m src.main``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .config import load_config
from .content import enrich_all, has_body
from .dedup import SeenStore, dedupe_articles
from .feedbuild import write_feed
from .feedfetch import fetch_all
from .llm import LLMClient
from .rank import rank_articles, select_for_llm
from .refine import refine_articles

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

    seen = SeenStore(cfg["seen_path"], int(cfg["seen_retention_days"])).load()

    raw_articles = fetch_all(cfg)
    candidates = dedupe_articles(raw_articles, seen.links)

    if not candidates:
        logger.info("No new articles after de-duplication; writing an empty but valid feed")
        write_feed([], cfg)
        seen.prune()
        seen.save()
        return 0

    llm_limit = int(cfg["max_candidates_for_llm"])
    if len(candidates) > llm_limit:
        candidates = select_for_llm(candidates, llm_limit)
        logger.info("Candidate list pre-filtered to %d entries (max_candidates_for_llm)", len(candidates))

    enrich_all(candidates, cfg["extract"])

    # Articles whose body could not be extracted are dropped rather than published
    # as RSS-summary stubs. They are not marked as seen, so a transient block gets
    # another chance on the next run.
    extracted = [article for article in candidates if has_body(article)]
    if len(extracted) < len(candidates):
        logger.info("Dropped %d of %d articles without an extractable body", len(candidates) - len(extracted), len(candidates))
    if not extracted:
        logger.warning("No article could be extracted; writing an empty but valid feed")
        write_feed([], cfg)
        seen.prune()
        seen.save()
        return 0

    candidates = extracted
    ranked = rank_articles(candidates, cfg, llm)
    selected = ranked[: int(cfg["max_items"])]
    logger.info("Selected top %d of %d ranked candidates", len(selected), len(ranked))

    refine_articles(selected, cfg, llm)

    output_path = write_feed(selected, cfg)

    # Everything that entered the pipeline is now considered seen, so the next
    # run never re-processes (or re-bills) the same links.
    for article in candidates:
        seen.mark(article.normalized_link)
    seen.prune()
    seen.save()

    logger.info(
        "Done: %d feeds -> %d candidates -> %d ranked -> %d published in %s",
        len(cfg.get("feeds") or []),
        len(candidates),
        len(ranked),
        len(selected),
        output_path,
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

"""LLM-based relevance ranking with a deterministic fallback ordering."""

from __future__ import annotations

import logging
from typing import Optional

from .models import Article
from .textutil import truncate

logger = logging.getLogger(__name__)

MAX_REASON_CHARS = 160

RANK_SYSTEM_PROMPT = (
    "You are a meticulous editor who evaluates articles for a technical reader. "
    "You respond with a single JSON object and nothing else: no prose, no markdown fences."
)

RANK_JSON_SPEC = (
    'Return exactly this JSON shape: {"order":[{"index":0,"score":9.2,"reason":"one short sentence"}, ...]}. '
    "Include every article id exactly once, sorted from most to least important. "
    "score is a number between 0 and 10 (one decimal is enough).\n"
    "Use the whole scale and make the score agree with the reason:\n"
    "  9-10 essential for this reader; 7-8 strong, worth their time; 5-6 average; "
    "3-4 weak or off-topic; 0-2 promotional, duplicated or irrelevant.\n"
    "A promotional post, deal round-up or pure listicle can never score above 4, no matter how "
    "fresh it is."
)


def fallback_order(articles: list[Article]) -> list[Article]:
    """Newest first, then higher source weight."""
    return sorted(articles, key=lambda item: item.sort_key(), reverse=True)


def select_for_llm(articles: list[Article], limit: int) -> list[Article]:
    """Pre-filter over-long candidate lists by time and source weight."""
    if limit <= 0 or len(articles) <= limit:
        return list(articles)
    return fallback_order(articles)[:limit]


def build_ranking_messages(batch: list[Article], cfg: dict, category=None) -> list[dict]:
    llm_cfg = cfg.get("llm", {})
    interest = (cfg.get("interest") or "").strip() or "general software engineering and tooling"
    criteria = (llm_cfg.get("ranking_criteria") or "").strip() or "Relevance, information density, practical value, freshness, diversity."
    preview_chars = max(200, int(llm_cfg.get("max_summary_chars", 1200)))
    category_line = f"Category: {category.label} ({category.key})\n" if category is not None else ""

    blocks: list[str] = []
    for index, article in enumerate(batch):
        body = (article.content or article.summary or "").strip().replace("\n", " ")
        published = article.published.strftime("%Y-%m-%d %H:%M UTC") if article.published else "unknown"
        blocks.append(
            f"[{index}] title: {article.title}\n"
            f"    source: {article.source_name} | published: {published}\n"
            f"    link: {article.link}\n"
            f"    excerpt: {truncate(body, preview_chars)}"
        )

    user_prompt = (
        f"Reader interests: {interest}\n\n"
        f"{category_line}"
        f"Ranking criteria:\n{criteria}\n\n"
        f"Score and rank the {len(batch)} candidate articles below.\n\n"
        + "\n\n".join(blocks)
        + f"\n\n{RANK_JSON_SPEC}"
    )
    return [
        {"role": "system", "content": RANK_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def parse_ranking_response(data: Optional[dict], batch_size: int, offset: int = 0) -> Optional[dict[int, tuple[float, str]]]:
    """Map the model response onto global article indices."""
    if not isinstance(data, dict):
        return None
    order = data.get("order")
    if not isinstance(order, list) or not order:
        return None

    scores: dict[int, tuple[float, str]] = {}
    for item in order:
        if not isinstance(item, dict):
            continue
        try:
            raw_index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue

        # Accept both batch-local and already-global indices.
        if 0 <= raw_index < batch_size:
            index = offset + raw_index
        elif offset <= raw_index < offset + batch_size:
            index = raw_index
        else:
            continue

        score = max(0.0, min(10.0, score))
        reason = str(item.get("reason") or "").strip()[:MAX_REASON_CHARS]
        scores[index] = (score, reason)

    return scores or None


def rank_articles(articles: list[Article], cfg: dict, llm=None, category=None) -> list[Article]:
    """Rank candidates with the LLM; degrade gracefully to time + weight ordering."""
    items = list(articles)
    if not items:
        return []

    reason = getattr(llm, "unavailable_reason", None) if llm is not None else "no LLM client"
    if llm is None or not getattr(llm, "available", False):
        logger.warning("LLM unavailable (%s); using time + source weight ordering", reason or "unknown reason")
        return fallback_order(items)

    batch_size = max(1, int(cfg.get("llm", {}).get("rank_batch_size", 40)))
    scores: dict[int, tuple[float, str]] = {}
    failed_batches = 0

    for offset in range(0, len(items), batch_size):
        batch = items[offset : offset + batch_size]
        try:
            # Deterministic samples: ranking should not drift between runs.
            data = llm.chat_json(
                build_ranking_messages(batch, cfg, category=category),
                retries=1,
                temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001 - a ranking failure must not abort the run
            logger.warning("Ranking call crashed at offset %d: %s", offset, exc)
            data = None

        parsed = parse_ranking_response(data, len(batch), offset) if data else None
        if parsed is None:
            failed_batches += 1
            logger.warning("Ranking batch at offset %d unusable; those items keep time ordering", offset)
            continue
        scores.update(parsed)

    if not scores:
        logger.warning("LLM ranking produced nothing usable; using time + source weight ordering")
        return fallback_order(items)

    for index, article in enumerate(items):
        if index in scores:
            article.score, article.reason = scores[index]

    if failed_batches:
        logger.warning("%d ranking batch(es) failed; unscored items are ordered by time", failed_batches)

    if logger.isEnabledFor(logging.DEBUG):
        for index in sorted(scores):
            score, why = scores[index]
            logger.debug("rank %2d  %4.1f  %s | %s", index, score, items[index].title[:60], why[:60])

    ranked = sorted(items, key=_ranked_key, reverse=True)
    logger.info("LLM ranked %d articles (%d scored)", len(ranked), len(scores))
    return ranked


def _ranked_key(article: Article) -> tuple[int, float, object, float]:
    scored = article.score is not None
    return (1 if scored else 0, article.score or 0.0, article.time_key(), article.source_weight)

"""Per-article LLM refinement (condense + light copy editing), with per-item fallback."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .models import Article
from .textutil import truncate

logger = logging.getLogger(__name__)

MIN_BODY_CHARS = 80
MIN_REFINED_CHARS = 20
MAX_TITLE_CHARS = 200

DEFAULT_STYLE = (
    "Write concise, objective prose. Keep key data and conclusions, drop redundant wording "
    "and repetition. Never change facts or add information that is not in the source."
)

REFINE_SYSTEM_PROMPT = (
    "You are a careful technical editor. You condense articles while preserving every fact, "
    "figure and conclusion, and you never invent information. You respond with a single JSON "
    "object and nothing else: no prose, no markdown fences."
)


def build_refine_messages(article: Article, body: str, cfg: dict) -> list[dict]:
    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    style = str(refine_cfg.get("style") or "").strip() or DEFAULT_STYLE
    max_output = int(refine_cfg.get("max_output_chars", 800))
    keep_title = bool(refine_cfg.get("keep_title", True))

    if keep_title:
        title_rule = 'Do not return a "title" field; the title must stay unchanged.'
        json_hint = '{"refined":"..."}'
    else:
        title_rule = (
            'You may return an improved headline as "title" if it clearly helps; '
            "otherwise omit the field."
        )
        json_hint = '{"refined":"...","title":"..."}'

    user_prompt = (
        f"Editing style: {style}\n\n"
        "Hard constraints:\n"
        "- Preserve all facts, figures, outcomes and the author's stance; never invent content.\n"
        f"- Keep the result under roughly {max_output} characters.\n"
        "- Stay objective, readable and tightly structured; use short paragraphs.\n"
        "- Do not add ratings, commentary, calls to action or notes about the editing itself.\n"
        f"- {title_rule}\n\n"
        f"Source: {article.source_name}\n"
        f"Original title: {article.title}\n\n"
        f"Original body (may be truncated):\n{body}\n\n"
        f"Return exactly one JSON object: {json_hint}"
    )
    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def refine_articles(articles: list[Article], cfg: dict, llm=None) -> None:
    """Refine bodies in place. Never raises; each item falls back independently."""
    items = list(articles)
    if not items:
        return

    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    if not bool(refine_cfg.get("enabled", True)):
        logger.info("Refinement disabled in config, keeping original bodies")
        return
    if llm is None or not getattr(llm, "available", False):
        reason = getattr(llm, "unavailable_reason", None) if llm is not None else "no LLM client"
        logger.warning("LLM unavailable (%s); skipping refinement, keeping original bodies", reason)
        return

    concurrency = max(1, int(refine_cfg.get("concurrency", 3)))
    logger.info("Refining %d articles with concurrency=%d", len(items), concurrency)

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="refine") as pool:
        futures = {pool.submit(_refine_one, article, cfg, llm): article for article in items}
        for future in as_completed(futures):
            article = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - _refine_one already guards
                logger.warning("Refine task crashed for %s: %s", article.link, exc)
                _apply_fallback(article, _fallback_text(article, cfg), "task crashed")

    refined = sum(1 for article in items if article.refined_content and not article.refine_failed)
    failed = sum(1 for article in items if article.refine_failed)
    logger.info("Refinement finished: %d refined, %d fell back to the original body", refined, failed)


def _refine_one(article: Article, cfg: dict, llm) -> None:
    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    max_input = int(refine_cfg.get("max_input_chars", 4000))
    max_output = int(refine_cfg.get("max_output_chars", 800))
    keep_title = bool(refine_cfg.get("keep_title", True))

    body = (article.content or article.summary or "").strip()
    fallback = truncate(body, max_input)

    if len(body) < MIN_BODY_CHARS:
        logger.info("Body of %s too short to refine (%d chars), keeping it as is", article.link, len(body))
        return

    messages = build_refine_messages(article, fallback, cfg)
    try:
        data = llm.chat_json(messages, retries=1)
    except Exception as exc:  # noqa: BLE001 - defensive, chat_json already catches
        _apply_fallback(article, fallback, f"LLM call raised: {exc}")
        return

    if not data:
        _apply_fallback(article, fallback, "no usable JSON response")
        return

    refined = data.get("refined")
    if not isinstance(refined, str) or len(refined.strip()) < MIN_REFINED_CHARS:
        _apply_fallback(article, fallback, "missing or too short 'refined' field")
        return

    article.refined_content = truncate(refined.strip(), max_output)
    article.refine_failed = False

    if not keep_title:
        candidate_title = data.get("title")
        if isinstance(candidate_title, str):
            cleaned = candidate_title.strip()
            if 0 < len(cleaned) <= MAX_TITLE_CHARS:
                article.refined_title = cleaned


def _fallback_text(article: Article, cfg: dict) -> str:
    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    max_input = int(refine_cfg.get("max_input_chars", 4000))
    return truncate((article.content or article.summary or "").strip(), max_input)


def _apply_fallback(article: Article, fallback: str, reason: str) -> None:
    """Keep the article, but show the truncated original body instead."""
    article.refined_content = fallback or None
    article.refined_title = None
    article.refine_failed = True
    logger.warning("Refinement failed for %s (%s), falling back to the original body", article.link, reason)

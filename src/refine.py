"""Per-article LLM clean-up: the model names the sentences that are not worth reading.

The body is numbered sentence by sentence, the model answers with the indices to
delete, and the kept sentences are stitched back together unchanged. Nothing is
rewritten or summarised, so an imperfect model can only remove text, never
invent it.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .models import Article
from .textutil import split_sentences, truncate

logger = logging.getLogger(__name__)

MIN_BODY_CHARS = 80
# Fewer sentences than this leaves nothing meaningful to choose between.
MIN_SENTENCES = 3
MIN_KEPT_CHARS = 20
MAX_TITLE_CHARS = 200

_PARAGRAPH_SPLIT_RE = re.compile(r"\n+")

DEFAULT_GUIDE = (
    "Delete a sentence only when it is not article content: navigation, "
    "share/subscribe/app prompts, cookie or legal notices, advertising and sponsorship text, "
    '"read more" and related-link blocks, author bios and contact details, photo credits, '
    "sentences repeated elsewhere in the text, and jargon-heavy disclaimers or methodology "
    "asides that a reader can skip without losing the story. Never delete a sentence that "
    "carries facts, figures, quotes or the author's argument, and never delete sentences just "
    "to make the article shorter."
)

REFINE_SYSTEM_PROMPT = (
    "You are a careful copy editor preparing a scraped web article for an English learner. "
    "You never rewrite, reorder or summarise: you only choose whole sentences to delete. "
    "You respond with a single JSON object and nothing else: no prose, no markdown fences."
)


def build_refine_messages(article: Article, numbered: list[tuple[int, str]], cfg: dict) -> list[dict]:
    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    guide = str(refine_cfg.get("drop_criteria") or "").strip() or DEFAULT_GUIDE
    keep_title = bool(refine_cfg.get("keep_title", True))

    if keep_title:
        title_rule = 'Do not return a "title" field; the title stays unchanged.'
        json_hint = '{"drop":[2]}'
    else:
        title_rule = (
            'You may add an improved "title" when the current one is unclear or clickbait; '
            "otherwise omit the field."
        )
        json_hint = '{"drop":[2],"title":"..."}'

    listing = "\n".join(f"{index}: {text}" for index, text in numbered)
    user_prompt = (
        f"What to delete: {guide}\n\n"
        "Hard constraints:\n"
        "- You are choosing sentences to delete, not rewriting them. Kept sentences must stay "
        "exactly as written, in their original order.\n"
        "- Use only the indices listed below; never merge or reorder sentences.\n"
        "- Length is not a reason to delete: an article with nothing to remove gets an empty list.\n"
        "- Do not add ratings, commentary or notes about the editing itself.\n"
        f"- {title_rule}\n\n"
        f"Source: {article.source_name}\n"
        f"Original title: {article.title}\n\n"
        "Sentences (index: text):\n"
        f"{listing}\n\n"
        f"Return exactly one JSON object: {json_hint}\n"
        'List every index to delete, or use an empty list when nothing should be removed.'
    )
    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def refine_articles(articles: list[Article], cfg: dict, llm=None) -> None:
    """Clean up bodies in place. Never raises; each item falls back independently."""
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
    logger.info("Refinement finished: %d cleaned, %d fell back to the original body", refined, failed)


def _refine_one(article: Article, cfg: dict, llm) -> None:
    refine_cfg = (cfg.get("llm", {}) or {}).get("refine", {}) or {}
    max_input = int(refine_cfg.get("max_input_chars", 4000))
    max_drop_ratio = float(refine_cfg.get("max_drop_ratio", 0.4))
    keep_title = bool(refine_cfg.get("keep_title", True))

    body = (article.content or article.summary or "").strip()
    fallback = truncate(body, max_input)

    if len(body) < MIN_BODY_CHARS:
        logger.info("Body of %s too short to refine (%d chars), keeping it as is", article.link, len(body))
        return

    paragraphs = number_sentences(fallback)
    total = sum(len(paragraph) for paragraph in paragraphs)
    if total < MIN_SENTENCES:
        logger.info("Body of %s has only %d sentences, nothing to choose between", article.link, total)
        return

    numbered = [(index, text) for paragraph in paragraphs for index, text in paragraph]
    messages = build_refine_messages(article, numbered, cfg)
    try:
        data = llm.chat_json(messages, retries=1)
    except Exception as exc:  # noqa: BLE001 - defensive, chat_json already catches
        _apply_fallback(article, fallback, f"LLM call raised: {exc}")
        return

    if not data:
        _apply_fallback(article, fallback, "no usable JSON response")
        return

    drop = parse_drop_indices(data, total)
    if drop is None:
        _apply_fallback(article, fallback, "missing or unusable 'drop' list")
        return

    if len(drop) > int(total * max_drop_ratio):
        _apply_fallback(
            article, fallback, f"model asked to delete {len(drop)} of {total} sentences"
        )
        return

    kept = rebuild_body(paragraphs, drop)
    if len(kept) < MIN_KEPT_CHARS:
        _apply_fallback(article, fallback, "kept text would be too short")
        return

    article.refined_content = kept
    article.refine_failed = False
    logger.debug("Cleaned %s: dropped %d of %d sentences", article.link, len(drop), total)

    if not keep_title:
        candidate_title = data.get("title")
        if isinstance(candidate_title, str):
            cleaned = candidate_title.strip()
            if 0 < len(cleaned) <= MAX_TITLE_CHARS:
                article.refined_title = cleaned


def number_sentences(body: str) -> list[list[tuple[int, str]]]:
    """Group sentences by paragraph and number them 1..N across the whole body."""
    paragraphs: list[list[tuple[int, str]]] = []
    counter = 0
    for block in _PARAGRAPH_SPLIT_RE.split(body or ""):
        units = split_sentences(block)
        if not units:
            continue
        numbered = []
        for unit in units:
            counter += 1
            numbered.append((counter, unit))
        paragraphs.append(numbered)
    return paragraphs


def parse_drop_indices(data: dict, count: int) -> Optional[set[int]]:
    """Return the 1-based sentence indices to delete, or None when unusable."""
    raw = data.get("drop")
    if not isinstance(raw, list):
        return None

    indices: set[int] = set()
    for item in raw:
        if isinstance(item, bool):
            continue
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= count:
            indices.add(index)

    if raw and not indices:
        return None
    return indices


def rebuild_body(paragraphs: list[list[tuple[int, str]]], drop: set[int]) -> str:
    """Join the kept sentences, preserving paragraph breaks and original wording."""
    kept_paragraphs = []
    for paragraph in paragraphs:
        kept = [text for index, text in paragraph if index not in drop]
        if kept:
            kept_paragraphs.append(" ".join(kept))
    return "\n\n".join(kept_paragraphs)


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

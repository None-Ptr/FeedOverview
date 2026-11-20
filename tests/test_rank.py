"""LLM ranking plus its deterministic fallback."""

from datetime import datetime, timezone

from src.rank import fallback_order, rank_articles, select_for_llm

from tests.helpers import FakeLLM, make_article

DAY = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _article(title: str, link: str, day: int, weight: float = 1.0):
    return make_article(title=title, link=link, weight=weight, published=DAY.replace(day=day))


class TestFallbackOrdering:
    def test_no_llm_orders_by_time_then_weight(self, cfg):
        older = _article("older", "https://example.com/old", 1)
        newer = _article("newer", "https://example.com/new", 5)
        same_day_light = _article("light", "https://example.com/light", 5, weight=0.5)
        same_day_heavy = _article("heavy", "https://example.com/heavy", 5, weight=2.0)

        ranked = rank_articles([older, newer, same_day_light, same_day_heavy], cfg, None)

        assert [item.title for item in ranked] == ["heavy", "newer", "light", "older"]
        assert all(item.score is None for item in ranked)

    def test_unavailable_client_falls_back(self, cfg):
        articles = [_article("a", "https://example.com/a", 1), _article("b", "https://example.com/b", 2)]
        ranked = rank_articles(articles, cfg, FakeLLM(available=False))
        assert [item.title for item in ranked] == ["b", "a"]

    def test_fallback_order_is_pure(self, cfg):
        articles = [_article("a", "https://example.com/a", 1), _article("b", "https://example.com/b", 2)]
        assert fallback_order(articles)[0].title == "b"


class TestLLMRanking:
    def test_applies_llm_order_and_scores(self, cfg):
        low = _article("low", "https://example.com/low", 9)
        high = _article("high", "https://example.com/high", 1)

        def responder(messages):
            return {
                "order": [
                    {"index": 1, "score": 9.4, "reason": "deep dive"},
                    {"index": 0, "score": 2.0, "reason": "thin"},
                ]
            }

        ranked = rank_articles([low, high], cfg, FakeLLM(responder))

        assert [item.title for item in ranked] == ["high", "low"]
        assert ranked[0].score == 9.4
        assert ranked[0].reason == "deep dive"

    def test_invalid_response_falls_back(self, cfg):
        articles = [_article("a", "https://example.com/a", 1), _article("b", "https://example.com/b", 2)]
        ranked = rank_articles(articles, cfg, FakeLLM(lambda messages: None))
        assert [item.title for item in ranked] == ["b", "a"]
        assert all(item.score is None for item in ranked)

    def test_batches_are_merged(self, cfg):
        articles = [
            _article("a", "https://example.com/a", 1),
            _article("b", "https://example.com/b", 2),
            _article("c", "https://example.com/c", 3),
        ]
        seen_batches = []

        def responder(messages):
            batch = messages[-1]["content"].count("title: ")
            seen_batches.append(batch)
            return {"order": [{"index": 0, "score": 5.0, "reason": "ok"}]}

        rank_articles(articles, cfg, FakeLLM(responder))

        # cfg.llm.rank_batch_size == 2 -> batches of 2 and 1
        assert seen_batches == [2, 1]


class TestPrefilter:
    def test_limits_by_time_and_weight(self, cfg):
        articles = [
            _article("d1", "https://example.com/1", 1),
            _article("d5", "https://example.com/5", 5),
            _article("d3", "https://example.com/3", 3),
        ]
        kept = select_for_llm(articles, 2)
        assert [item.title for item in kept] == ["d5", "d3"]

    def test_no_limit_keeps_everything(self, cfg):
        articles = [_article("a", "https://example.com/a", 1)]
        assert select_for_llm(articles, 10) == articles

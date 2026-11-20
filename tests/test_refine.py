"""Refinement: per-item fallback, title handling and output caps."""

from src.refine import refine_articles

from tests.helpers import FakeLLM, make_article

LONG_BODY = "This is a reasonably long original body used for refinement tests. " * 6


def _refine_cfg(cfg, **overrides):
    cfg["llm"]["refine"].update(overrides)
    return cfg


class TestRefine:
    def test_refines_each_article(self, cfg):
        articles = [make_article(title="One", link="https://example.com/1", content=LONG_BODY)]

        llm = FakeLLM(lambda messages: {"refined": "A much shorter but faithful summary of the article."})
        refine_articles(articles, cfg, llm)

        assert articles[0].refined_content == "A much shorter but faithful summary of the article."
        assert articles[0].refine_failed is False

    def test_single_failure_falls_back_to_original_body(self, cfg):
        good = make_article(title="Good", link="https://example.com/good", content=LONG_BODY)
        bad = make_article(title="Boom", link="https://example.com/bad", content=LONG_BODY)

        def responder(messages):
            if "Boom" in messages[-1]["content"]:
                raise RuntimeError("upstream 429")
            return {"refined": "Condensed version of the good article, still factual."}

        refine_articles([good, bad], cfg, FakeLLM(responder))

        assert good.refined_content == "Condensed version of the good article, still factual."
        assert good.refine_failed is False
        assert bad.refine_failed is True
        assert bad.refined_content.startswith("This is a reasonably long original body")
        assert len(bad.refined_content) <= cfg["llm"]["refine"]["max_input_chars"]
        assert bad.effective_content  # the item is never dropped

    def test_invalid_json_falls_back(self, cfg):
        article = make_article(title="Bad JSON", link="https://example.com/bad", content=LONG_BODY)
        refine_articles([article], cfg, FakeLLM(lambda messages: None))

        assert article.refine_failed is True
        assert article.refined_content  # truncated original body

    def test_missing_refined_field_falls_back(self, cfg):
        article = make_article(title="Empty", link="https://example.com/empty", content=LONG_BODY)
        refine_articles([article], cfg, FakeLLM(lambda messages: {"title": "only a title"}))

        assert article.refine_failed is True
        assert article.refined_content

    def test_disabled_keeps_original_content(self, cfg):
        _refine_cfg(cfg, enabled=False)
        article = make_article(title="Skip", link="https://example.com/skip", content=LONG_BODY)

        llm = FakeLLM(lambda messages: {"refined": "should never be used"})
        refine_articles([article], cfg, llm)

        assert article.refined_content is None
        assert article.content == LONG_BODY
        assert llm.calls == []

    def test_unavailable_llm_is_skipped(self, cfg):
        article = make_article(title="Skip", link="https://example.com/skip", content=LONG_BODY)
        refine_articles([article], cfg, FakeLLM(available=False))
        assert article.refined_content is None

    def test_keep_title_ignores_returned_title(self, cfg):
        article = make_article(title="Original", link="https://example.com/t", content=LONG_BODY)
        llm = FakeLLM(lambda messages: {"refined": "Refined body long enough to be accepted.", "title": "New title"})
        refine_articles([article], cfg, llm)

        assert article.refined_title is None
        assert article.effective_title == "Original"

    def test_title_is_adopted_when_keep_title_false(self, cfg):
        _refine_cfg(cfg, keep_title=False)
        article = make_article(title="Original", link="https://example.com/t", content=LONG_BODY)
        llm = FakeLLM(lambda messages: {"refined": "Refined body long enough to be accepted.", "title": "Better title"})
        refine_articles([article], cfg, llm)

        assert article.effective_title == "Better title"

    def test_output_is_capped(self, cfg):
        _refine_cfg(cfg, max_output_chars=60)
        article = make_article(title="Long", link="https://example.com/l", content=LONG_BODY)
        llm = FakeLLM(lambda messages: {"refined": "A very long refined body " * 20})
        refine_articles([article], cfg, llm)

        assert len(article.refined_content) <= 60

    def test_short_body_is_not_sent_to_llm(self, cfg):
        article = make_article(title="Tiny", link="https://example.com/tiny", content="short")
        llm = FakeLLM(lambda messages: {"refined": "ignored"})
        refine_articles([article], cfg, llm)

        assert llm.calls == []
        assert article.refined_content is None

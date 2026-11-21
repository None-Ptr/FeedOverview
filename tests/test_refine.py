"""Refinement: sentence-index deletion, per-item fallback and title handling."""

from src.refine import refine_articles
from src.textutil import split_sentences

from tests.helpers import FakeLLM, make_article

BODY = (
    "The city council approved the budget on Tuesday. "
    "The plan adds two new bus lines next spring. "
    "Subscribe to our newsletter for more local news. "
    "Critics say the spending is too high. "
    "Follow us on social media for updates."
)


def _refine_cfg(cfg, **overrides):
    cfg["llm"]["refine"].update(overrides)
    return cfg


class TestRefine:
    def test_deletes_only_the_requested_sentences(self, cfg):
        article = make_article(title="Budget", link="https://example.com/1", content=BODY)

        llm = FakeLLM(lambda messages: {"drop": [3, 5]})
        refine_articles([article], cfg, llm)

        assert article.refined_content == (
            "The city council approved the budget on Tuesday. "
            "The plan adds two new bus lines next spring. "
            "Critics say the spending is too high."
        )
        assert article.refine_failed is False

    def test_prompt_numbers_every_sentence(self, cfg):
        article = make_article(title="Budget", link="https://example.com/n", content=BODY)

        llm = FakeLLM(lambda messages: {"drop": []})
        refine_articles([article], cfg, llm)

        prompt = llm.calls[0][-1]["content"]
        assert "1: The city council approved the budget on Tuesday." in prompt
        assert "3: Subscribe to our newsletter for more local news." in prompt
        assert "5: Follow us on social media for updates." in prompt
        assert '"drop"' in prompt

    def test_empty_drop_list_keeps_the_body(self, cfg):
        article = make_article(title="Budget", link="https://example.com/0", content=BODY)

        refine_articles([article], cfg, FakeLLM(lambda messages: {"drop": []}))

        assert article.refined_content == BODY
        assert article.refine_failed is False

    def test_paragraph_breaks_are_preserved(self, cfg):
        body = "The first paragraph starts here. It ends here.\n\nA second paragraph begins. It also ends."
        article = make_article(title="Two", link="https://example.com/p", content=body)

        refine_articles([article], cfg, FakeLLM(lambda messages: {"drop": [2]}))

        assert article.refined_content == "The first paragraph starts here.\n\nA second paragraph begins. It also ends."

    def test_string_indices_are_accepted(self, cfg):
        article = make_article(title="Budget", link="https://example.com/s", content=BODY)

        refine_articles([article], cfg, FakeLLM(lambda messages: {"drop": ["3"]}))

        assert "Subscribe" not in article.refined_content
        assert article.refine_failed is False

    def test_summarising_is_rejected(self, cfg):
        article = make_article(title="Budget", link="https://example.com/all", content=BODY)

        refine_articles([article], cfg, FakeLLM(lambda messages: {"drop": [1, 2, 3, 4, 5]}))

        assert article.refine_failed is True
        assert article.refined_content == BODY

    def test_out_of_range_indices_fall_back(self, cfg):
        article = make_article(title="Budget", link="https://example.com/oob", content=BODY)

        refine_articles([article], cfg, FakeLLM(lambda messages: {"drop": [99]}))

        assert article.refine_failed is True
        assert article.refined_content == BODY

    def test_single_failure_falls_back_to_original_body(self, cfg):
        good = make_article(title="Good", link="https://example.com/good", content=BODY)
        bad = make_article(title="Boom", link="https://example.com/bad", content=BODY)

        def responder(messages):
            if "Boom" in messages[-1]["content"]:
                raise RuntimeError("upstream 429")
            return {"drop": [3]}

        refine_articles([good, bad], cfg, FakeLLM(responder))

        assert "Subscribe" not in good.refined_content
        assert good.refine_failed is False
        assert bad.refine_failed is True
        assert bad.refined_content == BODY
        assert bad.effective_content  # the item is never dropped

    def test_invalid_json_falls_back(self, cfg):
        article = make_article(title="Bad JSON", link="https://example.com/bad", content=BODY)
        refine_articles([article], cfg, FakeLLM(lambda messages: None))

        assert article.refine_failed is True
        assert article.refined_content == BODY

    def test_missing_drop_field_falls_back(self, cfg):
        article = make_article(title="Empty", link="https://example.com/empty", content=BODY)
        refine_articles([article], cfg, FakeLLM(lambda messages: {"title": "only a title"}))

        assert article.refine_failed is True
        assert article.refined_content

    def test_disabled_keeps_original_content(self, cfg):
        _refine_cfg(cfg, enabled=False)
        article = make_article(title="Skip", link="https://example.com/skip", content=BODY)

        llm = FakeLLM(lambda messages: {"drop": [1]})
        refine_articles([article], cfg, llm)

        assert article.refined_content is None
        assert article.content == BODY
        assert llm.calls == []

    def test_unavailable_llm_is_skipped(self, cfg):
        article = make_article(title="Skip", link="https://example.com/skip", content=BODY)
        refine_articles([article], cfg, FakeLLM(available=False))
        assert article.refined_content is None

    def test_keep_title_ignores_returned_title(self, cfg):
        article = make_article(title="Original", link="https://example.com/t", content=BODY)
        llm = FakeLLM(lambda messages: {"drop": [3], "title": "New title"})
        refine_articles([article], cfg, llm)

        assert article.refined_title is None
        assert article.effective_title == "Original"

    def test_title_is_adopted_when_keep_title_false(self, cfg):
        _refine_cfg(cfg, keep_title=False)
        article = make_article(title="Original", link="https://example.com/t", content=BODY)
        llm = FakeLLM(lambda messages: {"drop": [3], "title": "Better title"})
        refine_articles([article], cfg, llm)

        assert article.effective_title == "Better title"

    def test_short_body_is_not_sent_to_llm(self, cfg):
        article = make_article(title="Tiny", link="https://example.com/tiny", content="short")
        llm = FakeLLM(lambda messages: {"drop": [1]})
        refine_articles([article], cfg, llm)

        assert llm.calls == []
        assert article.refined_content is None

    def test_two_sentence_body_is_not_sent_to_llm(self, cfg):
        body = (
            "This is the first sentence of a short article, long enough to pass the length check. "
            "And this is the second sentence of the same short article."
        )
        assert len(split_sentences(body)) == 2
        article = make_article(title="Two", link="https://example.com/two", content=body)
        llm = FakeLLM(lambda messages: {"drop": [1]})

        refine_articles([article], cfg, llm)

        assert llm.calls == []
        assert article.refined_content is None

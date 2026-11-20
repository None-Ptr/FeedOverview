"""End-to-end pipeline behaviour without any LLM credentials."""

import json
import xml.etree.ElementTree as ET

import src.main as main_module

from tests.helpers import make_article

BODY = "A long enough extracted article body for the pipeline tests."


def _fake_enrich(articles, extract_cfg):
    """Simulate trafilatura: items without a body are flagged and get dropped."""
    for article in articles:
        if not article.content:
            article.extraction_failed = True


def _prepare(cfg, tmp_path, monkeypatch):
    cfg["output_path"] = str(tmp_path / "docs" / "feed.xml")
    cfg["seen_path"] = str(tmp_path / "cache" / "seen.json")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(main_module, "enrich_all", _fake_enrich)


def test_run_without_llm_writes_valid_feed(cfg, tmp_path, monkeypatch):
    _prepare(cfg, tmp_path, monkeypatch)
    articles = [
        make_article(title="First", link="https://example.com/first", content=BODY),
        make_article(title="Second", link="https://example.com/second", content=BODY),
    ]
    monkeypatch.setattr(main_module, "fetch_all", lambda c: articles)

    assert main_module.run(cfg) == 0

    root = ET.parse(cfg["output_path"]).getroot()
    items = root.findall("./channel/item")
    assert len(items) == 2
    assert "AI 精简版" in items[0].findtext("description")

    seen = json.loads(open(cfg["seen_path"], encoding="utf-8").read())
    assert "https://example.com/first" in seen["seen"]


def test_second_run_does_not_repeat_links(cfg, tmp_path, monkeypatch):
    _prepare(cfg, tmp_path, monkeypatch)
    articles = [make_article(title="First", link="https://example.com/first", content=BODY)]
    monkeypatch.setattr(main_module, "fetch_all", lambda c: articles)

    assert main_module.run(cfg) == 0
    assert len(ET.parse(cfg["output_path"]).getroot().findall("./channel/item")) == 1

    assert main_module.run(cfg) == 0
    assert ET.parse(cfg["output_path"]).getroot().findall("./channel/item") == []


def test_article_without_body_is_dropped_not_summarised(cfg, tmp_path, monkeypatch):
    _prepare(cfg, tmp_path, monkeypatch)
    articles = [
        make_article(title="Ok", link="https://example.com/ok", content=BODY),
        make_article(title="No body", link="https://example.com/no-body", summary="RSS summary only."),
    ]
    monkeypatch.setattr(main_module, "fetch_all", lambda c: articles)

    assert main_module.run(cfg) == 0

    items = ET.parse(cfg["output_path"]).getroot().findall("./channel/item")
    assert len(items) == 1
    assert items[0].findtext("title") == "Ok"

    # The dropped link is not marked as seen, so a transient block can recover.
    seen = json.loads(open(cfg["seen_path"], encoding="utf-8").read())
    assert "https://example.com/no-body" not in seen["seen"]


def test_all_extraction_failures_still_produce_a_valid_feed(cfg, tmp_path, monkeypatch):
    _prepare(cfg, tmp_path, monkeypatch)
    articles = [make_article(title="Blocked", link="https://example.com/blocked", summary="Summary.")]
    monkeypatch.setattr(main_module, "fetch_all", lambda c: articles)

    assert main_module.run(cfg) == 0

    root = ET.parse(cfg["output_path"]).getroot()
    assert root.findall("./channel/item") == []
    assert root.findtext("./channel/title") == "Test Feed"

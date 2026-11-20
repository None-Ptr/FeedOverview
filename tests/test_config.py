"""Configuration loading: defaults, aliases, coercion and bad input."""

import pytest

from src.config import DEFAULTS, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg["max_items"] == DEFAULTS["max_items"]
    assert cfg["feeds"] == []
    assert cfg["extract"]["timeout"] == 15


def test_partial_config_keeps_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, "max_items: 7\n"))
    assert cfg["max_items"] == 7
    assert cfg["llm"]["refine"]["max_output_chars"] == 800
    assert cfg["extract"]["fast"] is True


def test_title_is_accepted_as_name_alias(tmp_path):
    path = _write(
        tmp_path,
        "feeds:\n"
        "  - title: 'NPR Books'\n"
        "    url: 'https://feeds.npr.org/1032/rss.xml'\n"
        "    category: 'Books'\n",
    )
    cfg = load_config(path)
    assert cfg["feeds"] == [
        {
            "name": "NPR Books",
            "url": "https://feeds.npr.org/1032/rss.xml",
            "weight": 1.0,
            "category": "Books",
            "category_label": "Books",
        }
    ]
    assert [category.key for category in cfg["categories"]] == ["Books"]


def test_feed_without_url_is_dropped(tmp_path):
    path = _write(tmp_path, "feeds:\n  - name: 'broken'\n  - name: 'ok'\n    url: 'https://a.com/rss'\n")
    cfg = load_config(path)
    assert [feed["name"] for feed in cfg["feeds"]] == ["ok"]


def test_feeds_default_weight(tmp_path):
    cfg = load_config(_write(tmp_path, "feeds:\n  - name: 'a'\n    url: 'https://a.com/rss'\n"))
    assert cfg["feeds"][0]["weight"] == 1.0


def test_non_mapping_yaml_raises(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, "- just\n- a\n- list\n"))


def test_empty_file_uses_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg["max_items"] == DEFAULTS["max_items"]


def test_env_overrides_llm_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("LLM_MODEL", "glm-4-flash")
    cfg = load_config(_write(tmp_path, "llm:\n  model: 'gpt-4o-mini'\n"))
    assert cfg["llm"]["api_key"] == "sk-test"
    assert cfg["llm"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert cfg["llm"]["model"] == "glm-4-flash"


def test_numeric_strings_are_coerced(tmp_path):
    cfg = load_config(_write(tmp_path, "max_items: '9'\nmax_items_per_source: '4'\n"))
    assert cfg["max_items"] == 9
    assert cfg["max_items_per_source"] == 4


def test_non_positive_limits_are_clamped(tmp_path):
    cfg = load_config(
        _write(tmp_path, "max_items: 0\nmax_candidates_per_category: -3\nseen_retention_days: 0\n")
    )
    assert cfg["max_items"] == 1
    assert cfg["max_candidates_per_category"] == 1
    assert cfg["seen_retention_days"] == 1


def test_declared_categories_are_resolved_case_insensitively(tmp_path):
    path = _write(
        tmp_path,
        "categories:\n"
        "  - key: News\n"
        "    label: 新闻\n"
        "  - key: Science\n"
        "    label: 科学\n"
        "feeds:\n"
        "  - name: 'NPR'\n"
        "    url: 'https://a.com/rss'\n"
        "    category: 'news'\n",
    )
    cfg = load_config(path)
    assert [(category.key, category.label) for category in cfg["categories"]] == [
        ("News", "新闻"),
        ("Science", "科学"),
    ]
    assert cfg["feeds"][0]["category"] == "News"
    assert cfg["feeds"][0]["category_label"] == "新闻"


def test_categories_are_derived_from_feeds_in_order(tmp_path):
    path = _write(
        tmp_path,
        "feeds:\n"
        "  - name: 'a'\n"
        "    url: 'https://a.com/rss'\n"
        "    category: 'Science'\n"
        "  - name: 'b'\n"
        "    url: 'https://b.com/rss'\n"
        "    category: 'News'\n"
        "  - name: 'c'\n"
        "    url: 'https://c.com/rss'\n"
        "    category: 'Science'\n",
    )
    cfg = load_config(path)
    assert [category.key for category in cfg["categories"]] == ["Science", "News"]


def test_unknown_category_falls_back_to_uncategorized(tmp_path):
    path = _write(
        tmp_path,
        "categories:\n"
        "  - key: News\n"
        "    label: 新闻\n"
        "feeds:\n"
        "  - name: 'a'\n"
        "    url: 'https://a.com/rss'\n"
        "    category: 'Astrology'\n",
    )
    cfg = load_config(path)
    assert [category.key for category in cfg["categories"]] == ["News", "Uncategorized"]
    assert cfg["feeds"][0]["category"] == "Uncategorized"
    assert cfg["feeds"][0]["category_label"] == "未分类"


def test_empty_output_path_raises(tmp_path):
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, 'output_path: ""\n'))

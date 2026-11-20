"""HTTP helper: retries, timeouts, and permanently failing URLs."""

import requests

import src.http as http_module


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"ok"):
        self.status_code = status_code
        self.content = content


def _patch(monkeypatch, outcomes):
    """Feed a sequence of responses/exceptions into requests.get and record calls."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(http_module.requests, "get", fake_get)
    monkeypatch.setattr(http_module.time, "sleep", lambda _seconds: None)
    return calls


class TestHttpGet:
    def test_success_first_try(self, monkeypatch):
        calls = _patch(monkeypatch, [FakeResponse()])
        response = http_module.get("https://example.com", timeout=3, retries=2)
        assert response is not None and response.status_code == 200
        assert len(calls) == 1
        assert calls[0]["timeout"] == 3
        assert "User-Agent" in calls[0]["headers"]

    def test_retries_server_errors(self, monkeypatch):
        calls = _patch(monkeypatch, [FakeResponse(500), FakeResponse(200)])
        response = http_module.get("https://example.com", retries=2)
        assert response is not None and response.status_code == 200
        assert len(calls) == 2

    def test_retries_rate_limit(self, monkeypatch):
        calls = _patch(monkeypatch, [FakeResponse(429), FakeResponse(200)])
        response = http_module.get("https://example.com", retries=2)
        assert response is not None and response.status_code == 200
        assert len(calls) == 2

    def test_gives_up_after_retries(self, monkeypatch):
        calls = _patch(monkeypatch, [requests.ConnectionError("boom")])
        assert http_module.get("https://example.com", retries=2) is None
        assert len(calls) == 3

    def test_404_is_not_retried(self, monkeypatch):
        calls = _patch(monkeypatch, [FakeResponse(404)])
        assert http_module.get("https://example.com", retries=2) is None
        assert len(calls) == 1

    def test_timeout_exception_is_swallowed(self, monkeypatch):
        _patch(monkeypatch, [requests.Timeout("slow")])
        assert http_module.get("https://example.com", retries=1) is None

    def test_zero_retries_means_one_attempt(self, monkeypatch):
        calls = _patch(monkeypatch, [FakeResponse(503)])
        assert http_module.get("https://example.com", retries=0) is None
        assert len(calls) == 1

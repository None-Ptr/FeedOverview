"""LLM client plumbing and tolerant JSON parsing."""

from types import SimpleNamespace

from src.llm import LLMClient, is_rate_limit_error, parse_json_object


class TestParseJsonObject:
    def test_plain_json(self):
        assert parse_json_object('{"order": []}') == {"order": []}

    def test_fenced_json(self):
        raw = '```json\n{"refined": "text"}\n```'
        assert parse_json_object(raw) == {"refined": "text"}

    def test_json_surrounded_by_prose(self):
        raw = 'Sure, here it is: {"refined": "text"} hope that helps'
        assert parse_json_object(raw) == {"refined": "text"}

    def test_garbage_returns_none(self):
        assert parse_json_object("no json here") is None
        assert parse_json_object("") is None
        assert parse_json_object(None) is None

    def test_json_array_is_rejected(self):
        assert parse_json_object('[{"index": 0}]') is None


class TestLLMClient:
    def test_unavailable_without_api_key(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        client = LLMClient({"model": "test"})
        assert client.available is False
        assert "LLM_API_KEY" in client.unavailable_reason

    def test_available_with_api_key(self):
        client = LLMClient(
            {"model": "test", "timeout": 5},
            api_key="sk-test",
            base_url="http://127.0.0.1:9/v1",
        )
        assert client.available is True
        # Constructing the SDK client must not perform any network I/O.
        assert client._ensure_client() is not None

    def test_env_model_overrides_config(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "from-env")
        client = LLMClient({"model": "from-config"}, api_key="sk-test")
        assert client.model == "from-env"


class TestExtraBody:
    class _FakeCompletions:
        def __init__(self):
            self.kwargs: dict = {}

        def create(self, **kwargs):
            self.kwargs = kwargs
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _client_with_fake(self, llm_cfg):
        client = LLMClient(llm_cfg, api_key="sk-test")
        fake = self._FakeCompletions()
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        return client, fake

    def test_extra_body_is_forwarded(self):
        client, fake = self._client_with_fake(
            {"model": "glm-4.5-flash", "extra_body": {"thinking": {"type": "disabled"}}}
        )
        assert client.chat([{"role": "user", "content": "hi"}]) == '{"ok": true}'
        assert fake.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_no_extra_body_by_default(self):
        client, fake = self._client_with_fake({"model": "test"})
        client.chat([{"role": "user", "content": "hi"}])
        assert "extra_body" not in fake.kwargs


class TestRateLimitDetection:
    def test_status_code_429(self):
        class ApiError(Exception):
            status_code = 429

        assert is_rate_limit_error(ApiError("slow down")) is True

    def test_message_mentions_rate_limit(self):
        assert is_rate_limit_error(RuntimeError("Error code: 429 - too many requests")) is True
        assert is_rate_limit_error(RuntimeError("concurrent limit reached")) is True

    def test_zhipu_concurrency_code(self):
        assert is_rate_limit_error(RuntimeError("Error code: 1302 - concurrency limit")) is True

    def test_other_failures_are_not_rate_limits(self):
        assert is_rate_limit_error(RuntimeError("connection reset")) is False

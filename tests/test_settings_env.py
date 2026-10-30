import pytest

from opsloop.core.config import Settings


def test_cors_origins_accept_comma_list_json_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSLOOP_CORS_ORIGINS", "http://localhost:3000, https://opsloop.vercel.app")
    assert Settings(_env_file=None).cors_origins == [
        "http://localhost:3000",
        "https://opsloop.vercel.app",
    ]
    monkeypatch.setenv("OPSLOOP_CORS_ORIGINS", '["https://a.example"]')
    assert Settings(_env_file=None).cors_origins == ["https://a.example"]
    monkeypatch.setenv("OPSLOOP_CORS_ORIGINS", "")
    assert Settings(_env_file=None).cors_origins == ["http://localhost:3000"]


def test_api_tokens_parse_json_and_tolerate_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSLOOP_API_TOKENS", '{"t1": {"name": "alice", "role": "approver"}}')
    assert Settings(_env_file=None).api_tokens == {"t1": {"name": "alice", "role": "approver"}}
    monkeypatch.setenv("OPSLOOP_API_TOKENS", "")
    assert Settings(_env_file=None).api_tokens == {}

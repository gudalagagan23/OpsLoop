import pytest
from starlette.testclient import TestClient

from opsloop.deploy.clients import DockerCandidateBackend, HttpRouter
from opsloop.deploy.router import Split, create_router_app


def test_http_router_drives_the_real_router_admin_api(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_router_app("http://stable", "http://candidate", Split())
    tc = TestClient(app, base_url="http://router")
    router = HttpRouter("http://router")
    monkeypatch.setattr(router, "_http", tc)
    router.set_split(25)
    assert tc.get("/_router/status").json()["candidate_pct"] == 25
    router.set_split(0)
    assert tc.get("/_router/status").json()["candidate_pct"] == 0
    assert router.samples(0.0) == []


def test_docker_candidate_is_an_explicit_stub() -> None:
    backend = DockerCandidateBackend()
    with pytest.raises(NotImplementedError):
        backend.start_candidate("patch", "hash", [])
    assert backend.candidate_healthy() is False

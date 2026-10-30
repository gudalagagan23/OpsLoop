import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from opsloop.deploy.router import Split, classify, create_router_app


def _upstream(name: str, status: int = 200) -> FastAPI:
    app = FastAPI()

    @app.api_route("/{p:path}", methods=["GET", "POST", "PATCH"])
    def any_route(p: str) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"from": name}, status_code=status)

    return app


class _Routing(httpx.AsyncBaseTransport):
    """Sends stable-host requests to one app and candidate-host requests to another."""

    def __init__(self, apps: dict[str, FastAPI]) -> None:
        self.transports = {h: httpx.ASGITransport(app=a) for h, a in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.transports[request.url.host].handle_async_request(request)


def _setup(cand_status: int = 200) -> tuple[TestClient, Split]:
    apps = {
        "stable": _upstream("stable"),
        "candidate": _upstream("candidate", cand_status),
    }
    upstream = httpx.AsyncClient(transport=_Routing(apps), timeout=10.0)
    split = Split()
    router = create_router_app("http://stable", "http://candidate", split, upstream=upstream)
    return TestClient(router), split


@pytest.fixture
def routed() -> tuple[TestClient, Split]:
    return _setup()


def test_zero_split_sends_everything_to_stable(routed: tuple[TestClient, Split]) -> None:
    client, split = routed
    names = {
        client.get(f"/projects/1/tasks?i={i}", headers={"x-request-id": str(i)}).json()["from"]
        for i in range(40)
    }
    assert names == {"stable"}
    assert all(s.target == "stable" for s in split.since(0))


def test_split_is_deterministic_per_request_id(routed: tuple[TestClient, Split]) -> None:
    _client, split = routed
    split.set(25)
    first = [split.choose(str(i)) for i in range(500)]
    assert first == [split.choose(str(i)) for i in range(500)]
    assert 0.18 < first.count("candidate") / len(first) < 0.32


def test_traffic_share_matches_the_split(routed: tuple[TestClient, Split]) -> None:
    client, split = routed
    split.set(25)
    for i in range(300):
        client.get(f"/projects/1/tasks?i={i}", headers={"x-request-id": f"r{i}"})
    recorded = split.since(0)
    share = sum(1 for s in recorded if s.target == "candidate") / len(recorded)
    assert 0.15 < share < 0.35
    assert {s.kind for s in recorded} == {"list"}


def test_candidate_errors_are_recorded_against_the_candidate() -> None:
    client, split = _setup(cand_status=500)
    split.set(100)
    for i in range(10):
        client.get(f"/projects/1/tasks?i={i}", headers={"x-request-id": f"e{i}"})
    recorded = split.since(0)
    assert {s.target for s in recorded} == {"candidate"}
    assert {s.status for s in recorded} == {500}


def test_split_endpoint_validates_range_and_candidate_presence(
    routed: tuple[TestClient, Split],
) -> None:
    client, _split = routed
    assert client.post("/_router/split/101").status_code == 422
    assert client.post("/_router/split/50").json() == {"candidate_pct": 50}
    assert client.get("/_router/status").json() == {"candidate_pct": 50, "has_candidate": True}


def test_without_a_candidate_a_split_is_refused() -> None:
    apps = {"stable": _upstream("stable")}
    upstream = httpx.AsyncClient(transport=_Routing(apps), timeout=10.0)
    router = create_router_app("http://stable", None, Split(), upstream=upstream)
    client = TestClient(router)
    assert client.post("/_router/split/5").status_code == 409
    assert client.post("/_router/split/0").status_code == 200


def test_samples_endpoint_exposes_what_the_controller_reads(
    routed: tuple[TestClient, Split],
) -> None:
    client, _split = routed
    client.get("/projects/1/tasks", headers={"x-request-id": "s1"})
    rows = client.get("/_router/samples").json()
    assert rows and {"ts", "target", "status", "ms", "kind"} <= set(rows[0])


def test_classify() -> None:
    assert classify("POST", "/projects/1/tasks") == "create"
    assert classify("GET", "/projects/1/tasks") == "list"
    assert classify("GET", "/projects") == "other"

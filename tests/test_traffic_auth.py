import asyncio

import httpx
import jwt

from taskflow.traffic.model import Event, Profile
from taskflow.traffic.runner import TenantSession, TrafficRunner


def _token(n: int) -> str:
    return jwt.encode({"sub": "1", "tid": 1, "plan": "pro", "n": n}, "x", algorithm="HS256")


def test_expired_token_is_renewed_instead_of_silently_failing() -> None:
    old, new = _token(1), _token(2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": new})
        if request.headers.get("authorization") == f"Bearer {old}":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json=[])

    runner = TrafficRunner("http://api", Profile(), seed=1, tenants=1)
    runner.sessions[1] = TenantSession(1, "pro", {"Authorization": f"Bearer {old}"}, [1])

    async def go() -> None:
        async with httpx.AsyncClient(
            base_url="http://api", transport=httpx.MockTransport(handler)
        ) as c:
            await runner._do(c, Event(0.0, 1, "list_projects", 0.1))

    asyncio.run(go())
    assert runner.stats["token_renewed"] == 1
    assert runner.sessions[1].headers["Authorization"] == f"Bearer {new}"

"""Clients the rollout controller uses: the traffic router's admin API over HTTP, and the
candidate backend (STUB: container start-up is not yet exercised against live Docker)."""

import httpx

from opsloop.deploy.health import Sample


class HttpRouter:
    """Implements the rollout controller's Router interface against opsloop/deploy/router.py."""

    def __init__(self, base_url: str, timeout_s: float = 5.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s)

    def set_split(self, candidate_pct: int) -> None:
        r = self._http.post(f"/_router/split/{candidate_pct}")
        r.raise_for_status()

    def samples(self, since: float) -> list[Sample]:
        r = self._http.get("/_router/samples", params={"since": since})
        r.raise_for_status()
        return [Sample(**row) for row in r.json()]

    def close(self) -> None:
        self._http.close()


class HttpHealth:
    """Candidate and stable health, checked through their /health endpoints."""

    def __init__(self, candidate_url: str, stable_url: str, timeout_s: float = 3.0) -> None:
        self.candidate_url, self.stable_url = candidate_url, stable_url
        self._http = httpx.Client(timeout=timeout_s)

    def _ok(self, url: str) -> bool:
        try:
            return self._http.get(f"{url}/health").status_code == 200
        except httpx.HTTPError:
            return False

    def candidate_ok(self) -> bool:
        return self._ok(self.candidate_url)

    def stable_ok(self) -> bool:
        return self._ok(self.stable_url)


class DockerCandidateBackend:
    """STUB: builds and runs a candidate TaskFlow container from the stable commit plus the
    approved patch. Not yet exercised against live Docker; Stage 3's gate test covers it."""

    def __init__(self, image: str = "opsloop-sandbox:dev") -> None:
        self.image = image
        self.running = False

    def start_candidate(self, patch: str, version_hash: str, flags: list[str]) -> str:
        # STUB: raises so no caller can mistake this for a working candidate.
        raise NotImplementedError("DockerCandidateBackend.start_candidate is not implemented yet")

    def candidate_healthy(self) -> bool:
        return False

    def stable_healthy(self) -> bool:
        return True

    def stop_candidate(self) -> None:
        self.running = False

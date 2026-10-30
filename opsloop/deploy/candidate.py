"""Candidate backend: runs a TaskFlow container from the stable code plus one approved patch.

The patch is applied to a fresh copy of the committed `taskflow/` tree in .candidates/<hash>/.
That copy is mounted read-only into a container that joins the compose network, so the
candidate reads and writes the same simulated database as stable. Container flags come from
the scenario, exactly as in the sandbox. Only the patch's version hash names the directory,
so two candidates for two patches never share files."""

import io
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import httpx

from opsloop.sandbox.runner import REPO_ROOT, export_tree

CANDIDATE_ROOT = REPO_ROOT / ".candidates"
CONTAINER = "opsloop-taskflow-candidate"
NETWORK = "opsloop_default"
IMAGE = "opsloop-python:dev"
DB_URL = "postgresql+psycopg://opsloop:opsloop@postgres:5432/opsloop"
HOST_PORT = 8081


class CandidateError(Exception):
    pass


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(  # noqa: S603 - fixed docker CLI
        ["docker", *args],
        capture_output=True,
        timeout=180,
        check=False,  # noqa: S607
    )
    if check and proc.returncode != 0:
        raise CandidateError(
            f"docker {args[0]} failed: {proc.stderr.decode(errors='replace')[:400]}"
        )
    return proc


def _rm(path: Path) -> None:
    def clear(func, p, _exc):  # type: ignore[no-untyped-def]
        import os
        import stat

        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=clear)


def materialise(patch: str, version_hash: str) -> Path:
    """Write stable's taskflow/ plus the patch into .candidates/<hash>/taskflow."""
    target = CANDIDATE_ROOT / version_hash[:16]
    if target.exists():
        _rm(target)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(export_tree(None)), mode="r") as tar:
        tar.extractall(target, filter="data")
    (target / "candidate.patch").write_text(patch, encoding="utf-8", newline="\n")
    # git apply needs a repository; a throwaway one keeps the host repo untouched.
    subprocess.run(["git", "init", "-q"], cwd=target, check=True, capture_output=True)  # noqa: S603, S607
    subprocess.run(["git", "add", "-A"], cwd=target, check=True, capture_output=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603, S607
        [
            "git",
            "-c",
            "user.name=opsloop",
            "-c",
            "user.email=opsloop@invalid",
            "commit",
            "-qm",
            "base",
        ],  # noqa: S607
        cwd=target,
        check=True,
        capture_output=True,
    )
    res = subprocess.run(  # noqa: S603, S607
        ["git", "apply", "--whitespace=nowarn", "candidate.patch"],
        cwd=target,
        capture_output=True,  # noqa: S607
    )
    if res.returncode != 0:
        raise CandidateError(f"patch does not apply: {res.stderr.decode(errors='replace')[:400]}")
    (target / "candidate.patch").unlink()
    _rm(target / ".git")
    return target


class DockerCandidateBackend:
    """Implements the rollout controller's Backend interface."""

    def __init__(self, flags: list[str] | None = None, health_timeout_s: float = 90.0) -> None:
        self.flags = flags or []
        self.health_timeout_s = health_timeout_s
        self.url = f"http://localhost:{HOST_PORT}"
        self._http = httpx.Client(timeout=3.0)
        self.tree: Path | None = None

    def start_candidate(self, patch: str, version_hash: str, flags: list[str]) -> str:
        self.stop_candidate()
        self.tree = materialise(patch, version_hash)
        _docker(
            "run",
            "-d",
            "--name",
            CONTAINER,
            "--network",
            NETWORK,
            "-p",
            f"{HOST_PORT}:8080",
            "-v",
            f"{self.tree / 'taskflow'}:/work/taskflow:ro",
            "-w",
            "/work",
            "-e",
            "PYTHONPATH=/work",
            "-e",
            f"TASKFLOW_DATABASE_URL={DB_URL}",
            "-e",
            f"TASKFLOW_FLAGS={','.join(flags or self.flags)}",
            "-e",
            "CONTROL_PORT=9101",
            IMAGE,
            "python",
            "-m",
            "taskflow.api",
        )
        return CONTAINER

    def candidate_healthy(self) -> bool:
        try:
            return self._http.get(f"{self.url}/health").status_code == 200
        except httpx.HTTPError:
            return False

    def wait_healthy(self) -> bool:
        deadline = time.time() + self.health_timeout_s
        while time.time() < deadline:
            if self.candidate_healthy():
                return True
            time.sleep(2)
        return False

    def stable_healthy(self) -> bool:
        try:
            return self._http.get("http://localhost:8080/health").status_code == 200
        except httpx.HTTPError:
            return False

    def stop_candidate(self) -> None:
        _docker("rm", "-f", CONTAINER, check=False)

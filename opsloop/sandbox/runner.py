"""Docker sandbox: an isolated place to apply and run a proposed patch.

Isolation: containers join an *internal* docker network (no route to the internet); the only
other host on it is a throwaway Postgres. No secrets are passed in. CPU, memory and process
limits apply. The workspace is built from a clean export of the deployed code: only
`taskflow/` and the visible tests (hidden tests and OpsLoop's own code are never copied in).
"""

import io
import logging
import os
import subprocess
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

log = logging.getLogger("opsloop.sandbox")

IMAGE = os.environ.get("OPSLOOP_SANDBOX_IMAGE", "opsloop-sandbox:dev")
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_PATHS = ("taskflow", "sandbox_tests/visible")
DB_USER = DB_NAME = DB_PASSWORD = "sbx"
_SKIP_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class ExecResult:
    rc: int
    out: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _docker(
    *args: str, input: bytes | None = None, timeout: float = 120, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(  # noqa: S603 - fixed docker CLI, arguments are not shell-parsed
        ["docker", *args], input=input, capture_output=True, timeout=timeout, check=False
    )
    if check and proc.returncode != 0:
        raise SandboxError(
            f"docker {' '.join(args[:3])} failed: {proc.stderr.decode(errors='replace')[:500]}"
        )
    return proc


def image_exists() -> bool:
    return _docker("image", "inspect", IMAGE, check=False).returncode == 0


def build_image() -> None:
    log.info("building sandbox image %s", IMAGE)
    _docker("build", "-f", "docker/sandbox.Dockerfile", "-t", IMAGE, ".", timeout=900)


def git_head() -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def export_tree(ref: str | None = None) -> bytes:
    """tar of the workspace paths: from a git ref (deployed commit) or the working tree."""
    if ref is not None:
        out = subprocess.run(  # noqa: S603
            ["git", "archive", "--format=tar", ref, *WORKSPACE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
        return out.stdout
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in WORKSPACE_PATHS:
            tar.add(
                REPO_ROOT / rel,
                arcname=rel,
                filter=lambda ti: (
                    None
                    if _SKIP_PARTS & set(Path(ti.name).parts) or ti.name.endswith(".pyc")
                    else ti
                ),
            )
    return buf.getvalue()


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Sandbox:
    def __init__(
        self, *, flags: list[str] | None = None, memory: str = "1g", cpus: str = "1.5"
    ) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.flags = flags or []
        self.memory, self.cpus = memory, cpus
        self.net = f"opsloop-sbx-{self.id}"
        self.db = f"opsloop-sbx-db-{self.id}"
        self.ws = f"opsloop-sbx-ws-{self.id}"
        self._started = False

    # ---- lifecycle -------------------------------------------------------------------

    def __enter__(self) -> "Sandbox":
        try:
            _docker("network", "create", "--internal", self.net)
            _docker(
                "run",
                "-d",
                "--name",
                self.db,
                "--network",
                self.net,
                "--network-alias",
                "db",
                "-e",
                f"POSTGRES_USER={DB_USER}",
                "-e",
                f"POSTGRES_PASSWORD={DB_PASSWORD}",
                "-e",
                f"POSTGRES_DB={DB_NAME}",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=1g",
                "--memory",
                "1g",
                "--pids-limit",
                "256",
                "pgvector/pgvector:pg16",
                "-c",
                "fsync=off",
                "-c",
                "synchronous_commit=off",
            )
            _docker(
                "run",
                "-d",
                "--name",
                self.ws,
                "--network",
                self.net,
                "--memory",
                self.memory,
                "--cpus",
                self.cpus,
                "--pids-limit",
                "256",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-e",
                f"TASKFLOW_DATABASE_URL=postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@db:5432/{DB_NAME}",
                "-e",
                f"TASKFLOW_FLAGS={','.join(self.flags)}",
                IMAGE,
            )
            self._started = True
            self._wait_db()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(
        self, et: type[BaseException] | None, ev: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        for name in (self.ws, self.db):
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", self.net, check=False)

    def _wait_db(self, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (
                _docker(
                    "exec", self.db, "pg_isready", "-U", DB_USER, "-d", DB_NAME, check=False
                ).returncode
                == 0
            ):
                time.sleep(1)  # pg_isready can answer during the init restart
                if (
                    _docker("exec", self.db, "pg_isready", "-U", DB_USER, check=False).returncode
                    == 0
                ):
                    return
            time.sleep(1)
        raise SandboxError("sandbox database did not become ready")

    # ---- files and commands ------------------------------------------------------------

    def load_tree(self, tar_bytes: bytes) -> None:
        _docker("cp", "-", f"{self.ws}:/work", input=tar_bytes)
        for cmd in (
            "git init -q",
            "git add -A",
            "git -c user.name=sandbox -c user.email=sandbox@invalid commit -qm base",
        ):
            self.exec(cmd, timeout=60)

    def put(self, files: dict[str, bytes], dest: str) -> None:
        _docker("exec", self.ws, "mkdir", "-p", dest)
        _docker("cp", "-", f"{self.ws}:{dest}", input=tar_of(files))

    def exec(
        self,
        cmd: str | list[str],
        *,
        timeout: float = 120,
        env: dict[str, str] | None = None,
        workdir: str = "/work",
    ) -> ExecResult:
        argv = ["bash", "-c", cmd] if isinstance(cmd, str) else cmd
        flags = [f for k, v in (env or {}).items() for f in ("-e", f"{k}={v}")]
        started = time.time()
        try:
            proc = _docker(
                "exec",
                "-w",
                workdir,
                *flags,
                self.ws,
                "timeout",
                "-s",
                "KILL",
                str(int(timeout)),
                *argv,
                timeout=timeout + 30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(124, f"timed out after {timeout:.0f}s", time.time() - started)
        out = (proc.stdout + proc.stderr).decode(errors="replace")
        return ExecResult(proc.returncode, out, time.time() - started)

    def read_file(self, path: str, limit: int = 60_000) -> str:
        res = self.exec(["head", "-c", str(limit), "--", path], timeout=20)
        if not res.ok:
            raise SandboxError(f"cannot read {path}: {res.out.strip()[:200]}")
        return res.out

    def write_file(self, path: str, content: str) -> None:
        name = path.lstrip("/")
        parent = os.path.dirname(f"/work/{name}") or "/work"
        _docker("exec", self.ws, "mkdir", "-p", parent)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = content.encode()
            info = tarfile.TarInfo(os.path.basename(name))
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        _docker("cp", "-", f"{self.ws}:{parent}", input=buf.getvalue())

    def list_dir(self, path: str = ".") -> str:
        res = self.exec(
            [
                "find",
                path,
                "-not",
                "-path",
                "*/.git*",
                "-not",
                "-name",
                "__pycache__",
                "-maxdepth",
                "3",
                "-type",
                "f",
            ],
            timeout=20,
        )
        return res.out

    def diff(self) -> str:
        """Everything changed relative to the base commit, including new files."""
        self.exec("git add -A", timeout=30)
        res = self.exec("git diff --cached --binary --no-color HEAD", timeout=30)
        return res.out

    def apply_patch(self, patch: str) -> ExecResult:
        _docker("cp", "-", f"{self.ws}:/tmp", input=tar_of({"patch.diff": patch.encode()}))
        check = self.exec("git apply --check --whitespace=nowarn /tmp/patch.diff", timeout=30)
        if not check.ok:
            return check
        return self.exec("git apply --whitespace=nowarn /tmp/patch.diff", timeout=30)

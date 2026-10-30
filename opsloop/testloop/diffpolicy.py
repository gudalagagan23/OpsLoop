"""Diff policy: deterministic checks on a proposed patch, run before it can reach a human.

Rules (spec section 11): allowed paths only, no test or OpsLoop edits, no new network calls, no
secrets, no auth weakening, no binary/symlink/mode changes. Plain code, outside any agent.
"""

import posixpath
import re
from dataclasses import dataclass

from opsloop.agents.profiles import SpecialistProfile

_TEST_PATH = re.compile(
    r"(^|/)(tests?|tests_hidden|sandbox_tests|__tests__)(/|$)|(^|/)(test_[^/]*|conftest)\.py$|_test\.py$"
)

_NETWORK = re.compile(
    r"^\s*(import|from)\s+(requests|urllib3?|socket|http\.client|aiohttp|httpx|ftplib|smtplib"
    r"|telnetlib|paramiko|websockets?)\b"
    r"|\burlopen\s*\(|\bsocket\.socket\s*\(|\brequests\.(get|post|put|patch|delete|head|request)\s*\("
    r"|\bhttpx\.(get|post|put|patch|delete|Client|AsyncClient)\s*\("
)
_EXEC = re.compile(
    r"\bos\.(system|popen|exec\w*|spawn\w*)\s*\(|\bsubprocess\b|\beval\s*\(|\bexec\s*\("
    r"|__import__\s*\(|\bpickle\.loads?\s*\(|\bmarshal\.loads?\s*\("
)
_SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:(api[_-]?key|secret|passw(or)?d|token))\s*[:=]\s*[\"'][^\"']{8,}[\"']"
    r"|(os\.environ(\.get)?|os\.getenv)\s*[\[(]\s*[\"'](ANTHROPIC|AWS_|GITHUB_|OPSLOOP_)"
)
_AUTH_ADDED = re.compile(
    r"verify_signature[\"']?\s*:\s*False|verify\s*=\s*False|algorithms\s*=\s*\[[^\]]*[\"']none[\"']"
    r"|options\s*=\s*\{[^}]*verify_(signature|exp)[^}]*False"
)
_AUTH_REMOVED = re.compile(
    r"jwt\.decode|HTTPException\(\s*401|Depends\(principal\)|tenant_id\s*=\s*:t\b|_own_project\("
)


@dataclass(frozen=True)
class Violation:
    rule: str
    file: str
    line: int | None
    detail: str


@dataclass(frozen=True)
class PolicyResult:
    ok: bool
    files: tuple[str, ...]
    violations: tuple[Violation, ...]
    added: int
    removed: int


def _match(path: str, patterns: tuple[str, ...]) -> bool:
    """Glob match where `**` spans directories and `*` does not cross `/`."""
    for pat in patterns:
        rx = (
            re.escape(pat)
            .replace(r"\*\*/", "(?:.*/)?")
            .replace(r"\*\*", ".*")
            .replace(r"\*", "[^/]*")
        )
        if re.fullmatch(rx, path):
            return True
    return False


def _bad_path(path: str) -> str | None:
    if path.startswith("/") or ".." in path.split("/") or "\\" in path:
        return "path escapes the workspace"
    if posixpath.normpath(path) != path:
        return "path is not normalised"
    if ".git/" in f"{path}/" or path.startswith(".git"):
        return "touches git metadata"
    return None


def path_violation(path: str, profile: SpecialistProfile) -> str | None:
    """Why a single path may not be written by this specialist (None = allowed)."""
    reason = _bad_path(path)
    if reason:
        return reason
    if _TEST_PATH.search(path):
        return "tests may not be edited"
    if _match(path, profile.forbidden_paths):
        return f"{path} is forbidden for {profile.name}"
    if not _match(path, profile.allowed_write_paths):
        scope = ", ".join(profile.allowed_write_paths)
        return f"{path} is outside {profile.name}'s write scope ({scope})"
    return None


def check_patch(patch: str, profile: SpecialistProfile) -> PolicyResult:
    violations: list[Violation] = []
    files: list[str] = []
    added_by_file: dict[str, list[str]] = {}
    removed_by_file: dict[str, list[tuple[int, str]]] = {}
    cur_a = cur_b = ""
    new_line = 0
    added = removed = 0

    def v(rule: str, file: str, line: int | None, detail: str) -> None:
        violations.append(Violation(rule, file, line, detail))

    if not patch.strip():
        return PolicyResult(
            False, (), (Violation("empty_patch", "", None, "patch is empty"),), 0, 0
        )

    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/(\S+)$", raw)
            if not m:
                v("malformed_diff", "", None, raw[:120])
                continue
            cur_a, cur_b = m.group(1), m.group(2)
            for p in {cur_a, cur_b}:
                if p not in files:
                    files.append(p)
                reason = _bad_path(p)
                if reason:
                    v("bad_path", p, None, reason)
                if _TEST_PATH.search(p):
                    v("test_edit", p, None, "tests may not be edited")
                if _match(p, profile.forbidden_paths):
                    v("forbidden_path", p, None, f"{p} is forbidden for {profile.name}")
                elif not _match(p, profile.allowed_write_paths):
                    v("path_not_allowed", p, None, f"{p} is outside {profile.name}'s write scope")
            if cur_a != cur_b:
                v("rename", cur_b, None, f"rename {cur_a} -> {cur_b} is not allowed")
            continue
        if raw.startswith(("new file mode 120000", "old mode 120000", "new mode 120000")):
            v("symlink", cur_b, None, "symlinks are not allowed")
        elif raw.startswith(("old mode", "new mode")):
            v("mode_change", cur_b, None, "file mode changes are not allowed")
        elif raw.startswith(("GIT binary patch", "Binary files")):
            v("binary", cur_b, None, "binary changes are not allowed")
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", raw)
            new_line = int(m.group(1)) if m else 0
        elif raw.startswith("+++") or raw.startswith("---"):
            continue
        elif raw.startswith("+"):
            text = raw[1:]
            added += 1
            added_by_file.setdefault(cur_b, []).append(text)
            for rule, rx, detail in (
                ("network_call", _NETWORK, "adds a network call or import"),
                ("code_execution", _EXEC, "adds process or dynamic code execution"),
                ("secret", _SECRET, "adds what looks like a secret or credential access"),
                ("auth_weakening", _AUTH_ADDED, "weakens token verification"),
            ):
                if rx.search(text):
                    v(rule, cur_b, new_line, f"{detail}: {text.strip()[:100]}")
            new_line += 1
        elif raw.startswith("-"):
            removed += 1
            removed_by_file.setdefault(cur_b or cur_a, []).append((new_line, raw[1:]))
        elif raw.startswith(" "):
            new_line += 1

    for path, rows in removed_by_file.items():
        kept = {t.strip() for t in added_by_file.get(path, [])}
        for line_no, text in rows:
            if _AUTH_REMOVED.search(text) and text.strip() not in kept:
                v(
                    "auth_weakening",
                    path,
                    line_no,
                    f"removes an authentication or tenant-isolation check: {text.strip()[:100]}",
                )

    if len(files) > profile.max_files_changed:
        v(
            "too_many_files",
            "",
            None,
            f"{len(files)} files changed, limit {profile.max_files_changed}",
        )
    return PolicyResult(not violations, tuple(files), tuple(violations), added, removed)

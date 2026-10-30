import pytest

from opsloop.deploy import candidate
from opsloop.testloop.patchutil import make_diff


def test_materialise_applies_the_patch_to_a_fresh_copy(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(candidate, "CANDIDATE_ROOT", tmp_path)
    patch = make_diff("taskflow/api/main.py", None, None) or ""
    src = (candidate.REPO_ROOT / "taskflow/api/main.py").read_text(encoding="utf-8")
    patch = make_diff("taskflow/api/main.py", src, src.replace("def health(", "def health_v2(", 1))
    tree = candidate.materialise(patch, "a" * 64)
    assert "def health_v2(" in (tree / "taskflow/api/main.py").read_text(encoding="utf-8")
    assert not (tree / "candidate.patch").exists() and not (tree / ".git").exists()
    # the committed repo is untouched
    assert "def health_v2(" not in src


def test_a_patch_that_does_not_apply_is_reported(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(candidate, "CANDIDATE_ROOT", tmp_path)
    bad = make_diff("taskflow/api/main.py", "nope\n", "still nope\n")
    with pytest.raises(candidate.CandidateError, match="does not apply"):
        candidate.materialise(bad, "b" * 64)


def test_stop_is_safe_when_nothing_runs(monkeypatch):  # type: ignore[no-untyped-def]
    calls = []
    monkeypatch.setattr(candidate, "_docker", lambda *a, **k: calls.append(a))
    candidate.DockerCandidateBackend().stop_candidate()
    assert calls == [("rm", "-f", candidate.CONTAINER)]

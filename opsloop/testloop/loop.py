"""Test Loop (plain code, no AI): validate one proposed patch in a fresh sandbox.

Steps (spec section 11): reproduce -> policy check + apply -> unit / integration / regression
tests -> security (bandit, new findings only) -> performance replay -> hidden checks ->
acceptance -> Report 2. The patch is always re-applied to a clean export of the deployed code,
whatever workspace the coding agent used, and hidden tests never enter the agent's sandbox.
"""

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from opsloop.agents.profiles import SpecialistProfile
from opsloop.replay.scenario import Scenario
from opsloop.sandbox.runner import REPO_ROOT, Sandbox, export_tree, git_head
from opsloop.testloop.acceptance import evaluate, gap_closed
from opsloop.testloop.diffpolicy import check_patch

log = logging.getLogger("opsloop.testloop")

PROBE = REPO_ROOT / "opsloop" / "sandbox" / "probe.py"
HIDDEN_DIR = REPO_ROOT / "sandbox_tests" / "hidden"
_PROBE_RE = re.compile(r"^PROBE_RESULT (\{.*\})$", re.M)


def version_hash(base_commit: str, patch: str) -> str:
    """Identity of one validated change: approval and deployment are bound to this."""
    return hashlib.sha256((base_commit + patch).encode()).hexdigest()


@dataclass
class Step:
    name: str
    status: str  # passed | failed | skipped | error
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Validation:
    passed: bool
    verdict: str  # passed | failed | not_reproduced | rejected_by_policy | patch_does_not_apply
    version_hash: str
    base_commit: str
    steps: list[Step]
    before: dict[str, Any]
    after: dict[str, Any]
    gap_closed: float
    report2: dict[str, Any]


def _probe(sb: Sandbox) -> tuple[dict[str, Any], Step]:
    res = sb.exec("python /opt/probe/probe.py", timeout=420)
    m = _PROBE_RE.search(res.out)
    if not res.ok or not m:
        return {}, Step("probe", "error", res.seconds, {"output": res.out[-1500:]})
    return json.loads(m.group(1)), Step("probe", "passed", res.seconds)


def _pytest(sb: Sandbox, name: str, target: str, cwd: str = "/work") -> Step:
    res = sb.exec(
        f"python -m pytest {target} -q -p no:cacheprovider -x --tb=short",
        timeout=420,
        workdir=cwd,
    )
    last = [ln for ln in res.out.strip().splitlines() if ln.strip()][-1:] or [""]
    return Step(
        name,
        "passed" if res.ok else "failed",
        res.seconds,
        {"summary": last[0], "output": "" if res.ok else res.out[-3000:]},
    )


def _bandit(sb: Sandbox) -> dict[str, set[tuple[str, str, str]]]:
    res = sb.exec("bandit -q -f json -r taskflow/api taskflow/config.py || true", timeout=120)
    try:
        data = json.loads(res.out[res.out.index("{") :])
    except ValueError:
        return {"medium_up": set()}
    issues = {
        (r["test_id"], r["filename"], r["issue_text"])
        for r in data.get("results", [])
        if r["issue_severity"] in ("MEDIUM", "HIGH")
    }
    return {"medium_up": issues}


def validate(
    patch: str,
    scenario: Scenario,
    profile: SpecialistProfile,
    *,
    strategy: str = "code_fix",
    base_ref: str | None = None,
    hidden: bool = True,
) -> Validation:
    """Validate `patch` against `scenario`. base_ref=None uses the working tree (tests/dev);
    production passes the deployed commit."""
    base_commit = base_ref or (git_head() + "+worktree")
    vh = version_hash(base_commit, patch)
    steps: list[Step] = []

    def finish(
        verdict: str, before: dict[str, Any], after: dict[str, Any], gc: float = 0.0
    ) -> Validation:
        passed = verdict == "passed"
        return Validation(
            passed,
            verdict,
            vh,
            base_commit,
            steps,
            before,
            after,
            gc,
            _report2(patch, scenario, strategy, vh, base_commit, steps, before, after, verdict),
        )

    # 1. Policy first: nothing unsafe ever reaches a sandbox, let alone a human.
    policy = check_patch(patch, profile)
    steps.append(
        Step(
            "diff_policy",
            "passed" if policy.ok else "failed",
            0.0,
            {
                "files": list(policy.files),
                "added": policy.added,
                "removed": policy.removed,
                "violations": [asdict(v) for v in policy.violations],
            },
        )
    )
    if not policy.ok:
        return finish("rejected_by_policy", {}, {})

    tree = export_tree(base_ref)
    with Sandbox(flags=scenario.flags) as sb:
        sb.load_tree(tree)
        sb.put({"probe.py": PROBE.read_bytes()}, "/opt/probe")

        # 2. Reproduce the incident on the unpatched code.
        before, step = _probe(sb)
        steps.append(Step("reproduce", step.status, step.seconds, {"metrics": before}))
        if step.status != "passed":
            return finish("not_reproduced", before, {})
        base_results = evaluate(scenario.acceptance, before)
        if all(r.passed for r in base_results):
            steps[-1].status = "failed"
            steps[-1].detail["reason"] = "unpatched code already meets the acceptance criteria"
            return finish("not_reproduced", before, {})
        steps[-1].detail["violated"] = [r.label for r in base_results if not r.passed]
        bandit_before = _bandit(sb)

        # 3. Apply the patch.
        applied = sb.apply_patch(patch)
        steps.append(
            Step(
                "apply_patch",
                "passed" if applied.ok else "failed",
                applied.seconds,
                {"output": applied.out[-1500:]},
            )
        )
        if not applied.ok:
            return finish("patch_does_not_apply", before, {})

        # 4. Tests: unit, integration, regression.
        for name in ("unit", "integration", "regression"):
            steps.append(_pytest(sb, name, f"sandbox_tests/visible/{name}"))
            if steps[-1].status != "passed":
                return finish("failed", before, {})

        # 5. Security: new medium/high static-analysis findings only.
        bandit_after = _bandit(sb)
        new = sorted(bandit_after["medium_up"] - bandit_before["medium_up"])
        steps.append(
            Step(
                "security_scan",
                "passed" if not new else "failed",
                0.0,
                {"new_findings": [list(t) for t in new]},
            )
        )
        if new:
            return finish("failed", before, {})

        # 6. Performance under replay, with the fault still armed.
        after, step = _probe(sb)
        results = evaluate(scenario.acceptance, after)
        gc = gap_closed(scenario.acceptance, before, after) if after else 0.0
        ok = step.status == "passed" and all(r.passed for r in results)
        steps.append(
            Step(
                "performance",
                "passed" if ok else "failed",
                step.seconds,
                {
                    "before": before,
                    "after": after,
                    "gap_closed": round(gc, 3),
                    "criteria": [asdict(r) for r in results],
                },
            )
        )

        # 7. Hidden checks: tests the agent has never seen, copied in only now.
        if hidden:
            files = {p.name: p.read_bytes() for p in HIDDEN_DIR.glob("*.py")}
            sb.put(files, "/hidden")
            steps.append(_pytest(sb, "hidden_checks", "/hidden"))
        if not ok or any(s.status != "passed" for s in steps if s.name == "hidden_checks"):
            return finish("failed", before, after, gc)
        return finish("passed", before, after, gc)


def _report2(
    patch: str,
    scenario: Scenario,
    strategy: str,
    vh: str,
    base_commit: str,
    steps: list[Step],
    before: dict[str, Any],
    after: dict[str, Any],
    verdict: str,
) -> dict[str, Any]:
    policy = next((s for s in steps if s.name == "diff_policy"), None)
    perf = next((s for s in steps if s.name == "performance"), None)
    # Changed lines only (context is not the change), and index helpers as well as raw DDL.
    changed = "\n".join(
        ln[1:] for ln in patch.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    )
    schema_change = bool(
        re.search(
            r"CREATE\s+(UNIQUE\s+)?INDEX|ALTER\s+TABLE|DROP\s+|\bensure_\w*index\b|\bIndex\(",
            changed,
            re.I,
        )
    )
    risks: list[str] = []
    if schema_change:
        risks.append("Changes the database schema: needs explicit human approval.")
    if verdict == "passed":
        risks.append("Validated only on simulated data in a sandbox; production load may differ.")
    return {
        "verdict": verdict,
        "strategy": strategy,
        "scenario": scenario.id,
        "diff": patch,
        "tests": [
            {
                "step": s.name,
                "status": s.status,
                "seconds": round(s.seconds, 1),
                "summary": s.detail.get("summary", ""),
            }
            for s in steps
        ],
        "performance": None
        if perf is None
        else {
            "before": before,
            "after": after,
            "criteria": perf.detail.get("criteria", []),
            "gap_closed": perf.detail.get("gap_closed"),
        },
        "security_findings": ([] if policy is None else policy.detail.get("violations", []))
        + [
            {"rule": "bandit", "detail": f}
            for s in steps
            if s.name == "security_scan"
            for f in s.detail.get("new_findings", [])
        ],
        "remaining_risks": risks,
        "rollback_plan": (
            f"Redeploy the previous version (commit {base_commit}); the change is one patch "
            f"(version {vh[:12]}) and can be reverted with git revert."
        ),
        "version_hash": vh,
        "base_commit": base_commit,
        "simulated": True,
    }


def hidden_test_names() -> list[Path]:
    return sorted(HIDDEN_DIR.glob("*.py"))

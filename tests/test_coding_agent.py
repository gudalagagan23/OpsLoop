from types import SimpleNamespace
from typing import Any

from opsloop.agents.coding_agent import ApiToolLoopRunner, Brief
from opsloop.agents.profiles import load_profile
from opsloop.core.llm import LLMError, LLMUsage, ScriptedLLM, ToolCall


class FakeWorkspace:
    def __init__(self) -> None:
        self.files = {
            "taskflow/api/main.py": "a = 1\nb = 2\n",
            "sandbox_tests/visible/t.py": "x\n",
        }
        self.writes: list[str] = []

    def read_file(self, path: str, limit: int = 0) -> str:
        return self.files[path]

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content
        self.writes.append(path)

    def list_dir(self, path: str = ".") -> str:
        return "\n".join(sorted(self.files))

    def diff(self) -> str:
        return f"diff of {sorted(self.writes)}"

    def exec(self, cmd: Any, *, timeout: float = 0) -> Any:
        return SimpleNamespace(ok=True, rc=0, out="8 passed")


BRIEF = Brief(7, "t", "slow", "n+1", "api", "ev", ["queries <= 5"])
API = load_profile("api_specialist")


def _run(llm: ScriptedLLM, ws: FakeWorkspace, **kw: Any) -> Any:
    return ApiToolLoopRunner(llm, **kw).run(BRIEF, ws, API)


def test_edit_then_finish() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    llm.queue_turn("reading", [ToolCall("1", "read_file", {"path": "taskflow/api/main.py"})])
    edit = {"path": "taskflow/api/main.py", "old": "b = 2", "new": "b = 3"}
    llm.queue_turn(
        "editing",
        [ToolCall("2", "replace_in_file", edit), ToolCall("3", "run_visible_tests", {})],
    )
    llm.queue_turn("Fixed it.")
    res = _run(llm, ws)
    assert res.stopped == "finished" and res.notes == "Fixed it."
    assert ws.files["taskflow/api/main.py"] == "a = 1\nb = 3\n"
    assert res.tool_calls == 3 and res.patch == "diff of ['taskflow/api/main.py']"


def test_writes_outside_scope_and_to_tests_are_refused() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    llm.queue_turn(
        "",
        [
            ToolCall("1", "write_file", {"path": "sandbox_tests/visible/t.py", "content": "pass"}),
            ToolCall("2", "write_file", {"path": "taskflow/faults/registry.py", "content": "x"}),
            ToolCall("3", "write_file", {"path": "../../etc/passwd", "content": "x"}),
            ToolCall("4", "write_file", {"path": "/abs/path.py", "content": "x"}),
        ],
    )
    llm.queue_turn("done")
    res = _run(llm, ws)
    assert ws.writes == [], "no refused write may reach the workspace"
    assert res.tool_calls == 4


def test_replace_needs_a_unique_match() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    ws.files["taskflow/api/main.py"] = "x = 1\nx = 1\n"
    args = {"path": "taskflow/api/main.py", "old": "x = 1", "new": "x = 2"}
    llm.queue_turn("", [ToolCall("1", "replace_in_file", args)])
    llm.queue_turn("done")
    _run(llm, ws)
    assert ws.writes == []


def test_turn_limit_stops_a_looping_agent() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    for i in range(10):
        llm.queue_turn("", [ToolCall(str(i), "list_files", {})])
    res = _run(llm, ws, max_turns=3)
    assert res.stopped == "max_turns" and res.turns == 3


def test_cost_budget_stops_the_agent() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    llm.queue_turn("", [ToolCall("1", "list_files", {})])
    llm.turns[0].usage = LLMUsage(agent="a", model="m", cost_usd=9.0)
    llm.queue_turn("never reached")
    res = ApiToolLoopRunner(llm).run(BRIEF, ws, API, cost_budget_usd=1.0)
    assert res.stopped == "budget" and res.cost_usd == 9.0


def test_llm_failure_is_reported_not_raised() -> None:
    class Boom(ScriptedLLM):
        def agent_turn(self, **kw: Any) -> Any:
            raise LLMError("api down")

    res = ApiToolLoopRunner(Boom()).run(BRIEF, FakeWorkspace(), API)
    assert res.stopped == "error" and res.errors == ["api down"]


def test_tool_results_come_back_in_a_single_user_message() -> None:
    llm, ws = ScriptedLLM(), FakeWorkspace()
    seen: list[list[dict[str, Any]]] = []
    orig = llm.agent_turn

    def spy(**kw: Any) -> Any:
        seen.append(list(kw["messages"]))
        return orig(**kw)

    llm.agent_turn = spy  # type: ignore[method-assign]
    llm.queue_turn(
        "",
        [
            ToolCall("1", "list_files", {}),
            ToolCall("2", "read_file", {"path": "taskflow/api/main.py"}),
        ],
    )
    llm.queue_turn("done")
    _run(llm, ws)
    last = seen[-1][-1]
    assert last["role"] == "user"
    assert [r["tool_use_id"] for r in last["content"]] == ["1", "2"]


def test_brief_does_not_leak_hidden_test_contents() -> None:
    text = BRIEF.render()
    assert "tests_hidden" not in text and "sandbox_tests/hidden" not in text

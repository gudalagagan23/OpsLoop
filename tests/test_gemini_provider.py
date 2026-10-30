import json
from types import SimpleNamespace
from typing import Any

import pytest

from opsloop.core.config import Settings
from opsloop.core.llm import LLMError, LLMRefusal, LLMTruncated
from opsloop.core.llm_gemini import RAW, GeminiLLM, to_contents, to_function_declarations


def _part(
    text: str | None = None, call: tuple[str | None, str, dict[str, Any]] | None = None
) -> Any:
    fc = SimpleNamespace(id=call[0], name=call[1], args=call[2]) if call else None
    return SimpleNamespace(text=text, function_call=fc, thought=False)


def _resp(parts: list[Any], finish: str = "STOP") -> Any:
    return SimpleNamespace(
        response_id="r1",
        text="".join(p.text or "" for p in parts if not p.function_call),
        candidates=[
            SimpleNamespace(
                finish_reason=SimpleNamespace(name=finish), content=SimpleNamespace(parts=parts)
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=15, thoughts_token_count=5
        ),
    )


class FakeModels:
    def __init__(self, reply: Any) -> None:
        self.reply, self.kwargs = reply, {}

    def generate_content(self, **kw: Any) -> Any:
        self.kwargs = kw
        return self.reply


def _llm(reply: Any) -> tuple[GeminiLLM, FakeModels]:
    models = FakeModels(reply)
    settings = Settings(
        _env_file=None,
        llm_model="test-model",
        llm_input_usd_per_mtok=1.0,
        llm_output_usd_per_mtok=2.0,
    )
    return GeminiLLM(settings, client=SimpleNamespace(models=models)), models


def test_tools_become_function_declarations() -> None:
    tools = to_function_declarations(
        [{"name": "read_file", "description": "d", "input_schema": {"type": "object"}}]
    )
    decl = tools[0].function_declarations[0]
    assert decl.name == "read_file" and decl.parameters_json_schema == {"type": "object"}
    assert to_function_declarations([]) == []


def test_history_round_trip_matches_results_to_tool_names() -> None:
    history = [
        {"role": "user", "content": "fix it"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x = 1"},
                {"type": "tool_result", "tool_use_id": "t1", "content": "oops", "is_error": True},
            ],
        },
    ]
    contents = to_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user"]
    call = contents[1].parts[1].function_call
    assert (call.id, call.name, call.args) == ("t1", "read_file", {"path": "a.py"})
    first, second = (p.function_response for p in contents[2].parts)
    assert first.name == "read_file" and first.response == {"result": "x = 1"}
    assert second.response == {"result": "ERROR: oops"}


def test_raw_part_with_a_thought_signature_is_sent_back_unchanged() -> None:
    from google.genai import types

    raw = types.Part(
        function_call=types.FunctionCall(id="t1", name="n", args={}), thought_signature=b"sig"
    )
    contents = to_contents(
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}, RAW: raw}],
            }
        ]
    )
    assert contents[0].parts[0] is raw
    assert contents[0].parts[0].thought_signature == b"sig"


def test_agent_turn_maps_calls_and_keeps_the_raw_part() -> None:
    part = _part(call=("c1", "read_file", {"path": "a.py"}))
    llm, fake = _llm(_resp([_part("thinking out loud"), part]))
    turn = llm.agent_turn(
        agent="coding_agent",
        system="s",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "read_file", "description": "", "input_schema": {}}],
    )
    assert turn.stop_reason == "tool_use" and turn.text == "thinking out loud"
    assert turn.tool_calls[0].input == {"path": "a.py"}
    assert turn.assistant_content[1][RAW] is part
    assert fake.kwargs["config"].automatic_function_calling.disable is True


def test_missing_call_id_gets_a_generated_one() -> None:
    llm, _ = _llm(_resp([_part(call=(None, "read_file", {}))]))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert turn.tool_calls[0].id.startswith("call_")


def test_usage_counts_thinking_tokens_as_output() -> None:
    llm, _ = _llm(_resp([_part("hi")]))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (100, 20)
    assert turn.usage.cost_usd == pytest.approx(100e-6 + 20 * 2e-6)


def test_structured_reply_is_validated() -> None:
    schema = {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    llm, fake = _llm(_resp([_part(json.dumps({"summary": "pool"}))]))
    body, _ = llm.structured(agent="investigator", system="s", prompt="p", schema=schema)
    assert body == {"summary": "pool"}
    assert fake.kwargs["config"].response_mime_type == "application/json"
    bad, _ = _llm(_resp([_part("{}")]))
    with pytest.raises(LLMError, match="missing required field"):
        bad.structured(agent="a", system="s", prompt="p", schema=schema)


def test_truncated_and_blocked_replies_are_reported() -> None:
    cut, _ = _llm(_resp([_part('{"su')], finish="MAX_TOKENS"))
    with pytest.raises(LLMTruncated):
        cut.structured(agent="a", system="s", prompt="p", schema={"type": "object", "required": []})
    blocked, _ = _llm(_resp([_part("x")], finish="SAFETY"))
    with pytest.raises(LLMRefusal):
        blocked.agent_turn(
            agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
        )
    empty, _ = _llm(
        SimpleNamespace(
            candidates=[],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=0, thoughts_token_count=0
            ),
            response_id="x",
            text="",
        )
    )
    with pytest.raises(LLMRefusal):
        empty.agent_turn(
            agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
        )


class _Flaky:
    def __init__(self, failures: int, code: int, reply: Any) -> None:
        self.failures, self.code, self.reply, self.calls = failures, code, reply, 0

    def generate_content(self, **_kw: Any) -> Any:
        from google.genai import errors

        self.calls += 1
        if self.calls <= self.failures:
            raise errors.APIError(
                self.code, {"error": {"message": "busy", "status": "UNAVAILABLE"}}
            )
        return self.reply


def test_overload_is_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("opsloop.core.llm_gemini.time.sleep", lambda _s: None)
    flaky = _Flaky(1, 503, _resp([_part("ok")]))
    llm = GeminiLLM(Settings(_env_file=None, llm_model="m"), client=SimpleNamespace(models=flaky))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert turn.text == "ok" and flaky.calls == 2


def test_persistent_overload_gives_up_with_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("opsloop.core.llm_gemini.time.sleep", lambda _s: None)
    flaky = _Flaky(99, 503, None)
    llm = GeminiLLM(Settings(_env_file=None, llm_model="m"), client=SimpleNamespace(models=flaky))
    with pytest.raises(LLMError, match="503"):
        llm.agent_turn(
            agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
        )
    assert flaky.calls == 2  # one model configured: first try + one retry


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("opsloop.core.llm_gemini.time.sleep", lambda _s: None)
    flaky = _Flaky(99, 400, None)
    llm = GeminiLLM(Settings(_env_file=None, llm_model="m"), client=SimpleNamespace(models=flaky))
    with pytest.raises(LLMError, match="400"):
        llm.agent_turn(
            agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
        )
    assert flaky.calls == 1


class _ByModel:
    def __init__(self, busy: set[str], reply: Any) -> None:
        self.busy, self.reply, self.models_tried = busy, reply, []

    def generate_content(self, **kw: Any) -> Any:
        from google.genai import errors

        self.models_tried.append(kw["model"])
        if kw["model"] in self.busy:
            raise errors.APIError(503, {"error": {"message": "busy", "status": "UNAVAILABLE"}})
        return self.reply


def test_overloaded_primary_falls_back_to_the_next_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("opsloop.core.llm_gemini.time.sleep", lambda _s: None)
    fake = _ByModel({"primary"}, _resp([_part("ok")]))
    settings = Settings(
        _env_file=None, llm_model="primary", llm_fallback_models="backup-a,backup-b"
    )
    llm = GeminiLLM(settings, client=SimpleNamespace(models=fake))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert turn.text == "ok" and fake.models_tried == ["primary", "primary", "backup-a"]
    assert turn.usage.model == "backup-a"

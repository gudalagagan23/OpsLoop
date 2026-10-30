import json
from types import SimpleNamespace
from typing import Any

import pytest

from opsloop.core.config import Settings
from opsloop.core.llm import LLMError, LLMTruncated
from opsloop.core.llm_groq import GroqLLM, check_required, to_openai_messages, to_openai_tools


def _resp(
    content: str | None = None,
    calls: list[tuple[str, str, str]] | None = None,
    finish: str = "stop",
) -> Any:
    tool_calls = [
        SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a))
        for i, n, a in (calls or [])
    ] or None
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        id="req_1",
        choices=[SimpleNamespace(message=msg, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class FakeCompletions:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.kwargs: dict[str, Any] = {}

    def create(self, **kw: Any) -> Any:
        self.kwargs = kw
        return self.reply


def _llm(reply: Any) -> tuple[GroqLLM, FakeCompletions]:
    fake = FakeCompletions(reply)
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    settings = Settings(
        _env_file=None,
        llm_model="test-model",
        llm_input_usd_per_mtok=1.0,
        llm_output_usd_per_mtok=2.0,
    )
    return GroqLLM(settings, client=client), fake  # type: ignore[arg-type]


def test_tools_are_converted_to_openai_function_format() -> None:
    tools = [{"name": "read_file", "description": "d", "input_schema": {"type": "object"}}]
    assert to_openai_tools(tools) == [
        {
            "type": "function",
            "function": {"name": "read_file", "description": "d", "parameters": {"type": "object"}},
        }
    ]


def test_tool_round_trip_keeps_ids_and_results() -> None:
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
                {"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True},
            ],
        },
    ]
    msgs = to_openai_messages("sys", history)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["tool_calls"][0]["id"] == "t1"
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "x = 1"}
    assert msgs[4] == {"role": "tool", "tool_call_id": "t2", "content": "ERROR: boom"}


def test_agent_turn_maps_tool_calls_and_stop_reason() -> None:
    llm, fake = _llm(_resp(calls=[("c1", "read_file", '{"path": "a.py"}')], finish="tool_calls"))
    turn = llm.agent_turn(
        agent="coding_agent",
        system="s",
        messages=[{"role": "user", "content": "go"}],
        tools=[{"name": "read_file", "description": "", "input_schema": {}}],
    )
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].input == {"path": "a.py"}
    assert turn.assistant_content == [
        {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}}
    ]
    assert fake.kwargs["model"] == "test-model" and "tools" in fake.kwargs


def test_invalid_tool_arguments_do_not_crash_the_loop() -> None:
    llm, _ = _llm(_resp(calls=[("c1", "read_file", "{not json")], finish="tool_calls"))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert "_invalid_json" in turn.tool_calls[0].input


def test_usage_is_priced_from_settings() -> None:
    llm, _ = _llm(_resp(content="hi"))
    turn = llm.agent_turn(
        agent="a", system="s", messages=[{"role": "user", "content": "go"}], tools=[]
    )
    assert turn.usage.input_tokens == 100 and turn.usage.output_tokens == 20
    assert turn.usage.cost_usd == pytest.approx(100 * 1e-6 + 20 * 2e-6)


def test_structured_reply_is_checked_against_the_schema() -> None:
    schema = {
        "type": "object",
        "required": ["summary", "confidence"],
        "properties": {"summary": {"type": "string"}, "confidence": {"type": "number"}},
    }
    llm, fake = _llm(_resp(content=json.dumps({"summary": "pool", "confidence": 0.9})))
    body, _ = llm.structured(agent="investigator", system="s", prompt="p", schema=schema)
    assert body["confidence"] == 0.9
    assert fake.kwargs["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in fake.kwargs["messages"][0]["content"]


def test_structured_reply_missing_a_field_is_an_error() -> None:
    schema = {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    llm, _ = _llm(_resp(content="{}"))
    with pytest.raises(LLMError, match="missing required field"):
        llm.structured(agent="a", system="s", prompt="p", schema=schema)


def test_truncated_structured_reply_is_reported() -> None:
    llm, _ = _llm(_resp(content='{"summ', finish="length"))
    with pytest.raises(LLMTruncated):
        llm.structured(agent="a", system="s", prompt="p", schema={"type": "object", "required": []})


def test_check_required_flags_wrong_types() -> None:
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    assert check_required({"n": "3"}, schema) == ["field 'n' should be integer"]
    assert check_required({"n": 3}, schema) == []

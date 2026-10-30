"""Groq provider behind the same LLMClient interface as the Claude client.

The pipeline speaks Anthropic-style messages and tool definitions (the coding agent and the
Investigator are written that way). This module converts both ways, so no other code changes
when the provider is switched. Model name and prices come from the environment.

Structured output: Groq's JSON mode guarantees valid JSON, not the schema, so the schema is
given to the model in the prompt and the required top-level keys are checked here. The
Investigator's own validator checks citations after that.
"""

import json
import time
from typing import Any

import groq
from groq import Groq

from opsloop.core.config import Settings, get_settings
from opsloop.core.llm import (
    AgentTurn,
    LLMError,
    LLMNotConfigured,
    LLMTruncated,
    LLMUsage,
    Recorder,
    ToolCall,
)

_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "function_call": "tool_use",
}


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _tool_result_text(block: dict[str, Any]) -> str:
    text = block.get("content", "")
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return f"ERROR: {text}" if block.get("is_error") else text


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "user":
            texts = [b["text"] for b in content if b.get("type") == "text"]
            for b in content:
                if b.get("type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b["tool_use_id"],
                            "content": _tool_result_text(b),
                        }
                    )
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
            continue
        # assistant: text blocks plus tool_use blocks
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [
            {
                "id": b["id"],
                "type": "function",
                "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
            }
            for b in content
            if b.get("type") == "tool_use"
        ]
        msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


def check_required(body: Any, schema: dict[str, Any]) -> list[str]:
    """Top-level check only: every required key present, and the basic JSON type matches."""
    if not isinstance(body, dict):
        return ["reply is not a JSON object"]
    problems = [
        f"missing required field {k!r}" for k in schema.get("required", []) if k not in body
    ]
    types: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, spec in schema.get("properties", {}).items():
        want = spec.get("type")
        if key in body and isinstance(want, str) and not isinstance(body[key], types[want]):
            problems.append(f"field {key!r} should be {want}")
    return problems


class GroqLLM:
    def __init__(
        self,
        settings: Settings | None = None,
        recorder: Recorder | None = None,
        client: Groq | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.llm_model:
            raise LLMNotConfigured("set OPSLOOP_LLM_MODEL in .env (see .env.example)")
        self.client = client or Groq(max_retries=4, timeout=120.0)
        self.recorder = recorder

    def _usage(self, resp: Any, agent: str, started: float, incident_id: int | None) -> LLMUsage:
        u = resp.usage
        s = self.settings
        inp, out = int(u.prompt_tokens or 0), int(u.completion_tokens or 0)
        cost = inp * s.llm_input_usd_per_mtok / 1e6 + out * s.llm_output_usd_per_mtok / 1e6
        usage = LLMUsage(
            agent=agent,
            model=s.llm_model,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            latency_s=time.monotonic() - started,
            request_id=getattr(resp, "id", None),
            incident_id=incident_id,
        )
        if self.recorder is not None:
            self.recorder(usage)
        return usage

    def _create(self, **kw: Any) -> Any:
        try:
            return self.client.chat.completions.create(model=self.settings.llm_model, **kw)
        except groq.APIStatusError as exc:
            raise LLMError(f"Groq API error {exc.status_code}: {exc.message}") from exc
        except groq.APIConnectionError as exc:
            raise LLMError(f"cannot reach Groq: {exc}") from exc

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 4096,
        incident_id: int | None = None,
    ) -> tuple[dict[str, Any], LLMUsage]:
        started = time.monotonic()
        instruction = (
            "\n\nReply with one JSON object that matches this JSON Schema exactly. "
            "Output JSON only.\n" + json.dumps(schema)
        )
        resp = self._create(
            messages=[
                {"role": "system", "content": system + instruction},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=max_tokens,
            temperature=0,
        )
        usage = self._usage(resp, agent, started, incident_id)
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMTruncated("structured reply was cut off")
        try:
            body = json.loads(choice.message.content or "")
        except json.JSONDecodeError as exc:
            raise LLMError(f"reply was not valid JSON: {exc}") from exc
        problems = check_required(body, schema)
        if problems:
            raise LLMError("reply does not match the schema: " + "; ".join(problems))
        return body, usage

    def agent_turn(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
        incident_id: int | None = None,
    ) -> AgentTurn:
        started = time.monotonic()
        resp = self._create(
            messages=to_openai_messages(system, messages),
            tools=to_openai_tools(tools),
            max_completion_tokens=max_tokens,
            temperature=0,
        )
        usage = self._usage(resp, agent, started, incident_id)
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        content: list[dict[str, Any]] = []
        if msg.content:
            content.append({"type": "text", "text": msg.content})
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args}
            )
        stop = _STOP.get(choice.finish_reason or "", "end_turn")
        return AgentTurn(msg.content or "", calls, stop, usage, content)

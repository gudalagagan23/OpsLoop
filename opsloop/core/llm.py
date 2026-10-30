"""LLM provider interface. Everything that calls a model goes through LLMClient so providers
and models can be swapped (model names come from OPSLOOP_LLM_MODEL, never from code).

Two call shapes:
  * structured(): one request whose reply must match a JSON schema (output_config.format).
  * agent_turn(): one turn of a manual tool-use loop; the caller executes tools and loops.
Forced tool_choice is never used (not supported on current Opus/Sonnet models).
"""

import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from opsloop.core.config import Settings, get_settings

log = logging.getLogger("opsloop.llm")


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    """The model declined (stop_reason == "refusal"). Treated as a reason to escalate."""


class LLMTruncated(LLMError):
    """The reply hit max_tokens; structured output can't be trusted."""


class LLMNotConfigured(LLMError):
    pass


@dataclass(frozen=True)
class LLMUsage:
    agent: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    request_id: str | None = None
    incident_id: int | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class AgentTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: LLMUsage
    # Echoed back verbatim as the assistant message of the next request.
    assistant_content: Any = field(default=None, repr=False)


Recorder = Callable[[LLMUsage], None]


class LLMClient(Protocol):
    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> tuple[dict[str, Any], LLMUsage]: ...

    def agent_turn(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> AgentTurn: ...


def cost_of(settings: Settings, usage: Any) -> float:
    """USD for one response, from prices in the environment (0 when prices are not set)."""
    per = 1_000_000
    return (
        (usage.input_tokens or 0) * settings.llm_input_usd_per_mtok / per
        + (usage.output_tokens or 0) * settings.llm_output_usd_per_mtok / per
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        * settings.llm_cache_read_usd_per_mtok
        / per
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        * settings.llm_cache_write_usd_per_mtok
        / per
    )


class AnthropicLLM:
    def __init__(
        self,
        settings: Settings | None = None,
        recorder: Recorder | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.llm_model:
            raise LLMNotConfigured("set OPSLOOP_LLM_MODEL in .env (see .env.example)")
        key = self.settings.anthropic_api_key.get_secret_value()
        self.client = client or anthropic.Anthropic(
            api_key=key or None, max_retries=4, timeout=600.0
        )
        self.recorder = recorder

    # ---- shared ----------------------------------------------------------------------

    def _usage(self, resp: Any, agent: str, started: float, incident_id: int | None) -> LLMUsage:
        u = resp.usage
        usage = LLMUsage(
            agent=agent,
            model=self.settings.llm_model,
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cost_usd=cost_of(self.settings, u),
            latency_s=time.monotonic() - started,
            request_id=getattr(resp, "_request_id", None),
            incident_id=incident_id,
        )
        if self.recorder is not None:
            self.recorder(usage)
        return usage

    def _check_stop(self, resp: Any) -> None:
        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            raise LLMRefusal(f"model refused: {getattr(detail, 'category', None)}")

    def _create(self, **kw: Any) -> Any:
        output_config: dict[str, Any] = {
            "effort": self.settings.llm_effort,
            **kw.pop("output_config", {}),
        }
        try:
            return self.client.messages.create(  # type: ignore[call-overload]
                model=self.settings.llm_model, output_config=output_config, **kw
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"cannot reach the Claude API: {exc}") from exc

    # ---- interface ---------------------------------------------------------------------

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> tuple[dict[str, Any], LLMUsage]:
        started = time.monotonic()
        resp = self._create(
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage = self._usage(resp, agent, started, incident_id)
        self._check_stop(resp)
        if resp.stop_reason == "max_tokens":
            raise LLMTruncated("structured reply was cut off")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text), usage
        except json.JSONDecodeError as exc:
            raise LLMError(f"reply was not valid JSON: {exc}") from exc

    def agent_turn(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> AgentTurn:
        started = time.monotonic()
        resp = self._create(max_tokens=max_tokens, system=system, messages=messages, tools=tools)
        usage = self._usage(resp, agent, started, incident_id)
        self._check_stop(resp)
        calls = [
            ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"
        ]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return AgentTurn(text, calls, resp.stop_reason, usage, resp.content)


class ScriptedLLM:
    """Test double: replays queued replies. Never used outside tests and offline demos."""

    def __init__(self) -> None:
        self.structured_replies: deque[dict[str, Any]] = deque()
        self.turns: deque[AgentTurn] = deque()
        self.calls: list[dict[str, Any]] = []

    def queue_structured(self, reply: dict[str, Any]) -> None:
        self.structured_replies.append(reply)

    def queue_turn(
        self, text: str = "", tool_calls: list[ToolCall] | None = None, stop: str | None = None
    ) -> None:
        calls = tool_calls or []
        usage = LLMUsage(agent="scripted", model="scripted")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
        ]
        self.turns.append(
            AgentTurn(text, calls, stop or ("tool_use" if calls else "end_turn"), usage, content)
        )

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> tuple[dict[str, Any], LLMUsage]:
        self.calls.append(
            {"kind": "structured", "agent": agent, "system": system, "prompt": prompt}
        )
        if not self.structured_replies:
            raise LLMError("ScriptedLLM: no structured reply queued")
        return self.structured_replies.popleft(), LLMUsage(agent=agent, model="scripted")

    def agent_turn(
        self,
        *,
        agent: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 16000,
        incident_id: int | None = None,
    ) -> AgentTurn:
        self.calls.append({"kind": "turn", "agent": agent, "n_messages": len(messages)})
        if not self.turns:
            raise LLMError("ScriptedLLM: no turn queued")
        return self.turns.popleft()

"""Google Gemini provider behind the same LLMClient interface as the Claude and Groq clients.

The pipeline speaks Anthropic-style messages and tools; this module converts both ways so no
other code changes when the provider is switched. Model name and prices come from the
environment (OPSLOOP_LLM_MODEL, OPSLOOP_LLM_*_USD_PER_MTOK); the API key is read from
GEMINI_API_KEY (or GOOGLE_API_KEY) by the SDK and never stored.

Gemini may attach a "thought signature" to a function-call part and expects it back on the
next turn, so the raw part is carried inside our tool_use block and reused when the history is
sent again.
"""

import json
import time
import uuid
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from opsloop.core.config import Settings, get_settings
from opsloop.core.llm import (
    AgentTurn,
    LLMError,
    LLMNotConfigured,
    LLMRefusal,
    LLMTruncated,
    LLMUsage,
    Recorder,
    ToolCall,
)
from opsloop.core.llm_groq import check_required

RAW = "_gemini_part"


def to_function_declarations(tools: list[dict[str, Any]]) -> list[types.Tool]:
    decls = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters_json_schema=t["input_schema"],
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=decls)] if decls else []


def _result_text(block: dict[str, Any]) -> str:
    text = block.get("content", "")
    if not isinstance(text, str):
        text = json.dumps(text, default=str)
    return f"ERROR: {text}" if block.get("is_error") else text


def to_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Anthropic-style messages -> Gemini contents. Tool results are matched to tool names by id."""
    names: dict[str, str] = {}
    out: list[types.Content] = []
    for m in messages:
        role, content = m["role"], m["content"]
        gem_role = "model" if role == "assistant" else "user"
        if isinstance(content, str):
            out.append(types.Content(role=gem_role, parts=[types.Part(text=content)]))
            continue
        parts: list[types.Part] = []
        for b in content:
            kind = b.get("type")
            if kind == "text" and b.get("text"):
                parts.append(types.Part(text=b["text"]))
            elif kind == "tool_use":
                names[b["id"]] = b["name"]
                raw = b.get(RAW)
                parts.append(
                    raw
                    if raw is not None
                    else types.Part(
                        function_call=types.FunctionCall(
                            id=b["id"], name=b["name"], args=b["input"]
                        )
                    )
                )
            elif kind == "tool_result":
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=b["tool_use_id"],
                            name=names.get(b["tool_use_id"], "tool"),
                            response={"result": _result_text(b)},
                        )
                    )
                )
        if parts:
            out.append(types.Content(role=gem_role, parts=parts))
    return out


class GeminiLLM:
    def __init__(
        self,
        settings: Settings | None = None,
        recorder: Recorder | None = None,
        client: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        if not self.settings.llm_model:
            raise LLMNotConfigured("set OPSLOOP_LLM_MODEL in .env (see .env.example)")
        self.client = client or genai.Client()
        self.recorder = recorder

    def _usage(self, resp: Any, agent: str, started: float, incident_id: int | None) -> LLMUsage:
        u = resp.usage_metadata
        s = self.settings
        inp = int(getattr(u, "prompt_token_count", 0) or 0)
        # Thinking tokens are billed as output.
        out = int(getattr(u, "candidates_token_count", 0) or 0) + int(
            getattr(u, "thoughts_token_count", 0) or 0
        )
        cost = inp * s.llm_input_usd_per_mtok / 1e6 + out * s.llm_output_usd_per_mtok / 1e6
        usage = LLMUsage(
            agent=agent,
            model=s.llm_model,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            latency_s=time.monotonic() - started,
            request_id=getattr(resp, "response_id", None),
            incident_id=incident_id,
        )
        if self.recorder is not None:
            self.recorder(usage)
        return usage

    def _generate(self, contents: Any, config: types.GenerateContentConfig) -> Any:
        try:
            return self.client.models.generate_content(
                model=self.settings.llm_model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            raise LLMError(f"Gemini API error {getattr(exc, 'code', '?')}: {exc}") from exc
        except OSError as exc:
            raise LLMError(f"cannot reach Gemini: {exc}") from exc

    @staticmethod
    def _finish(resp: Any) -> str:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            raise LLMRefusal("Gemini returned no candidates (blocked or empty)")
        reason = getattr(cands[0].finish_reason, "name", str(cands[0].finish_reason))
        if reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise LLMRefusal(f"Gemini blocked the reply: {reason}")
        return reason

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 8192,
        incident_id: int | None = None,
    ) -> tuple[dict[str, Any], LLMUsage]:
        started = time.monotonic()
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_tokens,
            temperature=0,
        )
        resp = self._generate(prompt, config)
        usage = self._usage(resp, agent, started, incident_id)
        if self._finish(resp) == "MAX_TOKENS":
            raise LLMTruncated("structured reply was cut off")
        try:
            body = json.loads(resp.text or "")
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
        max_tokens: int = 8192,
        incident_id: int | None = None,
    ) -> AgentTurn:
        started = time.monotonic()
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=to_function_declarations(tools),
            max_output_tokens=max_tokens,
            temperature=0,
            # We run the tool loop ourselves; never let the SDK call tools for us.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self._generate(to_contents(messages), config)
        usage = self._usage(resp, agent, started, incident_id)
        reason = self._finish(resp)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        blocks: list[dict[str, Any]] = []
        for part in resp.candidates[0].content.parts or []:
            if getattr(part, "function_call", None):
                fc = part.function_call
                call_id = fc.id or f"call_{uuid.uuid4().hex[:12]}"
                args = dict(fc.args or {})
                calls.append(ToolCall(call_id, fc.name, args))
                blocks.append(
                    {"type": "tool_use", "id": call_id, "name": fc.name, "input": args, RAW: part}
                )
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text_parts.append(part.text)
                blocks.append({"type": "text", "text": part.text})
        stop = "tool_use" if calls else ("max_tokens" if reason == "MAX_TOKENS" else "end_turn")
        return AgentTurn("".join(text_parts), calls, stop, usage, blocks)

"""Hand-written ReAct agent loop on top of Groq tool calling (no frameworks).

    ┌──────────── loop (max_steps) ────────────┐
    │ LLM sees: goal + tools + history          │
    │   ├─ asks for tool call(s) → we run them, │
    │   │  append results ("observations")      │
    │   └─ gives a final answer → return        │
    └───────────────────────────────────────────┘
    step limit hit → one last call with tools disabled, forcing an answer.

Every LLM call goes through LLMClient.chat, so KeyPool failover works mid-loop:
the conversation lives here, so if a key hits its limit on step 3 only that
step is retried on the next slot and no progress is lost.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.groq_client import LLMClient

log = logging.getLogger("app.agents")

MAX_OBSERVATION_CHARS = 4000
PREVIEW_CHARS = 300

T = TypeVar("T", bound=BaseModel)


class AgentError(Exception):
    """The agent could not produce a usable answer."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments
    fn: Callable[..., Any]

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class AgentResult:
    content: str
    trace: list[dict] = field(default_factory=list)
    steps: int = 0
    models: list[str] = field(default_factory=list)


def _preview(text: str | None, limit: int = PREVIEW_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _thought(msg: Any) -> str | None:
    """What the model said before acting: its visible content, else its reasoning field (gpt-oss)."""
    text = (getattr(msg, "content", None) or getattr(msg, "reasoning", None) or "").strip()
    text = re.sub(r"</?think>", "", text).strip()
    return _preview(text) if text else None


def _tokens(r) -> int | None:
    return getattr(r.usage, "total_tokens", None) if r.usage is not None else None


COMPACT_ARG_CHARS = 400


def _compact_history(messages: list[dict]) -> None:
    """Shrink long tool-call arguments in all but the latest assistant turn (in place).

    The Tailor passes the full draft to its tools on every step; resending every old
    draft would blow through Groq's per-minute token limit. The model only needs the
    latest draft and the tool results.
    """
    assistant_turns = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    for m in assistant_turns[:-1]:
        for tc in m["tool_calls"]:
            fn = tc["function"]
            if len(fn["arguments"]) <= COMPACT_ARG_CHARS:
                continue
            try:
                args = json.loads(fn["arguments"])
                args = {k: (v[:COMPACT_ARG_CHARS] + "…[older draft trimmed]") if isinstance(v, str)
                        and len(v) > COMPACT_ARG_CHARS else v for k, v in args.items()}
                fn["arguments"] = json.dumps(args, ensure_ascii=False)
            except (json.JSONDecodeError, AttributeError):
                fn["arguments"] = "{}"


def _run_tool(tools: dict[str, Tool], name: str, raw_args: str | None) -> tuple[str, dict]:
    """Execute one tool call. Errors become observations so the model can recover."""
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        return json.dumps({"error": f"Invalid arguments: {e}"}), {}

    tool = tools.get(name)
    if tool is None:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {sorted(tools)}"}), args

    try:
        result = tool.fn(**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"}), args
    except Exception:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": f"Tool {name} failed. Try a different query."}), args

    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_OBSERVATION_CHARS:
        text = text[:MAX_OBSERVATION_CHARS] + "…[truncated]"
    return text, args


def run_agent(
    llm: LLMClient,
    *,
    system: str,
    user: str,
    tools: list[Tool],
    max_steps: int = 6,
    temperature: float = 0.2,
    max_tokens: int = 4000,
    validate_final: Callable[[str], bool] | None = None,
) -> AgentResult:
    """`validate_final`: if the final answer fails this check (empty, wrong format), the model
    gets one corrective nudge with tools disabled before we give up."""
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    specs = [t.spec() for t in tools]
    by_name = {t.name: t for t in tools}
    trace: list[dict] = []
    models: list[str] = []

    def finish(content: str, step: int) -> AgentResult:
        if validate_final is None or validate_final(content):
            return AgentResult(content=content, trace=trace, steps=step, models=models)
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": (
            "Your final answer was empty or not in the required format. "
            "Reply now with ONLY the final answer in the exact format from the instructions."
        )})
        _compact_history(messages)
        r = llm.chat(messages, tools=specs, tool_choice="none", temperature=temperature, max_tokens=max_tokens)
        models.append(r.model)
        trace.append({"step": step + 1, "type": "final", "model": r.model, "thought": _thought(r.message),
                      "retry": True, "tokens": _tokens(r)})
        return AgentResult(content=r.message.content or "", trace=trace, steps=step + 1, models=models)

    for step in range(1, max_steps + 1):
        _compact_history(messages)
        r = llm.chat(messages, tools=specs, tool_choice="auto", temperature=temperature, max_tokens=max_tokens)
        models.append(r.model)
        msg = r.message
        calls = msg.tool_calls or []
        thought = _thought(msg)

        if not calls:
            trace.append({"step": step, "type": "final", "model": r.model, "thought": thought, "tokens": _tokens(r)})
            return finish(msg.content or "", step)

        # Echo the assistant's tool request back into history, exactly as the API expects.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                for c in calls
            ],
        })
        for i, c in enumerate(calls):
            observation, args = _run_tool(by_name, c.function.name, c.function.arguments)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": observation})
            trace.append({
                "step": step,
                "type": "tool",
                "tool": c.function.name,
                "args": {k: _preview(v) if isinstance(v, str) else v for k, v in args.items()},
                "result": _preview(observation),
                "model": r.model,
                "thought": thought if i == 0 else None,
                "tokens": _tokens(r) if i == 0 else None,
            })

    # Out of steps: ask for the answer with tools switched off.
    messages.append({
        "role": "user",
        "content": "Step limit reached. Do not call any more tools. Give your final answer now using what you found.",
    })
    _compact_history(messages)
    r = llm.chat(messages, tools=specs, tool_choice="none", temperature=temperature, max_tokens=max_tokens)
    models.append(r.model)
    trace.append({"step": max_steps + 1, "type": "final", "model": r.model, "thought": _thought(r.message),
                  "forced": True, "tokens": _tokens(r)})
    return finish(r.message.content or "", max_steps + 1)


# ---------- structured output ----------

def extract_tagged(text: str | None, tag: str) -> str | None:
    """Content of <tag>...</tag> (tolerates a missing closing tag). Long documents are returned
    this way instead of inside JSON, because models escape long text in JSON badly."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(rf"<{tag}>\s*(.*?)\s*(?:</{tag}>|$)", text, re.S | re.I)
    if not m:
        return None
    return m.group(1).strip() or None


def extract_json(text: str | None) -> Any:
    """Pull a JSON object out of model output (handles ```json fences, <think> blocks, chatter)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("No JSON object found in the model output")


def parse_structured(llm: LLMClient, content: str, schema: type[T]) -> T:
    """Validate model output against a Pydantic schema; on failure, ask the LLM once to repair it."""
    try:
        return schema.model_validate(extract_json(content))
    except (ValueError, ValidationError) as first_error:
        log.info("Structured output invalid for %s, attempting repair: %s", schema.__name__, _preview(str(first_error)))
        error = first_error

    repair = llm.chat(
        [
            {"role": "system", "content": "You repair malformed JSON. Reply with ONLY a valid JSON object."},
            {"role": "user", "content": (
                f"JSON Schema:\n{json.dumps(schema.model_json_schema())}\n\n"
                f"Validation error:\n{_preview(str(error), 1500)}\n\n"
                f"Text to fix:\n{content[:12000]}"
            )},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=4000,
    )
    try:
        return schema.model_validate(extract_json(repair.message.content))
    except (ValueError, ValidationError) as e:
        raise AgentError("The AI returned an unusable answer. Please try again.") from e

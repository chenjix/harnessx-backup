"""Thin OpenAI/vLLM agent loop with a single Bash tool (qwen3_xml)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

BASH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "Bash",
        "description": (
            "Execute a shell command inside the task container and return "
            "stdout+stderr. Prefer working under /home/user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in milliseconds (default 30000).",
                },
            },
            "required": ["command"],
        },
    },
}

SYSTEM_PROMPT = """\
You are a terminal coding agent solving a single Linux task.
Use the Bash tool to inspect the environment, edit files, and run commands.
Work under /home/user unless the instruction says otherwise.
When the task is complete, stop calling tools and briefly confirm what you did.
Do not ask questions — act.
"""


@dataclass
class StepLog:
    step: int
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentResult:
    steps: int
    finished: str  # no_tool_calls | max_steps | error
    messages: list[dict[str, Any]]
    log: list[StepLog]
    error: str | None = None


def _chat(
    api_base: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    api_key: str,
    timeout: float,
    max_tokens: int | None = 4096,
) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": [BASH_TOOL],
        "tool_choice": "auto",
        "temperature": temperature,
    }
    if max_tokens is not None and max_tokens > 0:
        body["max_tokens"] = int(max_tokens)
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key or 'EMPTY'}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"command": str(raw)}


def run_agent(
    *,
    api_base: str,
    model: str,
    instruction: str,
    exec_fn: Callable[[str, float], tuple[int, str]],
    max_steps: int = 80,
    temperature: float = 0.0,
    api_key: str = "EMPTY",
    request_timeout: float = 180.0,
    default_cmd_timeout: float = 60.0,
    system_prompt: str | None = None,
    max_tokens: int | None = 4096,
) -> AgentResult:
    sys_prompt = (system_prompt or SYSTEM_PROMPT).strip() or SYSTEM_PROMPT
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": instruction},
    ]
    log: list[StepLog] = []

    for step in range(1, max_steps + 1):
        try:
            resp = _chat(
                api_base,
                model,
                messages,
                temperature=temperature,
                api_key=api_key,
                timeout=request_timeout,
                max_tokens=max_tokens,
            )
        except Exception as e:
            return AgentResult(
                steps=step - 1,
                finished="error",
                messages=messages,
                log=log,
                error=f"chat failed at step {step}: {e}",
            )

        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content")

        # Normalize assistant message for history.
        asst: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            asst["tool_calls"] = tool_calls
        messages.append(asst)

        entry = StepLog(step=step, role="assistant", content=content, tool_calls=tool_calls)
        if not tool_calls:
            log.append(entry)
            return AgentResult(
                steps=step, finished="no_tool_calls", messages=messages, log=log
            )

        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name") or ""
            args = _parse_args(fn.get("arguments"))
            tc_id = tc.get("id") or f"call_{step}"
            if name.lower() not in ("bash",):
                result = f"Error: unknown tool '{name}'. Only Bash is available."
                rc = 1
            else:
                cmd = str(args.get("command") or "")
                timeout_ms = args.get("timeout")
                try:
                    t_sec = (
                        min(float(timeout_ms) / 1000.0, 300.0)
                        if timeout_ms is not None
                        else default_cmd_timeout
                    )
                except Exception:
                    t_sec = default_cmd_timeout
                if not cmd.strip():
                    rc, result = 1, "Error: empty command"
                else:
                    t0 = time.time()
                    try:
                        rc, result = exec_fn(cmd, t_sec)
                    except Exception as e:
                        rc, result = 1, f"Error executing command: {e}"
                    # annotate duration for debugging
                    result = (result or "")[-12000:]
                    if not result.endswith("\n"):
                        result += "\n"
                    result += f"[rc={rc} elapsed={time.time()-t0:.1f}s]"

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "name": "Bash",
                "content": result,
            }
            messages.append(tool_msg)
            entry.tool_results.append(
                {"tool_call_id": tc_id, "command": args.get("command"), "rc": rc, "output_tail": result[-2000:]}
            )
        log.append(entry)

    return AgentResult(
        steps=max_steps, finished="max_steps", messages=messages, log=log
    )

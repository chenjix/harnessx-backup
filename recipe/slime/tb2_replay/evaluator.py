# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

from harnessx.core.events import EvalResult


class Tb2ReplayEvaluator:
    """Lightweight reward for replay-buffer GRPO.

    Since replay-buffer prompts do not include executable task verifiers,
    we score by behavior:
      - positive reward when the agent actually used Bash at least once and
        produced a non-empty final output;
      - negative reward otherwise.
    """

    def __init__(self, task) -> None:
        self._task = task

    async def evaluate(self, event) -> EvalResult:
        final_output = (event.final_output or "").strip()
        used_bash = False
        for msg in event.final_messages or ():
            if getattr(msg, "role", "") != "assistant":
                continue
            tool_calls = getattr(msg, "tool_calls", ()) or ()
            if any(getattr(tc, "name", "") == "Bash" for tc in tool_calls):
                used_bash = True
                break

        if used_bash and final_output:
            return EvalResult(
                passed=True,
                score=1.0,
                reason="used Bash and produced final output",
                reward=1.0,
            )
        return EvalResult(
            passed=False,
            score=-1.0,
            reason="missing Bash usage or empty final output",
            reward=-1.0,
        )


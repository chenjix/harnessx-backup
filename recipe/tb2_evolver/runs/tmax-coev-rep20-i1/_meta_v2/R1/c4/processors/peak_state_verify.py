# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""PeakStateVerifyProcessor — self-verification with a transient/peak-state item.

Extends the stock TB2 ``CustomSelfVerifyProcessor`` (the one-shot exit-time
verification checklist). The only change is the injected checklist message:
we add a general item that forces the agent to verify correctness properties
that are **transient / observed only during execution** (peak resource use,
rate/quota caps, memory ceilings, live-service backpressure, concurrency
safety) by re-running the scenario and observing the metric WHILE it runs —
not merely inspecting the final resting state.

Motivation (R0, task_000118): the graded property was PEAK log-directory
size during a deployment run. The agent's monitor never actually paused the
workers, so peak size hit ~200MB (threshold 45MB), yet the agent verified
only the post-run resting state (`du -sh` = 4KB after workers exited) and
declared success. A resting-state check is structurally blind to a peak
metric. The added checklist item is task-agnostic verification discipline:
it names the *class* of property and the *method* (observe during execution),
never any task-specific constant, path, or algorithm.

Behaviour is otherwise identical to the parent: fires at most once per task,
on the first no-tool-call exit intent, via a synthetic keepalive tool call
that is denied and answered with an ack, then the checklist is injected as a
single ``user`` message on the following ``on_before_model``. No new firing
paths, no extra turns beyond the parent's one-shot verification turn.
"""

from __future__ import annotations

from benchmarks.terminal_bench_2.harness import CustomSelfVerifyProcessor

# General verification-method checklist. Mirrors the stock message and adds
# item 4 (transient / peak / during-execution properties). No task-specific
# literals: no paths, constants, identifiers, or algorithms from any task.
_PEAK_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Verify transient / during-execution properties by observing them WHILE the scenario runs.** If any requirement is a runtime invariant that is only true *during* execution — a peak or maximum resource usage, a rate/quota/size cap that must never be exceeded, a memory ceiling, concurrency or ordering safety, or a service staying responsive under load — then inspecting the final resting state is NOT a valid check. The end state can look clean even when the invariant was violated mid-run (e.g. a size peaked far above the limit and then dropped, or a process was never actually paused). Re-run the full end-to-end scenario and sample the metric repeatedly *as it runs* (e.g. poll it in a loop against the exact threshold in the task) to confirm the invariant held at its worst moment, not just afterward.

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class PeakStateVerifyProcessor(CustomSelfVerifyProcessor):
    """CustomSelfVerifyProcessor with a transient/peak-state verification item."""

    # Share the parent's singleton group so only one self-verify processor
    # is active in the pipeline (prevents double-injection if both were
    # ever listed together).
    _singleton_group = "tb2_self_verify"

    def __init__(self) -> None:
        super().__init__()

    async def on_after_model(self, event):
        # Reuse the parent's exit-intent detection and one-shot bookkeeping,
        # then swap the pending checklist text for the extended version.
        async for out in super().on_after_model(event):
            if self._pending_message:
                self._pending_message = _PEAK_SELF_VERIFY_MSG
            yield out

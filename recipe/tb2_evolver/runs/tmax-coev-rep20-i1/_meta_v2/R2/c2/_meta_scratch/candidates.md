# Candidates — R2/c2

## Decision: explicit no-op (assigned focus unsupported by trajectories)

The assigned focus `task_000024_a0664029` is **not** a harness
capability gap. Its trajectory shows `status: error`, `elapsed_s: 0.1`,
and a `docker run failed ... container name already in use` RuntimeError
raised in `recipe/tmax_eval/docker_env.py::start_container` — i.e. the
container never launched and the agent run loop never executed a single
step.

A batch-wide scan confirms this is systemic, not task-specific: **23 of
50 tasks** in the batch fail with the identical docker container-name
collision at ~0.1s. No `HarnessConfig` lever (processor / tool /
template / system prompt) can affect this, because every such hook binds
*after* the container is up, and the offending code lives in read-only
`recipe/**`.

Per the brief ("If your focus turns out to be unsupported by the
trajectories, say so ... make the smallest defensible edit rather than
drifting onto another proposal's territory") and SOUL.md hard invariant
#1's explicit no-op path, the correct action is a byte-for-byte config
copy plus a `NEEDS_FROM_HUMAN.md` note. Drifting onto R1's control-lever
loop/stall clusters would poach a sibling proposal's territory and, more
importantly, cannot be validated this round because ~46% of rows are
uninterpretable t=0 infra failures.

No `## Candidate C-NNN` change section is proposed. See
`NEEDS_FROM_HUMAN.md` for the root cause and suggested read-only fix.

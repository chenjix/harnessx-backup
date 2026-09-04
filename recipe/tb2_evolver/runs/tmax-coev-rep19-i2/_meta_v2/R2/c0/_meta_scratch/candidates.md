# Candidates — R2 / c0

## Decision: explicit NO-OP (config byte-for-byte identical to current_config)

Assigned focus `task_000010_644ab1c2` is **unsupported by the trajectories
as a harness-fixable gap**. No candidate is shipped. `config.yaml` is a
byte-for-byte copy of `current_config` (R1/config.yaml). See
`_meta_scratch/NEEDS_FROM_HUMAN.md` for the out-of-scope runner fix request.

### Why no candidate

- **Signal**: `task_000010_644ab1c2/result.json` → `status: error`,
  `elapsed_s: 0.1`, no episode/message log. The task died at
  `docker run` (host-side container startup), before the agent phase began.
- **Verified body evidence**: error is a Docker container-**name Conflict**
  (`/tmax-task000010644ab1c2-1788106887` already in use). Reproduced across
  **all 10** `status: error` tasks this round (000010, 000028, 000118,
  000140, 000505, 000748, 000933, 001032, 001781, 001937), each 0.1s,
  timestamps clustered at `1788106887/…888`. Root cause traced to
  `recipe/tmax_eval/docker_env.py:105` — container name uses
  `int(time.time())` (1-second resolution), which collides across
  concurrent workers / leftover containers.
- **Lens / lever / intent**: lens = *infrastructure-failure* (not
  control/instruction/action/configuration); lever = **none applicable** —
  the failure is outside the evolvable surface. HarnessConfig only governs
  the in-container processor pipeline + system prompt (per tb2-playbook);
  no hook fires before the agent phase starts.
- **Retroactive check (variant: would-any-config-change-have-flipped-it?)**:
  NO. No processor/tool/template edit can intercept a host `docker run`
  Conflict. Even a hypothetical retry processor lives inside the agent loop
  that never launches. Retroactive check = hard-NO for every lever.
- **Why no-op, not a speculative edit**: any config change would carry
  regression risk against the 38 already-passing tasks with zero
  mechanistic path to the 10 error tasks. `expected_global_gain` of any
  invented change = 0 on this cluster; `regression_risk` > 0. Net-negative
  by construction → no-op is the Pareto-correct action.

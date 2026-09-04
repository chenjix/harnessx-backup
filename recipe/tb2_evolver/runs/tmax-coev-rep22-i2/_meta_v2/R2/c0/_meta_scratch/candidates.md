# Candidates — R2/c0 (assigned focus: task_000010_644ab1c2)

## Decision: explicit no-op (focus unsupported by trajectories)

The assigned focus task **`task_000010_644ab1c2`** did **not** fail for any
reason reachable by a `HarnessConfig`. Diagnosis and evidence below; no
`## Candidate C-NNN` section is proposed because there is no defensible
config/processor/tool/template edit that touches this failure mode. Per the
brief ("If your focus turns out to be unsupported by the trajectories, say so
in candidates.md and make the smallest defensible edit"), the smallest
defensible edit is a **byte-for-byte copy of `current_config`** — a change
would only add regression risk with zero possible upside on this failure.

### Verified body evidence

- `task_000010_644ab1c2.result.json`: `status=error`, `reward=0`,
  `elapsed_s=0.1`, and
  `error = "RuntimeError: docker run failed ... container name
  '/tmax-task000010644ab1c2-1788232928' is already in use by container ..."`.
- **No `.messages.json` exists** for this task — the run loop never emitted a
  turn. The agent, processors, tools, and system prompt were never invoked.
- `recipe/tmax_eval/run_eval.py`: `start_container` (line 149) is called and
  must succeed **before** `harness_config` is used (line 171). The failing
  tasks crash at 149, upstream of the entire MetaAgent write surface.
- `recipe/tmax_eval/docker_env.py::start_container`: container name =
  `f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"[:63]` —
  1-second-resolution timestamp + 20-char task_id prefix → name collisions.

### Cluster scope (why this is not a one-off)

13 of 50 tasks in this trajectory set died with the identical
`docker run failed ... already in use` error and `elapsed_s≈0.1`:
`task_000010_644ab1c2, task_000015_89886d8d, task_000028_7fe033ac,
task_000140_01c78b42, task_000264_ab8c7253, task_000396_e56917e2,
task_000505_50b5162d, task_000748_c9807703, task_000933_1f27096a,
task_001032_1adaccb9, task_001090_c61c71f2, task_001781_529727cf,
task_001937_ac874115`. This is a batch-orchestration flake, not a
model/harness capability gap.

### Why no lever fits (lens / lever / intent triage)

- **Configuration** — no YAML knob feeds `docker_env.start_container`; the
  container name/lifecycle is hard-coded in read-only recipe code.
- **Control** — a `MultiHookProcessor` fires inside the run loop; here the
  run loop never starts, so no hook can ever be reached.
- **Action / Instruction** — tools and prompt text are loaded after the
  container exists; irrelevant to a pre-boot docker failure.
- The fix must live in `recipe/tmax_eval/docker_env.py` /
  `run_eval.py`, which are **outside my write scope**. Logged in
  `_meta_scratch/NEEDS_FROM_HUMAN.md` with three concrete remediations
  (uuid-suffixed names, pre-run `docker rm -f`, retry-on-conflict).

### Why not force a speculative config change anyway

Shipping a processor/prompt tweak to "look active" would add regression risk
to the 20 passing tasks with **zero** mechanistic path to helping the 13
infra-error tasks (they never reach a processor). That violates the Pareto
constraint (regression risk > 0, global gain = 0). The correct move is the
explicit no-op plus a precise human hand-off.

### Retroactive check

Would any config edit have flipped `task_000010_644ab1c2` in this round?
**No** — the config was never loaded for it (`elapsed_s=0.1`, no messages).
The only thing that flips it is a change to container-name generation in
read-only recipe code. Confirmed by the absence of `messages.json` and the
crash site at `run_eval.py:149`, upstream of `harness_config` use at :171.

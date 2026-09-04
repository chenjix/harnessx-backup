# Candidates — R2 c4

## Candidate C-118NOOP — explicit no-op (assigned focus is not a harness-evolvable failure)

- **lens**: infrastructure / orchestration (pre-run-loop)
- **lever**: none (no config surface can intercept a `docker run` name conflict)
- **intent**: no-op / preservative (protect R1 accepted cluster, make no risky edit)
- **Tasks affected**: none flipped; `task_000118_3043e92d` stays failing
  (not harness-fixable). Protects the R1 grader-deps cluster from regression.

Assigned focus: **`task_000118_3043e92d` fails.**

### Diagnosis (verified from trajectory)

`task_000118_3043e92d.result.json`:

```json
"reward": 0,
"status": "error",
"elapsed_s": 0.1,
"error": "RuntimeError: docker run failed for task_000118_3043e92d: docker:
  Error response from daemon: Conflict. The container name
  \"/tmax-task0001183043e92d-1788418459\" is already in use by container
  \"895777cc...\". You have to remove (or rename) that container...",
"traceback": ".../recipe/tmax_eval/docker_env.py, line 124, in start_container"
```

Key facts:
- `elapsed_s: 0.1` and there is **no `*.messages.json`** for this task —
  the container never started, so the run loop never began. No agent
  step, no model call, no processor hook ever fired.
- Root cause is in `recipe/tmax_eval/docker_env.py::start_container`
  (line 105): the container name is
  `f"tmax-{task_id.replace('_','')[:20]}-{int(time.time())}"`. Under
  repeated/concurrent runs of the same task (this is a `rep24` sweep),
  two invocations landing in the **same wall-clock second** produce an
  identical container name → Docker `Conflict` → hard `status:error`.

### Why this is NOT harness-evolvable

Per the tb2-playbook: `config.yaml` controls only the **processor
pipeline and system prompt**, *not* the benchmark infrastructure. The
failure happens strictly **before** any HarnessConfig surface is
exercised:
- Not a model capability gap (the model was never invoked).
- Not a processor/pipeline deficiency (no hook can run pre-container).
- Not a tool/template/prompt issue.

The only fix lives in `recipe/tmax_eval/docker_env.py` (make the
container name collision-proof, e.g. add a PID/UUID/monotonic suffix or
pre-`docker rm -f` the stale name). That file is **read-only**
(`recipe/**` is outside the writable scope: `output_dir/` + `memo_path`
only).

### Lens / lever / intent

- **lens**: infrastructure / orchestration (pre-run-loop), not
  agent-trajectory.
- **lever**: none applicable — no `control` / `configuration` /
  `instruction` / `action` change on the config surface can intercept a
  `docker run` name conflict.
- **intent**: n/a — explicit no-op.

### Action taken

- Copied `current_config` (R1 `config.yaml`) **byte-for-byte** to
  `output_dir/config.yaml`; also copied the sibling `system_prompt.txt`
  byte-for-byte (required by `SiblingSystemPromptBuilder`, which reads
  it as a sibling of the YAML). `canonicalize` → `{"ok": true}`.
- Recorded the infra bug in `_meta_scratch/NEEDS_FROM_HUMAN.md`.

No config/processor/tool/template change is warranted or possible for
this focus. Making an unrelated edit to "look busy" would risk
regressing already-passing clusters (violating the Pareto constraint)
while leaving `task_000118` untouched — so the correct move is the
smallest defensible edit: a byte-identical no-op.

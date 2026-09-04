# Candidates — R2 / c2

## Candidate C-201 — verifier-runtime-readiness habit

**Three-axis tag:** lens = *passing-solution-killed-by-environment* /
lever = **instruction** / intent = **corrective**.

### Signal

Assigned focus `task_000028_7fe033ac` fails with `reward=0` even though
the agent's solution is **functionally correct** — the final HTTP endpoint
returns `HTTP/1.1 200 OK` with body `150` through Nginx (message [70]).
The failure is entirely in the **verifier phase**:

```
/tmp/test_final_state.py:6: in <module>
    import requests
E   ModuleNotFoundError: No module named 'requests'
!!!!! Interrupted: 1 error during collection !!!!!
```

The verifier runs `pytest` inside the same container the agent left behind.
Its test module does `import requests` to hit `http://127.0.0.1:8080/`, but
`requests` is **not installed** in the container's Python. Collection aborts,
so a correct solution scores 0.

### Verified body evidence (two-task cluster)

- `task_000028_7fe033ac`: `result.json` `reward=0`, `exit_reason=done`,
  `steps=54`. Agent verified the endpoint works (msg [53] `HTTP/1.1 200 OK`,
  msg [70] `200 OK ... 150`). `final_pytest.output_tail`:
  `ModuleNotFoundError: No module named 'requests'` at test collection.
  Task text: *"An automated verifier will make HTTP requests to
  http://127.0.0.1:8080/"*.
- `task_000958_4bb2b05d`: `reward=0`, `exit_reason=done`, `steps=64`.
  Same crash: `/tmp/test_final_state.py:4: import requests →
  ModuleNotFoundError`. C++ HTTP microservice task; agent again built a
  working service.
- Contrast — passing HTTP tasks whose verifier did NOT need `requests`:
  `task_000206_a943669b` (`2 passed`), `task_001498_df8254c9` (`4 passed`).
  So the discriminator is purely "verifier imports `requests` and it is
  absent", not solution quality.

The container supports offline package install (warm-cache logic in
`benchmarks/terminal_bench_2/dind_environment.py` skips already-present
pip packages), so `pip install requests` inside the agent phase is a
viable, cheap fix — the agents simply never did it because nothing told
them the verifier depends on it.

### Retroactive check (counterfactual-on-the-losing-cluster variant)

Had the agent, upon finishing an externally-verified HTTP service, ensured
`python3 -c "import requests"` succeeds (installing it if missing), the
verifier's `import requests` would resolve and collection would proceed to
the actual assertions — which both agents' working `200 OK` services should
pass. No change to the two currently-passing HTTP tasks (their verifiers
don't import `requests`; the check is a no-op there). No change to non-HTTP
tasks (the habit is gated on "task says an external verifier will make HTTP
requests to a service you run").

### Why instruction, not control/action

- **Not control**: the failure is not a loop, truncation, or budget burn —
  the run exits cleanly at `done`. There is no runaway state for a
  `MultiHookProcessor` to intercept. A processor could inject a reminder,
  but it would have to content-match the task text and still rely on the
  model to act — same dependency as a prompt line, more machinery, more
  regression surface.
- **Not action (new tool)**: TB2 exposes exactly one tool (`Bash`) and the
  benchmark ignores added tools (playbook: "Do not add tools"). Nothing to
  add.
- **Instruction fits**: this is a *habit gap* — the model already knows how
  to `pip install`; it just never connected "verifier makes HTTP requests"
  to "the verifier's Python needs an HTTP client". A generalized
  verifier-readiness paragraph in the system prompt closes exactly that gap
  and transfers to any unseen task with a `requests`-based (or similar)
  external verifier. It embeds **no** task id, path, port, or constant.

### Change

- `system_prompt.txt` (sibling of `config.yaml`, read by
  `SiblingSystemPromptBuilder`): append one generalized paragraph on
  verifier-runtime readiness for externally-verified services.
- `config.yaml`: byte-identical processor pipeline to R1 (no pipeline
  change). The only effective delta is the new sidecar prompt.

### Pareto

- `expected_global_gain`: flips the `requests`-missing verifier cluster
  (`task_000028`, `task_000958`) and generalizes to any future
  externally-HTTP-verified task where the test client is absent.
- `regression_risk`: low. The guidance is gated on the task explicitly
  describing an external HTTP verifier; it adds one `pip install` at the
  end of such tasks. Worst case a redundant install on tasks that already
  have `requests` (fast no-op via warm-cache). No effect on non-service
  tasks.
- `cost_shift`: negligible — at most one extra Bash call (`import requests`
  probe + optional install) on service tasks; zero on the rest.
- `rollback_trigger`: if R3 shows any previously-passing HTTP task
  regressing (e.g. the extra install command hangs or errors and burns the
  budget) alongside flat/down pass-rate, revert the prompt addition.

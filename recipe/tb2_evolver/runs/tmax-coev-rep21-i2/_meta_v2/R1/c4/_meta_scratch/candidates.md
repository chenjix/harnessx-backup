# Candidates — Round 1 (c4)

Assigned focus: `task_000069_41f1682c` fails.

## Diagnosis of task_000069_41f1682c

The task requires an idempotent deploy script and states literally: "It must
redirect **(append)** the standard output of the executable to
`/home/user/deployment.log`." The verifier clears the log, runs the script
twice, and asserts `deployment.log` has **exactly 2 lines**.

The agent wrote `TZ=UTC LC_ALL=C /home/user/edge_router > /home/user/deployment.log`
— a **truncate** (`>`), not an **append** (`>>`). It conflated "idempotent"
(which constrains `routes.txt`/the config dir) with "always overwrite the log."
Its self-verification (step: "The deployment script is idempotent - running it
again produces the same result") *confirmed its own wrong mental model*: it ran
the script twice, saw one line each time, and declared success — instead of
checking the literal instruction ("append") and the stated behaviour (log grows
per run). Result: `deployment.log should have exactly 2 lines after running
twice, but has 1`.

This is a **harness deficiency in the exit-time self-verify mechanism**
(`CustomSelfVerifyProcessor`): its checklist is generic ("does your solution
address every requirement") and never forces the agent to (a) extract each
imperative verbatim, (b) reproduce the exact usage pattern the spec describes,
and (c) diff observed behaviour against the *literal* wording rather than its
own restatement.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace the stock `CustomSelfVerifyProcessor` with a `SpecComplianceVerifyProcessor`
that keeps the identical one-shot exit-intercept mechanism but injects a sharper
spec-compliance checklist: quote each literal imperative, flag append-vs-overwrite
/ exact-count / idempotency / forbidden-residual-state wording, reproduce the
exact usage pattern the spec describes (e.g. run multiple times if the spec
constrains multiple runs), and diff observed behaviour against the quoted wording.

- Tasks affected (same mechanism — self-verify confirmed a self-invented model
  instead of the literal spec / stated behaviour):
  - `task_000069_41f1682c`: used `>` where spec said "append"; ran twice, saw
    1 line, declared "idempotent" ✓ — never checked the stated 2-run behaviour.
  - `task_000140_01c78b42`: spec forbids lingering `vm_service` processes;
    verifier found 5 lingering PIDs. Agent exited `done` (25 steps) without a
    self-check that the forbidden residual state (`pgrep`) was absent.
- Signal: `final_pytest` failures are spec-literal mismatches, not logic errors
  the model couldn't produce — 069 line-count off by exactly the overwrite/append
  difference; 140 leftover processes the spec explicitly forbade. Both exited
  `exit_reason=done` after a generic self-verify pass.
- Verified (Read of `.messages.json`):
  - 069 step "test idempotency by running the script again" → tool call
    `/home/user/deploy_service.sh && cat /home/user/deployment.log` returns a
    single line; assistant text: "The deployment script is idempotent - running
    it again produces the same result without appending duplicate lines." The
    self-verify checklist (`_tb2_self_verify`) then passed on this reasoning.
  - 140 `result.json` `final_pytest.output_tail`: `Lingering vm_service
    processes found: ['352','606','858','1092','1284']`; agent exited `done`.
- Why Control not Instruction: the exit-time checklist is a *mechanical hook*
  that must fire uniformly on every task's exit intent — a system-prompt rule is
  read once at the top and is exactly what the agent already ignored when it
  substituted its own interpretation. The processor guarantees the check is
  re-presented at the decisive moment (first exit intent), which the prompt
  cannot. It also preserves the existing, working keepalive mechanism rather
  than adding a parallel one.
- Why not Configuration: `CustomSelfVerifyProcessor` exposes no knob for the
  checklist text; the fix is new checklist content, which requires a new class.
- Retroactive check (A-corrective): yes — had the checklist forced 069 to quote
  "append" verbatim and reproduce the "run twice → check line count" behaviour,
  the `>`→`>>` bug surfaces before exit; had it forced 140 to `pgrep` for the
  forbidden residual processes, the leftover PIDs surface before exit.
- expected_global_gain: flips spec-literal-mismatch failures across domains
  (sys-admin/file-ops/security tasks with exact-format, append, idempotency, or
  cleanup requirements). This is a recurring shape (>=2 tasks, distinct inputs).
- regression_risk: LOW. The mechanism is byte-identical to the stock processor
  (same `_singleton_group`, `_order=90`, one-shot, synthetic keepalive) — only
  the injected text changes. Passing tasks that already self-verify cleanly get
  a slightly longer checklist but the same single extra turn; no new tool, no
  message-count change (+1 user, same as before, contract-checked).
- cost_shift: +~1 slightly longer injected message per task on the first exit
  intent (fires at most once). Marginal token increase; may *reduce* total cost
  by catching errors before a wasted verifier round on tasks it flips.
- Rollback trigger: if pass_rate drops vs R0 (0.18) or if sys-admin/file-ops
  clusters regress, revert to the stock `CustomSelfVerifyProcessor`.

## Not harness-fixable (logged, not patched)
- `task_000264_ab8c7253`: query plan lacked `USING INDEX` — SQL-optimization
  capability gap; no self-check surfaces it. Skip.
- `task_000111_cbada64a`: numeric result 2.5056 vs expected 2.5997 — model
  numerical/algorithmic capability gap. Skip.

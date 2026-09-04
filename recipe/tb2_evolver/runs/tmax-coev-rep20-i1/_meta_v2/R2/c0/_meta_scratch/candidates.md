# Candidates — R2 / c0

Assigned focus: `task_000010_644ab1c2` fails.

## Diagnosis

`task_000010_644ab1c2` (reward=0, `exit_reason=loop_detected`, steps=23). The
task mandates writing a script at `/home/user/operator.py`. The agent did so,
then ran `python3 /home/user/operator.py` with cwd `/home/user`. Python puts
the script's directory first on `sys.path`, so `/home/user/operator.py`
**shadowed the stdlib `operator` module**. The stdlib
`collections/__init__.py` does `from operator import eq as _eq`, which
resolved to the user's script → `ImportError: cannot import name 'namedtuple'
from partially initialized module 'collections' ... circular import`. This
broke *every* `python3` invocation from that directory (even
`python3 -c "import socket"`).

Body evidence (messages.json):
- step (line 122): first run → `ImportError ... deque ... partially
  initialized module 'collections' ... circular import`, traceback includes
  frame `File "/home/user/operator.py", line 14`.
- steps 175–415: the agent rewrites the script's imports repeatedly, tries
  `python3 -c "import socket"`, `import yaml`, `pip install pyyaml`, etc. —
  ALL fail with the same partial-init error whose traceback keeps showing
  `File "/home/user/operator.py"`. The agent narrates "This is a Python 3.10
  compatibility issue" over and over — a total misdiagnosis.
- Loop guard eventually terminates (`loop_detected`). `final_pytest` still
  fails with the identical circular-import error.

The agent never once considers that its own filename is the culprit. This is
a **harness capability gap**: a weak model cannot self-diagnose stdlib
shadowing, and the existing loop guard only *stops* the thrash — it gives the
model no path to recovery.

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Add a warn-only `on_after_tool` processor (`StdlibShadowGuard`) that detects
the stdlib-shadowing signature in Bash output — a partially-initialized /
circular-import `ImportError` whose traceback contains a frame in a
user-writable `.py` path (not `/usr/lib`) — and appends a targeted diagnostic
naming the root cause and the concrete recovery (run from a neutral cwd like
`cd /tmp && python3 <file>`, or `PYTHONSAFEPATH=1`, or rename if allowed).

- Tasks affected: task_000010_644ab1c2 (assigned). Mechanism is a general,
  well-known Python footgun that recurs on any task that (a) mandates or
  chooses a script filename colliding with a stdlib module and (b) runs
  Python from that directory. The processor is written to the *signature*,
  not to this task — it triggers on any `<name>.py` under any user path.
- Signal: `exit_reason=loop_detected`; result.json `final_pytest.output_tail`
  = `cannot import name 'namedtuple' from partially initialized module
  'collections' ... circular import` with a `/home/user/operator.py` frame;
  messages show ~16 identical-signature retries misattributed to "Python 3.10
  compatibility".
- Verified (Read messages.json): step at line 122 — first shadow error with
  `File "/home/user/operator.py"` frame; steps at lines 182/222/282/322/342 —
  same partial-init signature on varied commands, agent repeatedly blames a
  "Python version issue" and never suspects the filename.
- Why Control not Instruction: the model demonstrably cannot *recognize* this
  signature — a general prompt rule ("watch out for shadowing") would be
  ignored the same way it ignored the loop-detection warnings that fired 10+
  times verbatim. The recovery must be injected *at the moment the signature
  appears*, keyed to the actual failing file path parsed from the traceback —
  a mechanical after-tool hook, not static prose.
- Why Control not Action: nothing about the agent's action space is missing —
  Bash can already `cd /tmp && python3 ...`. The gap is perception of a
  specific error shape, which post-processing the tool result closes.
- Retroactive check (A-corrective): yes — had the diagnostic been in context
  after the first `python3 /home/user/operator.py` failure, the agent would
  have seen "run from /tmp" and `cd /tmp && python3 /home/user/operator.py`
  resolves the shadowing, letting the script complete its (already-written)
  backup + port-forward + apply pipeline. The loop never starts.
- expected_global_gain: closes the self-inflicted-interpreter-breakage class
  (shadowing → cascading import failure). Any task where the agent picks/must
  use a stdlib-colliding script name and runs from that dir is unblocked;
  today those tasks are unrecoverable because the model misdiagnoses 100% of
  the time.
- regression_risk: near-zero. The hook only appends text, never blocks or
  terminates, and only fires when BOTH the partial-init signature AND a
  user-path traceback frame are present — a shape that does not occur in a
  healthy run. Capped at `max_injections=3` per task so it cannot spam
  context. No passing task in the batch shows this signature.
- cost_shift: strongly negative on affected tasks (a 23-step loop collapses to
  a few steps once recovered) and neutral (a few hundred bytes of note, at
  most 3x) on everything else since the guard never fires on healthy runs.

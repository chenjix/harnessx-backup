# Needs from human — R2

## `agent_loop._chat` has no retry (out of evolvable surface)

`recipe/tmax_eval/agent_loop.py::_chat` makes a single `urllib` POST to the
model server with no retry. Any transient failure (`timed out`, `HTTP 400`)
at step N raises, and `run_agent` returns `finished="error"`, discarding all
prior progress. In R1 this hit 9/21 failing tasks:

- `chat failed at step N: timed out` — 8 tasks
  (task_000264, task_000396, task_000505, task_000578, task_000684,
   task_000958, task_001031, task_001089)
- `HTTP Error 400: Bad Request` — 1 task (task_001321)

Several of these show the agent working productively right up to the abort,
so a bounded retry-with-backoff (and, for the 400, a message-payload
sanitization / re-request) would plausibly recover a handful.

**Why not fixed this round:** `agent_loop.py` lives under `recipe/` which is
read-only, and it is NOT wired through the evolvable `config.yaml` /
system-prompt surface (the tmax loop only consumes the system prompt). The
meta-agent cannot patch it. A human change to `agent_loop._chat` (wrap the
`_chat` call in a retry loop, e.g. 3 attempts with exponential backoff on
`urllib.error.URLError` / timeout / 5xx, and skip/repair the last message on
persistent 400) is the right owner for this cluster.

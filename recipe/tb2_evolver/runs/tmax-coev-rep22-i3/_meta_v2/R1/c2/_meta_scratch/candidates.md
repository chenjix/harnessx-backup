# Candidates

## Candidate C-001
[lens: capability-gap | lever: control | intent: corrective]

Widen the `HttpVerifierDepProcessor` service-start detector so it also fires on
non-framework service starts (nohup/setsid daemons, nginx/apache, systemd
units) and self-probe idioms (/dev/tcp, python socket, curl/wget http,
ss/netstat listening checks), not just web-framework keywords.

- Tasks affected: task_000028_7fe033ac, task_001857_24daeef3
- Signal: both `result.json` `final_pytest` fail with rc=2 (collection error)
  and `output_tail` = `ModuleNotFoundError: No module named 'requests'` while
  `agent.exit_reason=done`. The `HttpVerifierReminder` message is **absent**
  from both message logs — the existing detector never fired, so the agent was
  never nudged to make `requests` importable and lost purely at test
  collection.
- Verified (Read of messages.json):
  - task_000028 step 4 Bash: `g++ -o /app/server /app/server.cpp -std=c++11 &&
    rm -f /tmp/video_backend.sock && nohup /app/server >> /app/server.log 2>&1 &`
    — a compiled C++ socket server started via `nohup ... &`; step 8 probes it
    with `exec 3<>/dev/tcp/127.0.0.1/8080 && echo -e "GET / HTTP/1.1..."`. The
    task text says "An automated verifier will make HTTP requests to
    http://127.0.0.1:8080/". The old regex (flask|fastapi|uvicorn|gunicorn|
    http.server|app.run(|.listen(|127.0.0.1:PORT|--port|host=...) matched none
    of these lines, so no reminder fired.
  - task_001857 step 6 Bash: python `socket.socket(...)/socket.connect(...)`
    probe against the local service; step 15 `nohup /home/user/diagnostic_server
    > /tmp/server.log 2>&1 &`. Again a compiled binary daemon + python socket
    probe — zero matches against the old regex; reminder never fired.
  - Confirmed via offline replay of the new regex against both logs: it now
    fires (task_000028 at step 4 on the `nohup ... &` line, task_001857 at
    step 6 on `socket.socket(`). Confirmed passing service tasks that use a
    genuine server still fire correctly and harmlessly (task_000297 python
    `http.server`, task_000321 `nohup python3 supervisor.py &`), and the two
    spurious `\bnode\b`-in-C-source matches (task_000870/000876) were removed
    by dropping the over-broad `node`/`express` keywords.
- Why Control not Instruction: the failure is a mechanical detection gap in an
  existing Control hook — the reminder mechanism is correct, only its trigger
  regex was under-inclusive. A prompt rule ("always install requests") would
  fire on every task (the majority are non-service) and inject task-irrelevant
  noise; the Control hook's per-task server-detection is exactly the
  conditional scoping that keeps non-service tasks untouched.
- Why Control not Configuration: the detector is a compiled regex embedded in
  the processor, not a constructor kwarg — there is no knob to retune; the fix
  requires editing the pattern, i.e. a code change to the same Control
  component.
- Retroactive check (A-corrective): yes — the reminder message drives an
  idempotent `python3 -m pip install requests` before exit; if it had fired on
  either task the `import requests` collection error (the *only* thing between
  a running service and a pass, rc=2 at collection) would have been removed.
  The reminder now provably fires on both replayed logs.
- expected_global_gain: closes the `requests`-collection failure cluster
  (>=2 tasks: 000028, 001857) that the existing hook was authored for but
  under-triggered; generalizes to any future HTTP/socket-service task that
  starts a compiled binary / nginx / systemd unit or self-probes via
  /dev/tcp / python socket / curl — shapes the old keyword list missed.
- regression_risk: low. The hook only *appends* one user message, once, and
  only on tasks matching a service/probe signal. On the 2 already-passing
  service tasks it fires on (000297, 000321) the nudge is idempotent
  (`import requests || pip install requests`) and does not change a correct
  outcome. Over-broad keywords that matched source-code substrings (`node`)
  were removed to avoid false positives on non-service tasks. No tool is
  blocked, no system prompt or existing message is mutated.
- cost_shift: negligible. At most one ~150-token appended message per matched
  task (16/50 tasks in the evolve set), fired once. No extra model turns are
  forced.

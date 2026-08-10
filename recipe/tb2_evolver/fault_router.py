#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Stage-1 fault localization: decide WHO should change, before deciding HOW.

The evolve loop and the SFT builder currently consume the same trajectories with
no routing at all: every failure is handed to the meta-agent, every success is
swept into the SFT corpus. That conflates interventions. The clearest case is a
tool-call failure — outwardly identical in the score, but:

  * the wrapper swallowed the error and the model never saw it   -> fix the HARNESS
  * the error came back in full and the model repeated it anyway -> train the MODEL

This module produces, per trajectory:

  critical_event          earliest failure that was never recovered from
  interaction_edge        Model-Tool | Model-Context | Model-LocalEnv | Model-Grader | None
  fault_side              MODEL | TOOL | HARNESS | ENV | GRADER | AMBIGUOUS
  failure_mode            narrower label within the edge
  evidence_span           the (step, action, observation) that justifies the call
  recovered_after_event   whether the agent got past it
  attribution_confidence  0-1; low values route to quarantine, not to a consumer

Design choices worth stating:

- We localize the EARLIEST unrecovered failure and treat everything after it as
  downstream symptom. Without this, one root cause gets counted dozens of times
  (a single swallowed error produces a long tail of retries, each of which looks
  like an independent training signal).
- Environment and grader faults are their own destination, not quarantine. A
  container that never started or a verifier demanding a file the instruction
  never mentioned is *repairable evidence about the harness/benchmark*, and it
  must be excluded from any denominator that claims to measure model capability.
- Confidence is reported and abstention is a first-class outcome. Attribution
  depends on evidence completeness; when the trace cannot distinguish two
  hypotheses we say so and name the missing evidence rather than guessing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

ERR_RE = re.compile(
    r"\b(error|traceback|exception|failed|failure|not found|no such|permission denied"
    r"|command not found|cannot|unable to|refused|timed? ?out|invalid|denied)\b", re.I
)
# Environment-level breakage: nothing the model or the prompt could have avoided.
ENV_RE = re.compile(
    r"(no space left on device|command not found|unable to locate package"
    r"|could not resolve host|temporary failure in name resolution|network is unreachable"
    r"|docker compose|cannot connect to the docker daemon|apt-get.*(E:|failed)"
    r"|ImportError: lib|GPG error|Connection refused)", re.I
)
# The harness rejected or mangled the call before the environment ever saw it.
WRAPPER_RE = re.compile(
    r"(missing \d+ required positional argument|unexpected keyword argument"
    r"|tool_call.*(malformed|could not be parsed)|bash_tool\(\)"
    r"|no tool call|tool call was truncated|\[empty tool result\])", re.I
)
GRADER_EXC = {"AddTestsDirError", "RewardFileNotFoundError", "VerifierError"}


@dataclass
class Attribution:
    task: str
    trial_dir: str
    observed_outcome: str                     # pass | fail
    critical_event: int | None = None         # step index
    interaction_edge: str | None = None
    fault_side: str = "AMBIGUOUS"
    failure_mode: str = ""
    evidence_span: dict[str, Any] = field(default_factory=dict)
    recovered_after_event: bool = False
    attribution_confidence: float = 0.0
    missing_evidence: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    destination: str = "quarantine"
    n_downstream_symptoms: int = 0
    n_tool_calls: int = 0
    n_steps: int = 0


def _session_events(trial: Path) -> list[dict[str, Any]]:
    """Flatten the HarnessX session jsonl into an ordered event list."""
    oh = trial / "agent" / "oh_runs"
    if not oh.is_dir():
        return []
    best: Path | None = None
    for f in oh.rglob("*.jsonl"):
        if "_trace" in f.name or "_state" in f.name:
            continue
        if best is None or f.stat().st_size > best.stat().st_size:
            best = f
    if best is None:
        return []
    out: list[dict[str, Any]] = []
    for line in best.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _localize(events: list[dict[str, Any]]) -> tuple[int | None, list[dict], int]:
    """Return (index of earliest unrecovered failure, turns, downstream symptom count).

    A failure is 'recovered' if a later tool observation for the same command
    family comes back clean. Everything after the earliest unrecovered failure is
    counted as downstream symptom rather than as separate evidence.
    """
    turns: list[dict] = []
    for ev in events:
        t = ev.get("type")
        if t in ("assistant", "raw_assistant"):
            msg = ev.get("message") or {}
            tcs = msg.get("tool_calls") or []
            turns.append(dict(kind="act", step=ev.get("step"),
                              content=str(msg.get("content") or "")[:600],
                              cmds=[str((tc.get("input") or {}).get("command")
                                        or (tc.get("function") or {}).get("arguments") or "")[:400]
                                    for tc in tcs]))
        elif t == "raw_tool":
            msg = ev.get("message") or {}
            turns.append(dict(kind="obs", step=ev.get("step"),
                              content=str(msg.get("content") or "")[:2000]))

    err_idx = [i for i, x in enumerate(turns)
               if x["kind"] == "obs" and ERR_RE.search(x["content"] or "")]
    if not err_idx:
        return None, turns, 0

    for i in err_idx:
        later_clean = any(
            turns[j]["kind"] == "obs" and not ERR_RE.search(turns[j]["content"] or "")
            for j in range(i + 1, len(turns))
        )
        if not later_clean:
            return i, turns, len([k for k in err_idx if k > i])
    # Every error was followed by a clean observation; the first one is still the
    # causal origin of whatever cascade followed.
    return err_idx[0], turns, len(err_idx) - 1


def _prev_action(turns: list[dict], idx: int) -> dict | None:
    for j in range(idx - 1, -1, -1):
        if turns[j]["kind"] == "act":
            return turns[j]
    return None


def _repeated_after(turns: list[dict], idx: int, cmd: str) -> bool:
    """Did the model re-issue the same command after seeing the error?"""
    if not cmd:
        return False
    key = re.sub(r"\s+", " ", cmd).strip()[:120]
    for j in range(idx + 1, len(turns)):
        if turns[j]["kind"] == "act":
            for c in turns[j]["cmds"]:
                if re.sub(r"\s+", " ", c).strip()[:120] == key:
                    return True
    return False


def attribute(trial: Path, task: str, result: dict[str, Any], max_steps: int) -> Attribution:
    rw = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
    passed = isinstance(rw, (int, float)) and rw > 0
    exc = result.get("exception_info") or {}
    exc_type = exc.get("exception_type") if isinstance(exc, dict) else None
    exc_msg = str(exc.get("exception_message") or "") if isinstance(exc, dict) else ""
    n_in = (result.get("agent_result") or {}).get("n_input_tokens")

    a = Attribution(task=task, trial_dir=str(trial),
                    observed_outcome="pass" if passed else "fail")

    # ── environment never came up: the agent produced nothing at all ─────────
    if not n_in:
        a.interaction_edge = "Model-LocalEnv"
        a.fault_side = "ENV"
        a.failure_mode = "environment_never_started"
        a.attribution_confidence = 0.97
        a.evidence_span = dict(exception_type=exc_type, message=exc_msg[:300])
        a.destination = "environment_repair"
        return a

    # ── grader/spec faults are visible in the exception type ─────────────────
    if exc_type in GRADER_EXC:
        a.interaction_edge = "Model-Grader"
        a.fault_side = "GRADER"
        a.failure_mode = f"grader_{str(exc_type)}"
        a.attribution_confidence = 0.9
        a.evidence_span = dict(exception_type=exc_type, message=exc_msg[:300])
        a.destination = "benchmark_repair"
        return a

    events = _session_events(trial)
    if not events:
        a.missing_evidence = ["session jsonl absent — cannot reconstruct event chain"]
        a.attribution_confidence = 0.0
        a.destination = "quarantine"
        return a

    idx, turns, downstream = _localize(events)
    a.n_steps = sum(1 for t in turns if t["kind"] == "act")
    a.n_tool_calls = sum(len(t["cmds"]) for t in turns if t["kind"] == "act")
    a.n_downstream_symptoms = downstream

    if passed:
        # Successes still carry routable evidence: a recovered failure is a
        # demonstration of the correction, which is exactly what SFT needs.
        a.recovered_after_event = idx is not None
        a.critical_event = idx
        a.fault_side = "MODEL"
        a.failure_mode = "recovered_success" if idx is not None else "clean_success"
        a.interaction_edge = "Model-Tool" if idx is not None else None
        a.attribution_confidence = 0.8
        a.destination = "sft_recovery_slice" if idx is not None else "sft_success"
        if idx is not None:
            obs = turns[idx]["content"]
            act = _prev_action(turns, idx) or {}
            a.evidence_span = dict(step=turns[idx].get("step"),
                                   action=(act.get("cmds") or [""])[0][:300],
                                   observation=obs[:400])
        return a

    if idx is None:
        # Failed with no error observation anywhere: the agent ran cleanly and
        # simply did not achieve the goal, or stopped early.
        if a.n_steps >= max_steps - 5:
            a.interaction_edge = "Model-Context"
            a.fault_side = "AMBIGUOUS"
            a.failure_mode = "step_budget_exhausted_no_errors"
            a.attribution_confidence = 0.45
            a.alternatives = [
                dict(fault_side="MODEL", reason="inefficient exploration", p=0.5),
                dict(fault_side="HARNESS", reason="step budget too small for this task", p=0.5),
            ]
            a.missing_evidence = ["per-step progress signal to tell stalling from slow progress"]
            a.destination = "quarantine"
        else:
            a.fault_side = "MODEL"
            a.failure_mode = "premature_stop_or_wrong_solution"
            a.attribution_confidence = 0.6
            a.destination = "model_training"
        a.n_steps = a.n_steps
        return a

    a.critical_event = idx
    obs = turns[idx]["content"]
    act = _prev_action(turns, idx) or {}
    cmd = (act.get("cmds") or [""])[0] if act else ""
    a.evidence_span = dict(step=turns[idx].get("step"), action=cmd[:300], observation=obs[:500])

    # ── classify the critical event ─────────────────────────────────────────
    if ENV_RE.search(obs):
        a.interaction_edge = "Model-LocalEnv"
        a.fault_side = "ENV"
        a.failure_mode = "missing_dependency_or_broken_env"
        a.attribution_confidence = 0.85
        a.destination = "environment_repair"
        return a

    if WRAPPER_RE.search(obs) or WRAPPER_RE.search(cmd):
        # The harness mangled the call, or handed back a stub instead of the real
        # error. Training the model on this teaches it to work around our bug.
        a.interaction_edge = "Model-Tool"
        a.fault_side = "TOOL"
        a.failure_mode = "tool_wrapper_rejected_or_suppressed"
        a.attribution_confidence = 0.85
        a.destination = "harness_evolution"
        a.evidence_span["editable_surface"] = "tool schema / argument validation / error propagation"
        return a

    if len(obs.strip()) < 12:
        # An error step whose observation is essentially empty: the model could not
        # have learned anything from it. That is a harness reporting defect, not a
        # model mistake — the distinction the paper is built around.
        a.interaction_edge = "Model-Tool"
        a.fault_side = "TOOL"
        a.failure_mode = "error_not_surfaced_to_model"
        a.attribution_confidence = 0.7
        a.destination = "harness_evolution"
        a.evidence_span["editable_surface"] = "error propagation / tool feedback"
        a.alternatives = [dict(fault_side="MODEL", reason="model ignored a terse but real error", p=0.3)]
        return a

    # Error was fully visible. Did the model adapt, or repeat itself?
    if _repeated_after(turns, idx, cmd):
        a.interaction_edge = "Model-Tool"
        a.fault_side = "MODEL"
        a.failure_mode = "ignored_visible_error_repeated_action"
        a.attribution_confidence = 0.8
        a.destination = "model_training"
        return a

    a.interaction_edge = "Model-Tool"
    a.fault_side = "MODEL"
    a.failure_mode = "visible_error_no_effective_recovery"
    a.attribution_confidence = 0.65
    a.alternatives = [dict(fault_side="HARNESS", reason="no recovery affordance offered", p=0.35)]
    a.destination = "model_training"
    return a


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs", nargs="*", default=None,
                    help="job dir names under .benchmarks/tb2 (default: all, excluding __task- shards)")
    ap.add_argument("--tasks", default=None, help="restrict to tasks in this JSON list")
    ap.add_argument("--max-steps", type=int, default=120)
    ap.add_argument("--min-confidence", type=float, default=0.6,
                    help="below this, route to quarantine regardless of the label")
    ap.add_argument("--out", default=str(ROOT / "recipe/tb2_evolver/fault_routing"))
    args = ap.parse_args()

    bench = ROOT / ".benchmarks" / "tb2"
    only = set(json.loads(Path(args.tasks).read_text())) if args.tasks else None
    jobs = ([bench / j for j in args.jobs] if args.jobs
            else [d for d in sorted(bench.iterdir()) if d.is_dir() and "__task-" not in d.name])

    rows: list[Attribution] = []
    for job in jobs:
        if not job.is_dir():
            continue
        for trial in sorted(job.iterdir()):
            rp = trial / "result.json"
            if not (trial.is_dir() and rp.is_file()):
                continue
            try:
                res = json.loads(rp.read_text())
            except Exception:
                continue
            task = res.get("task_name") or trial.name.split("__")[0]
            if only and task not in only:
                continue
            a = attribute(trial, task, res, args.max_steps)
            if a.attribution_confidence < args.min_confidence and a.destination != "quarantine":
                a.alternatives.append(dict(note="demoted to quarantine by confidence gate",
                                           original_destination=a.destination))
                a.destination = "quarantine"
            rows.append(a)

    if not rows:
        raise SystemExit("no trajectories found")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "attributions.jsonl").open("w", encoding="utf-8") as f:
        for a in rows:
            f.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")

    fails = [a for a in rows if a.observed_outcome == "fail"]
    dest = Counter(a.destination for a in rows)
    fside = Counter(a.fault_side for a in fails)
    edge = Counter(a.interaction_edge or "-" for a in fails)
    mode = Counter(a.failure_mode for a in fails)

    print(f"\n=== fault routing over {len(rows)} trajectories "
          f"({len(fails)} failures, {len(rows)-len(fails)} passes) ===\n")
    print("  fault_side (failures only):")
    for k, v in fside.most_common():
        print(f"    {v:>5}  {100*v/len(fails):>5.1f}%  {k}")
    print("\n  interaction_edge (failures only):")
    for k, v in edge.most_common():
        print(f"    {v:>5}  {100*v/len(fails):>5.1f}%  {k}")
    print("\n  destination (all trajectories):")
    for k, v in dest.most_common():
        print(f"    {v:>5}  {100*v/len(rows):>5.1f}%  {k}")
    print("\n  failure_mode (failures only):")
    for k, v in mode.most_common(12):
        print(f"    {v:>5}  {k}")

    # The headline number this step exists to produce.
    not_model = sum(v for k, v in fside.items() if k in ("ENV", "GRADER", "TOOL", "HARNESS"))
    print(f"\n  >>> {not_model}/{len(fails)} ({100*not_model/max(len(fails),1):.1f}%) of failures are NOT "
          f"model-side.")
    print("      Under the current unrouted loop every one of these was handed to the")
    print("      meta-agent as evidence about the model, or counted against model")
    print("      capability in the score.")

    dsum = sum(a.n_downstream_symptoms for a in fails)
    print(f"\n  >>> {dsum} downstream symptom events were collapsed into "
          f"{len([a for a in fails if a.critical_event is not None])} critical events")
    print(f"      (a mean of {dsum/max(len([a for a in fails if a.critical_event is not None]),1):.1f} "
          f"duplicate signals per root cause avoided).")

    summary = dict(
        n_trajectories=len(rows), n_failures=len(fails),
        fault_side=dict(fside), interaction_edge=dict(edge),
        destination=dict(dest), failure_mode=dict(mode),
        pct_failures_not_model_side=round(100 * not_model / max(len(fails), 1), 1),
        downstream_symptoms_collapsed=dsum,
        min_confidence_gate=args.min_confidence,
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  -> {out}/attributions.jsonl")
    print(f"  -> {out}/summary.json\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

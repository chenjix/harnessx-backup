#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build an SFT corpus from the model's OWN trajectories, sliced at the critical event.

Stage-2 of the routing design: once fault localization says a failure is
model-side, SFT still needs a demonstration of the *right* behaviour, which a
failure transcript does not contain. Three sources, in priority order:

  1. recovery slice  — the trajectory hit a critical event and then recovered.
                       The turns after the event ARE the correction, and they are
                       already in-distribution (same model, same harness, same
                       Harbor containers, /app paths, no THOUGHT: prefix).
  2. pass@n success  — the same task succeeded in another replicate round. The
                       noise-control rounds we run anyway double as pass@n
                       sampling, so this costs nothing extra.
  3. (external)      — Tmax capability retrieval, handled by route_capability.py,
                       used only to top up fault types with no local demonstration.

Why slices and not whole episodes: a single unrecovered failure produces a long
tail of retries, each of which looks like independent evidence. Measured on this
project's own data, 787 downstream symptom events collapsed to 235 critical
events — 3.3 duplicate signals per root cause. Supervising whole episodes trains
on that duplication; supervising the corrected branch does not.

What gets supervised: assistant turns strictly AFTER the critical event, with the
failure and its observation left in the prompt context. So the model is trained
on "given that this just went wrong, here is what recovers it" rather than on the
whole successful path (which it may already be able to produce).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import filter_and_build_sft as F  # noqa: E402
import route_capability as RC  # noqa: E402
import tmax_source  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
# Same error vocabulary the fault router localizes on, so the two stages agree
# on what counts as a failure event.
ERR_RE = re.compile(
    r"\b(error|traceback|exception|failed|failure|not found|no such|permission denied"
    r"|command not found|cannot|unable to|refused|timed? ?out|invalid|denied)\b", re.I
)


def locate_critical(messages: list[dict[str, Any]]) -> int | None:
    """Index of the earliest tool observation that was never followed by a clean one.

    Re-derived on the canonical message list rather than reusing the router's
    turn index: the router walks raw events, while `parse_messages` resolves
    content_refs and de-duplicates assistant copies, so the two indexings differ.
    Same algorithm, consistent index.
    """
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    err_idx = [i for i in tool_idx if ERR_RE.search(str(m_content(messages[i])))]
    if not err_idx:
        return None
    for i in err_idx:
        if not any(
            j > i and not ERR_RE.search(str(m_content(messages[j])))
            for j in tool_idx
        ):
            return i
    return err_idx[0]


def m_content(m: dict[str, Any]) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)


def recovery_pairs(
    messages: list[dict[str, Any]],
    critical: int,
    max_pairs: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """Supervise only the assistant turns after `critical` (the corrected branch)."""
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i <= critical or m.get("role") != "assistant":
            continue
        prompt = F.render_chat(messages[:i])
        response = F.render_assistant(m)
        if len(response.strip()) < 4:
            continue
        if len(prompt) + len(response) > max_chars:
            # Trim the oldest context rather than dropping a good demonstration;
            # the critical event and everything after it must survive the trim.
            keep_from = max(0, critical - 4)
            prompt = F.render_chat(messages[keep_from:i])
            if len(prompt) + len(response) > max_chars:
                continue
        out.append(dict(prompt=prompt, response=response, turn_index=i))
        if len(out) >= max_pairs:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attributions", default=str(ROOT / "recipe/tb2_evolver/fault_routing_clean/attributions.jsonl"))
    ap.add_argument("--name", default="own_recovery_sft")
    ap.add_argument("--tasks", default=None, help="restrict to tasks in this JSON list")
    ap.add_argument("--max-pairs-per-traj", type=int, default=6)
    ap.add_argument("--max-chars", type=int, default=14000)
    ap.add_argument("--per-task", type=int, default=4, help="cap trajectories kept per TB2 task")
    ap.add_argument("--eval-ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--include-clean-success", action="store_true", default=True)
    ap.add_argument("--no-clean-success", dest="include_clean_success", action="store_false")
    ap.add_argument("--tmax-topup-to", type=int, default=0,
                    help="Top up with capability-matched Tmax trajectories until the corpus "
                         "reaches this many trajectories total (0 = own data only).")
    ap.add_argument("--tmax-parquet", default=str(
        ROOT / "data/external/tmax-sft-only-success"
             / "skill_tax_20260505_2.2k_combined_balanced_thinking_only_success"
             / "train-00000-of-00001.parquet"))
    ap.add_argument("--tmax-per-env", type=int, default=2)
    ap.add_argument("--tmax-candidates-per-task", type=int, default=80)
    ap.add_argument("--holdout-tasks", default=None,
                    help="JSON list of tasks to EXCLUDE from training (defaults to filter_and_build_sft.HOLDOUT_TASKS)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    attr_path = Path(args.attributions)
    if not attr_path.is_file():
        raise SystemExit(f"missing attributions: {attr_path}\nRun recipe/tb2_evolver/fault_router.py first.")
    rows = [json.loads(l) for l in attr_path.read_text().splitlines() if l.strip()]

    only = set(json.loads(Path(args.tasks).read_text())) if args.tasks else None
    holdout = (set(json.loads(Path(args.holdout_tasks).read_text()))
               if args.holdout_tasks else set(F.HOLDOUT_TASKS))

    wanted = {"sft_recovery_slice"} | ({"sft_success"} if args.include_clean_success else set())
    cands = [r for r in rows if r.get("destination") in wanted]
    if only:
        cands = [r for r in cands if r["task"] in only]
    # Never train on a held-out task: it exists to measure transfer.
    held = [r for r in cands if r["task"] in holdout]
    cands = [r for r in cands if r["task"] not in holdout]

    print(f"=== candidates from routing ===")
    print(f"  recovery slices : {sum(1 for r in cands if r['destination']=='sft_recovery_slice')}")
    print(f"  clean successes : {sum(1 for r in cands if r['destination']=='sft_success')}")
    print(f"  excluded (holdout tasks): {len(held)}  {sorted({r['task'] for r in held})}")

    # Prefer higher-confidence attributions, then spread across tasks.
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in cands:
        by_task[r["task"]].append(r)
    for v in by_task.values():
        v.sort(key=lambda r: (-float(r.get("attribution_confidence") or 0), rng.random()))

    records: list[dict[str, Any]] = []
    stats = Counter()
    per_task_kept = Counter()
    skipped = Counter()
    for task in sorted(by_task):
        for r in by_task[task][: args.per_task]:
            trial = Path(r["trial_dir"])
            sess = F._find_session_jsonl(trial)
            if sess is None:
                skipped["no_session"] += 1
                continue
            messages, _ = F.parse_messages(sess)
            if not any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages):
                skipped["no_structured_tool_calls"] += 1
                continue

            if r["destination"] == "sft_recovery_slice":
                crit = locate_critical(messages)
                if crit is None:
                    skipped["critical_event_not_relocatable"] += 1
                    continue
                pairs = recovery_pairs(messages, crit, args.max_pairs_per_traj, args.max_chars)
                kind = "recovery_slice"
            else:
                # Clean success: no critical event, supervise from the start but
                # still cap pairs so one long episode cannot dominate.
                rec = dict(task=task, run=trial.parent.name, trial_dir=str(trial),
                           reward=1.0, model="own", source_bucket="own_clean_success",
                           quality_score=1.0, weight=1.0, n_tool_turns=r.get("n_tool_calls", 0),
                           n_recovered_tools=0, messages=messages)
                pairs = F.expand_turn_pairs(rec, max_pairs=args.max_pairs_per_traj)
                pairs = [p for p in pairs if len(p["prompt"]) + len(p["response"]) <= args.max_chars]
                kind = "clean_success"

            if not pairs:
                skipped[f"no_pairs_{kind}"] += 1
                continue

            for p in pairs:
                records.append(dict(
                    prompt=p["prompt"], response=p["response"],
                    task=task, run=trial.parent.name, reward=1.0, weight=1.0,
                    source_bucket=f"own_{kind}", quality_score=1.0,
                    turn_index=p.get("turn_index", 0),
                ))
            stats[kind] += 1
            per_task_kept[task] += 1

    n_own_traj = sum(stats.values())
    n_own_pairs = len(records)

    # ── Tmax top-up, driven by the per-task DEFICIT the routing exposes ───────
    # Own recovery slices are in-distribution and therefore preferred, but they
    # are concentrated on the handful of tasks the model can already sometimes
    # solve: fix-git / openssl / configure-git-webserver carry 15-19 slices each,
    # while custom-memory-heap-crash / build-cython-ext / gcode-to-text have
    # 0-1 despite 22-25 model-side failures apiece. Those are exactly the tasks
    # with demand and no local demonstration, so the external budget goes there
    # rather than being spread uniformly (which is what route_tmax.py did, and it
    # transferred nothing).
    tmax_meta: dict[str, Any] = {}
    if args.tmax_topup_to and args.tmax_topup_to > n_own_traj:
        need_total = args.tmax_topup_to - n_own_traj
        demand = Counter()
        for r in rows:
            if r.get("destination") == "model_training" and r["task"] not in holdout:
                if not only or r["task"] in only:
                    demand[r["task"]] += 1
        deficit = {t: max(0, demand[t] - per_task_kept.get(t, 0)) for t in demand}
        deficit = {t: v for t, v in deficit.items() if v > 0}
        if not deficit:
            print("\n  tmax top-up: no per-task deficit — own data already covers demand")
        else:
            tot = sum(deficit.values())
            # Largest-remainder allocation with `need_total` as a HARD cap.
            #
            # The previous form was `max(1, round(need_total * v / tot))`, whose
            # floor of 1 per deficit task made the real total `max(need_total,
            # len(deficit))`. With 28 tasks that silently turned a request for 4
            # external trajectories into 8+ — which is how a corpus asked to stay
            # majority-own ends up majority-Tmax. The cap has to bind, so tasks
            # below the cut get 0 rather than a courtesy 1.
            exact = {t: need_total * v / tot for t, v in deficit.items()}
            quota = {t: int(x) for t, x in exact.items()}
            rem = need_total - sum(quota.values())
            for t, _ in sorted(exact.items(), key=lambda kv: (-(kv[1] - int(kv[1])), -deficit[kv[0]])):
                if rem <= 0:
                    break
                quota[t] += 1
                rem -= 1
            quota = {t: q for t, q in quota.items() if q > 0}
            dropped = len(deficit) - len(quota)
            print(f"\n=== Tmax top-up (deficit-driven) ===")
            print(f"  own trajectories : {n_own_traj}")
            print(f"  target total     : {args.tmax_topup_to}  -> need {need_total} external")
            print(f"  quota allocated  : {sum(quota.values())} across {len(quota)} tasks"
                  + (f"  ({dropped} lower-deficit tasks got 0 — the cap binds)" if dropped else ""))

            # Only the tasks that survived the cap. These reads touch TB2
            # reference solutions (canary-bearing) to build retrieval vocabulary
            # only — narrowing to `quota` keeps that exposure to the minimum the
            # allocation actually needs.
            tb2_fp = RC.load_tb2_fingerprints(sorted(quota))
            tax = __import__("pandas").read_parquet(RC.TAXONOMY_PARQUET)
            env_fp = {}
            for i in tax.index:
                r_ = tax.loc[i]
                blob = " ".join(str(r_.get(c) or "") for c in
                                ("truth", "description", "primitive_skills", "language"))
                env_fp[str(r_["task_id"])] = RC.capability_tokens(blob)
            idf = RC.build_idf(list(env_fp.values()) + list(tb2_fp.values()))

            traj = __import__("pandas").read_parquet(args.tmax_parquet)
            traj["task_id"] = traj["metadata"].apply(lambda m: m["task"])
            by_env: dict[str, list[int]] = defaultdict(list)
            for i in traj.index:
                by_env[str(traj.at[i, "task_id"])].append(i)

            per_env_used: Counter = Counter()
            tmax_added = Counter()
            tmax_pairs = 0
            # Iterate the *quota*, not the deficit: tasks the cap zeroed out have
            # no entry in quota and must not be visited at all.
            for t in sorted(quota, key=lambda x: -deficit[x]):
                if not tb2_fp.get(t):
                    continue
                scored = [(e, RC.similarity(tb2_fp[t], fp, idf)) for e, fp in env_fp.items()]
                scored = [(e, sc) for e, sc in scored if sc >= 0.05]
                scored.sort(key=lambda x: -x[1])
                took = 0
                for env, sim in scored[: args.tmax_candidates_per_task]:
                    if took >= quota[t]:
                        break
                    for idx in by_env.get(env, []):
                        if took >= quota[t]:
                            break
                        if per_env_used[env] >= args.tmax_per_env:
                            continue
                        row = traj.loc[idx]
                        md = dict(row["metadata"])
                        if bool(md.get("has_ctrl_c")) or bool(md.get("json_extraction_failed")):
                            continue
                        if not bool(md.get("has_task_complete")):
                            continue
                        msgs = [tmax_source._tmax_message_to_internal(dict(m))
                                for m in list(row["messages"])]
                        ntc = sum(len(m.get("tool_calls") or []) for m in msgs
                                  if m.get("role") == "assistant")
                        if not (2 <= ntc <= 60):
                            continue
                        rec = dict(task=str(md.get("task")), run="allenai/tmax-sft:only_success",
                                   trial_dir=f"tmax://{md.get('trial_name')}", reward=1.0,
                                   model=str(md.get("source_model") or "Qwen/Qwen3.6-27B"),
                                   source_bucket="tmax_deficit_topup", quality_score=1.0,
                                   weight=1.0, n_tool_turns=ntc, n_recovered_tools=0,
                                   messages=msgs)
                        ps = F.expand_turn_pairs(rec, max_pairs=args.max_pairs_per_traj)
                        ps = [q for q in ps
                              if len(q["prompt"]) + len(q["response"]) <= args.max_chars]
                        if not ps:
                            continue
                        for q in ps:
                            records.append(dict(
                                prompt=q["prompt"], response=q["response"],
                                task=str(md.get("task")), run="allenai/tmax-sft:only_success",
                                reward=1.0, weight=1.0, source_bucket="tmax_deficit_topup",
                                quality_score=1.0, turn_index=q.get("turn_index", 0),
                                topup_for=t, similarity=round(sim, 4),
                            ))
                        tmax_pairs += len(ps)
                        per_env_used[env] += 1
                        tmax_added[t] += 1
                        took += 1
                print(f"  {t:30s} deficit={deficit[t]:>3} quota={quota[t]:>3} took={tmax_added[t]:>3}")
            tmax_meta = dict(
                requested=need_total, added=sum(tmax_added.values()), pairs=tmax_pairs,
                per_tb2_task=dict(tmax_added), deficit=deficit, quota=quota,
                source="allenai/tmax-sft (only_success), capability-matched per deficit task",
            )
            print(f"  ---> external trajectories added: {sum(tmax_added.values())} "
                  f"({tmax_pairs} pairs)")

    if not records:
        raise SystemExit(f"no usable pairs built (skipped: {dict(skipped)})")

    # Same on-disk shape as every other corpus so train_sft.sh needs no changes.
    rng2 = random.Random(args.seed)
    rng2.shuffle(records)
    n_eval = max(1, int(len(records) * args.eval_ratio))
    train, ev = records[n_eval:], records[:n_eval]
    out = F.OUT_ROOT / args.name
    out.mkdir(parents=True, exist_ok=True)
    F.write_jsonl(out / "train.jsonl", train)
    F.write_jsonl(out / "eval.jsonl", ev)

    bad = [r for r in records if "<tool_call>" in r["response"] and "<function=" not in r["response"]]
    if bad:
        raise SystemExit(f"ABORT: {len(bad)} targets are not qwen3_xml — serving stack could not read them back")

    n_tmax_pairs = sum(1 for r in records if r["source_bucket"] == "tmax_deficit_topup")
    summary = dict(
        name=args.name,
        n_trajectories=sum(stats.values()) + (tmax_meta.get("added", 0) if tmax_meta else 0),
        n_own_trajectories=sum(stats.values()),
        n_tmax_trajectories=tmax_meta.get("added", 0) if tmax_meta else 0,
        own_pairs=n_own_pairs, tmax_pairs=n_tmax_pairs,
        own_pair_fraction=round(n_own_pairs / max(len(records), 1), 3),
        tmax_topup=tmax_meta or None,
        n_train_pairs=len(train), n_eval_pairs=len(ev),
        by_kind=dict(stats),
        tasks=sorted(per_task_kept),
        pairs_per_task={t: per_task_kept[t] for t in sorted(per_task_kept)},
        skipped=dict(skipped),
        holdout_tasks=sorted(holdout),
        per_task_cap=args.per_task,
        source="own-model trajectories, sliced at the routed critical event",
        supervises="assistant turns strictly after the critical event (the corrected branch)",
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "provenance_extra.json").write_text(json.dumps(dict(
        model="own (same model that produced the trajectories)",
        n_own_model_trajectories=sum(stats.values()),
        n_tmax_external_trajectories=(tmax_meta.get("added", 0) if tmax_meta else 0),
        same_model_only=(not tmax_meta), tmax_only=False,
        routed="fault-localized recovery slices + deficit-driven Tmax top-up",
        n_tmax_external_pairs=n_tmax_pairs,
        structured_tool_calls_only=True, tool_call_format="qwen3_xml",
        holdout_tasks=sorted(holdout),
    ), indent=2) + "\n", encoding="utf-8")

    print(f"\n=== corpus ===")
    print(f"  out           : {out}")
    print(f"  trajectories  : {sum(stats.values())}  {dict(stats)}")
    print(f"  train/eval    : {len(train)} / {len(ev)} pairs")
    print(f"  distinct tasks: {len(per_task_kept)}")
    print(f"  qwen3_xml     : clean")
    if skipped:
        print(f"  skipped       : {dict(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

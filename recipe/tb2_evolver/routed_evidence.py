#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Stage-2 routing, applied per evolve round: split one round's trajectories into
the three consumers, and give the meta-agent only the slice it can actually act on.

The unrouted loop hands the meta-agent every failure. That is the defect this
module exists to fix: a container that never started and a swallowed tool error
look identical in the score, so the meta-agent "fixes" the former by editing a
prompt, which cannot possibly work and burns a round.

Routing per round (destinations come from fault_router.attribute):

    harness_evolution   -> THIS round's meta-agent evidence pack
    sft_recovery_slice  \\
    sft_success          |-> the SFT corpus built after the evolve stage
    model_training      /    (model_training supplies the *deficit* signal only)
    environment_repair  -> reported, excluded from both, never counted as model failure
    benchmark_repair    -> reported, excluded from both
    quarantine          -> excluded; the reason is printed so it can be audited

Two design choices worth stating:

- **Cross-trajectory dedup.** fault_router already collapses downstream symptoms
  *within* a trajectory. Here we collapse *across* trajectories: 20 trials that
  all died on the same malformed-tool-call wrapper are one editable defect with
  count=20, not 20 pieces of evidence. Without this the pack is dominated by
  whichever bug happens to fire most often, and rarer (often deeper) defects are
  never surfaced.

- **A floor, with contested evidence labelled as such.** Measured on two
  independent trajectory sets, unambiguous harness-side evidence runs at a median
  of 0 per round. A pack that is empty half the time makes the loop's H stage a
  no-op. So when Tier-1 evidence is short we promote Tier-2 — attributions whose
  `alternatives` contain a HARNESS hypothesis — ranked by that hypothesis's
  probability, and mark them CONTESTED in the pack. The meta-agent is told the
  difference explicitly; it is not told a guess is a fact.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recipe.tb2_evolver.fault_router import Attribution, attribute  # noqa: E402
from recipe.tb2_evolver.tmax_recovery_index import DEFAULT_OUT as TMAX_INDEX_PATH  # noqa: E402
from recipe.tb2_evolver.tmax_recovery_index import classify as classify_error  # noqa: E402

EVIDENCE_MD = "_ROUTED_EVIDENCE.md"
ROUTING_DIR = "_routing"

# Destinations that are the meta-agent's business.
HARNESS_DESTS = {"harness_evolution"}
# Destinations that feed the SFT builder (positive demonstrations).
SFT_DESTS = {"sft_recovery_slice", "sft_success"}
# Destination that supplies per-task *demand* for SFT data without supplying
# any trainable text itself (these are failures — there is nothing to imitate).
DEMAND_DEST = "model_training"
# Neither side can fix these by training or by editing the harness.
EXCLUDED_DESTS = {"environment_repair", "benchmark_repair", "quarantine"}


def _signature(a: Attribution) -> str:
    """Collapse trajectories that describe the same editable defect.

    Keyed on (failure_mode, editable_surface, normalized action) rather than on
    the raw observation: the observation carries task-specific paths and IDs that
    would defeat grouping, while the action shape is what the harness edit acts on.
    """
    surf = (a.evidence_span or {}).get("editable_surface") or "-"
    act = str((a.evidence_span or {}).get("action") or "")
    # Strip task-specific literals so `cat /app/foo.log` and `cat /srv/bar.log`
    # group together — the defect is in how the call is made, not in the path.
    act = re.sub(r"/\S+", "<path>", act)
    act = re.sub(r"\b\d+\b", "<n>", act)
    act = re.sub(r"\s+", " ", act).strip()[:80]
    return f"{a.failure_mode}|{surf}|{act}"


def _harness_alternative_p(a: Attribution) -> float:
    """Highest probability assigned to a HARNESS hypothesis in `alternatives`."""
    best = 0.0
    for alt in a.alternatives or []:
        if not isinstance(alt, dict):
            continue
        if str(alt.get("fault_side", "")).upper() in ("HARNESS", "TOOL"):
            try:
                best = max(best, float(alt.get("p") or 0.0))
            except (TypeError, ValueError):
                pass
    return best


def _load_tmax_index(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        return {}
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get("index") or {}
    except Exception:
        return {}


def _affordance_gaps(
    model_side: list[Attribution],
    tmax_index: dict[str, list[dict[str, Any]]],
    *,
    max_gaps: int,
    max_exemplars: int,
) -> list[dict[str, Any]]:
    """Pair our unrecovered errors with Tmax recoveries on the same error class.

    This is deliberately *not* an attribution. Our model failing to recover is a
    model fact; the Tmax exemplar is a different model on a different harness.
    What the pairing produces is a reachability question about our harness, which
    is the only thing the meta-agent can act on — so the output is framed as a
    question, and the provenance of both halves is stated in the pack.
    """
    if not tmax_index:
        return []
    buckets: dict[str, list[Attribution]] = defaultdict(list)
    for a in model_side:
        obs = str((a.evidence_span or {}).get("observation") or "")
        if not obs.strip():
            continue
        cls = classify_error(obs)
        if cls and tmax_index.get(cls):
            buckets[cls].append(a)

    gaps: list[dict[str, Any]] = []
    for cls, attrs in sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:max_gaps]:
        # Rank exemplars by how quickly the recovery reached success: a 2-turn
        # recovery is a cleaner demonstration of the affordance than a 30-turn
        # one, where whatever worked is buried in unrelated work.
        ex = sorted(tmax_index[cls], key=lambda e: e.get("assistant_turns_to_success", 999))[:max_exemplars]
        gaps.append(dict(
            error_class=cls,
            n_our_failures=len(attrs),
            our_tasks=sorted({a.task for a in attrs})[:8],
            our_modes=dict(Counter(a.failure_mode for a in attrs)),
            our_examples=[dict(task=a.task,
                               failed_action=str((a.evidence_span or {}).get("action") or "")[:200],
                               observation=str((a.evidence_span or {}).get("observation") or "")[:240])
                          for a in attrs[:2]],
            tmax_recoveries=ex,
        ))
    return gaps


def route_dir(
    traj_dir: Path,
    *,
    max_steps: int = 120,
    min_confidence: float = 0.6,
    min_evidence: int = 3,
    max_evidence: int = 12,
    tmax_index_path: Path | None = None,
    max_affordance_gaps: int = 5,
    max_exemplars: int = 3,
) -> dict[str, Any]:
    """Route every trial under `traj_dir`; write the evidence pack + split manifest."""
    traj_dir = Path(traj_dir).resolve()
    rows: list[Attribution] = []
    for trial in sorted(traj_dir.iterdir()):
        rp = trial / "result.json"
        if not (trial.is_dir() and rp.is_file()) or trial.name.startswith("_"):
            continue
        try:
            res = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        task = res.get("task_name") or trial.name.split("__")[0]
        a = attribute(trial, task, res, max_steps)
        if a.attribution_confidence < min_confidence and a.destination != "quarantine":
            a.alternatives.append(dict(note="demoted to quarantine by confidence gate",
                                       original_destination=a.destination))
            a.destination = "quarantine"
        rows.append(a)

    rdir = traj_dir / ROUTING_DIR
    rdir.mkdir(parents=True, exist_ok=True)
    with (rdir / "attributions.jsonl").open("w", encoding="utf-8") as f:
        for a in rows:
            f.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")

    # ── build the two tiers ─────────────────────────────────────────────────
    tier1 = [a for a in rows if a.destination in HARNESS_DESTS]
    tier2 = sorted(
        (a for a in rows
         if a.observed_outcome == "fail"
         and a.destination not in HARNESS_DESTS
         and _harness_alternative_p(a) > 0),
        key=lambda a: -_harness_alternative_p(a),
    )

    groups: dict[str, dict[str, Any]] = {}

    def _add(a: Attribution, tier: int) -> None:
        sig = _signature(a)
        g = groups.setdefault(sig, dict(tier=tier, count=0, tasks=set(), members=[]))
        # A signature seen as Tier-1 anywhere is Tier-1: an unambiguous instance
        # settles the question for the whole group, and demoting it to CONTESTED
        # because a weaker sibling arrived first would understate the evidence.
        g["tier"] = min(g["tier"], tier)
        g["count"] += 1
        g["tasks"].add(a.task)
        if len(g["members"]) < 3:
            g["members"].append(a)

    for a in tier1:
        _add(a, 1)

    # Promote Tier-2 leads up to `max_evidence`, not up to `min_evidence`.
    #
    # The earlier form stopped as soon as `min_evidence` (3) distinct groups
    # existed. Measured over four real rounds that capped every pack at exactly
    # 3 groups while 7-10 distinct groups were available, and because the ranking
    # is stable the same 3 appeared every round: four consecutive packs had zero
    # new defect signatures, and the meta-agent made no config edit at all in the
    # last two rounds — it had already tried everything it was being shown.
    # `min_evidence` now only governs whether promotion happens at all.
    promoted = 0
    if len(groups) < min_evidence or tier2:
        for a in tier2:
            if len(groups) >= max_evidence:
                break
            _add(a, 2)
            promoted += 1

    ranked = sorted(groups.items(), key=lambda kv: (kv[1]["tier"], -kv[1]["count"]))[:max_evidence]

    # ── carry forward what earlier rounds already saw ────────────────────────
    # A defect that survives several rounds of editing is usually not "not yet
    # attempted" — it is outside what the config can reach (the tool wrapper's
    # argument validation lives in Python, not in the YAML). Saying so lets the
    # meta-agent stop re-attempting it; without this it re-reads an identical
    # CONFIRMED entry every round with no hint that its previous fix failed.
    history: dict[str, int] = {}
    parent, stem = traj_dir.parent, re.sub(r"-r\d+-traj$", "", traj_dir.name)
    if stem != traj_dir.name:
        for sib in sorted(parent.glob(f"{stem}-r*-traj")):
            if sib == traj_dir:
                continue
            prev = sib / ROUTING_DIR / "seen_signatures.json"
            if prev.is_file():
                try:
                    for sig in json.loads(prev.read_text(encoding="utf-8")):
                        history[sig] = history.get(sig, 0) + 1
                except Exception:
                    pass
    (rdir / "seen_signatures.json").write_text(
        json.dumps([sig for sig, _ in ranked], ensure_ascii=False), encoding="utf-8")

    # ── write the pack the meta-agent reads ─────────────────────────────────
    fails = [a for a in rows if a.observed_outcome == "fail"]
    excluded = [a for a in fails if a.destination in EXCLUDED_DESTS]
    model_side = [a for a in fails if a.destination == DEMAND_DEST]

    lines: list[str] = []
    lines.append("# Routed evidence for this round\n")
    lines.append(
        "Every failure in this round was attributed to a responsible side before "
        "you were shown it. **Only the defects listed under \"Editable defects\" "
        "are yours to fix.** The rest are listed as counts so you know what the "
        "score contains, but editing the harness cannot address them — do not "
        "write prompt text aimed at them.\n"
    )
    lines.append(f"- trajectories: **{len(rows)}**  ({len(fails)} failed, {len(rows)-len(fails)} passed)")
    lines.append(f"- attributed to the harness/tool layer (yours): **{len(tier1)}**")
    lines.append(f"- attributed to the model (goes to SFT, not to you): **{len(model_side)}**")
    lines.append(f"- environment / benchmark / unattributable (nobody's to fix here): **{len(excluded)}**\n")

    if not ranked:
        lines.append("## Editable defects\n")
        lines.append(
            "**None found this round.** No trajectory produced evidence that the "
            "harness mangled a call, suppressed an error, or failed to surface "
            "feedback. Do not invent a defect to justify an edit: if you cannot "
            "point at a trajectory, prefer returning the config unchanged over "
            "speculative rewording.\n"
        )
    else:
        lines.append("## Editable defects\n")
        lines.append(
            "Grouped across trajectories: `count` is how many trials exhibited the "
            "same defect, so a count of 20 is one bug, not twenty.\n"
        )
        for i, (sig, g) in enumerate(ranked, 1):
            a0: Attribution = g["members"][0]
            tag = "CONFIRMED" if g["tier"] == 1 else "CONTESTED"
            seen_before = history.get(sig, 0)
            stale = "  ⟳ SEEN IN %d EARLIER ROUND(S)" % seen_before if seen_before else ""
            # The failure_mode alone is not a distinguishing title: nine separate
            # `visible_error_no_effective_recovery` groups render as nine identical
            # headings, and a reader skimming the pack cannot tell they are nine
            # different defects. Carry the action shape the signature keys on.
            shape = sig.split("|")[-1][:60] or "(no command captured)"
            lines.append(f"### {i}. [{tag}] `{a0.failure_mode}` — on `{shape}`  "
                         f"(count={g['count']}, tasks={len(g['tasks'])}){stale}")
            if seen_before >= 2:
                lines.append(
                    f"- **This defect has now survived {seen_before} round(s) of edits.** "
                    "Either your previous change did not address it, or it is not "
                    "reachable from this config at all — the tool wrapper's argument "
                    "validation, for instance, lives in Python, not in this YAML. "
                    "If you cannot reach it, say so explicitly in your notes and spend "
                    "this round on something else rather than re-attempting it."
                )
            lines.append(f"- interaction edge: `{a0.interaction_edge or '-'}`")
            surf = (a0.evidence_span or {}).get("editable_surface")
            if surf:
                lines.append(f"- editable surface: **{surf}**")
            if tag == "CONTESTED":
                p = _harness_alternative_p(a0)
                lines.append(
                    f"- ⚠️ primary attribution is `{a0.fault_side}`; harness is the "
                    f"alternative hypothesis at p={p:.2f}. Treat this as a lead to "
                    f"verify against the trajectory, not as an established defect."
                )
            lines.append(f"- affected tasks: {', '.join(sorted(g['tasks'])[:8])}")
            lines.append("- evidence:")
            for m in g["members"]:
                ev = m.evidence_span or {}
                lines.append(f"  - `{m.task}` step {ev.get('step')}: "
                             f"action `{str(ev.get('action') or '')[:160]}`")
                obs = str(ev.get("observation") or "").replace("\n", " ")[:220]
                if obs:
                    lines.append(f"    observation: `{obs}`")
                lines.append(f"    trial: `{Path(m.trial_dir).name}`")
            lines.append("")

    if excluded:
        lines.append("## Not actionable by a harness edit\n")
        by_mode = Counter(a.failure_mode for a in excluded)
        for k, v in by_mode.most_common():
            lines.append(f"- `{k}`: {v}")
        lines.append(
            "\nThese are excluded from the editable list on purpose. If the score "
            "looks low, some of it is these — that is not a signal about your config.\n"
        )

    # ── Tier 3: affordance gaps, derived by contrast against Tmax recoveries ──
    tmax_index = _load_tmax_index(Path(tmax_index_path) if tmax_index_path else TMAX_INDEX_PATH)
    gaps = _affordance_gaps(model_side, tmax_index,
                            max_gaps=max_affordance_gaps, max_exemplars=max_exemplars)
    if gaps:
        lines.append("## Affordance gaps (harness-actionable, derived by contrast)\n")
        lines.append(
            "Each entry pairs an error **our** model failed to recover from with "
            "recoveries a stronger model achieved on the **same error class** "
            "(mined from the Tmax success corpus — a different model on a "
            "different harness). The Tmax half is **not evidence of a defect "
            "here**; it is a demonstration that a recovery exists. Your question "
            "for each: *given the current config, is that recovery reachable?* "
            "Concretely — is the error surfaced in full rather than truncated, "
            "does any tool description or prompt line make the probe an obvious "
            "next move, is there a step budget left to attempt it?\n"
        )
        for i, g in enumerate(gaps, 1):
            lines.append(f"### A{i}. `{g['error_class']}` — our model failed to recover "
                         f"{g['n_our_failures']}x")
            lines.append(f"- our failure modes: "
                         f"{', '.join(f'`{k}`x{v}' for k, v in g['our_modes'].items())}")
            lines.append(f"- affected tasks: {', '.join(g['our_tasks'])}")
            lines.append("- what our model did:")
            for e in g["our_examples"]:
                lines.append(f"  - `{e['task']}`: ran `{e['failed_action']}`")
                lines.append(f"    got: `{str(e['observation']).replace(chr(10), ' ')}`")
            lines.append("- what a successful recovery looks like on this error class:")
            for e in g["tmax_recoveries"]:
                lines.append(f"  - after `{e.get('failed_action', '')[:110]}` failed, "
                             f"recovered with **`{e.get('recovery_action', '')[:150]}`** "
                             f"({e.get('assistant_turns_to_success')} turns to success)")
            lines.append("")

    if model_side:
        lines.append("## Model-side failures (routed to SFT, not to you)\n")
        by_mode = Counter(a.failure_mode for a in model_side)
        for k, v in by_mode.most_common():
            lines.append(f"- `{k}`: {v}")
        lines.append(
            "\nThe model saw the error and did not recover. A harness edit can "
            "sometimes help here (by making recovery easier to reach — see the "
            "affordance gaps above), but the primary fix is training data, which "
            "is already being collected.\n"
        )

    (traj_dir / EVIDENCE_MD).write_text("\n".join(lines), encoding="utf-8")

    # ── annotate per-task markdown so the verdict travels with the trajectory ─
    by_task: dict[str, list[Attribution]] = defaultdict(list)
    for a in rows:
        by_task[a.task].append(a)
    for task, attrs in by_task.items():
        md = traj_dir / f"{task}.md"
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        if "<!-- routing -->" in text:
            continue
        a = attrs[0]
        block = [
            "",
            "<!-- routing -->",
            "## Routing verdict",
            f"- fault_side: **{a.fault_side}**",
            f"- interaction_edge: `{a.interaction_edge or '-'}`",
            f"- failure_mode: `{a.failure_mode}`",
            f"- destination: **{a.destination}**",
            f"- confidence: {a.attribution_confidence:.2f}",
        ]
        if a.destination not in HARNESS_DESTS:
            block.append("- **This failure is not addressable by editing the harness config.**")
        try:
            md.write_text(text + "\n".join(block) + "\n", encoding="utf-8")
        except Exception:
            pass

    split = dict(
        traj_dir=str(traj_dir),
        n_trajectories=len(rows),
        n_failures=len(fails),
        destinations=dict(Counter(a.destination for a in rows)),
        fault_side_failures=dict(Counter(a.fault_side for a in fails)),
        harness_evidence_tier1=len(tier1),
        harness_evidence_promoted_tier2=promoted,
        editable_defect_groups=len(ranked),
        affordance_gaps=len(gaps),
        affordance_gap_classes=[g["error_class"] for g in gaps],
        total_harness_actionable_items=len(ranked) + len(gaps),
        sft_candidates=sum(1 for a in rows if a.destination in SFT_DESTS),
        sft_demand=dict(Counter(a.task for a in model_side)),
        excluded=len(excluded),
    )
    (rdir / "split.json").write_text(json.dumps(split, indent=2, ensure_ascii=False), encoding="utf-8")
    return split


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traj_dir", help="a round's trajectories dir (contains trial subdirs)")
    ap.add_argument("--max-steps", type=int, default=int(os.environ.get("TB2_MAX_STEPS", "120")))
    ap.add_argument("--min-confidence", type=float, default=0.6)
    ap.add_argument("--min-evidence", type=int, default=3,
                    help="promote CONTESTED leads until this many distinct defects exist")
    ap.add_argument("--max-evidence", type=int, default=12)
    ap.add_argument("--tmax-index", default=str(TMAX_INDEX_PATH),
                    help="recovery index from tmax_recovery_index.py; '' disables affordance gaps")
    ap.add_argument("--max-affordance-gaps", type=int, default=5)
    ap.add_argument("--max-exemplars", type=int, default=3)
    args = ap.parse_args()

    split = route_dir(Path(args.traj_dir), max_steps=args.max_steps,
                      min_confidence=args.min_confidence,
                      min_evidence=args.min_evidence, max_evidence=args.max_evidence,
                      tmax_index_path=Path(args.tmax_index) if args.tmax_index else Path("/nonexistent"),
                      max_affordance_gaps=args.max_affordance_gaps,
                      max_exemplars=args.max_exemplars)
    print(json.dumps(split, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

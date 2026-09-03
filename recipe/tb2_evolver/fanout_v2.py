"""Fan-out v2: stacked proposals, holistic screening, complementary merge.

v1 (``fanout.propose_and_screen``) is unchanged. This module is the opt-in
path behind ``--fanout-mode v2``.

Round shape::

    parent
      │
      ├── focus_1 (may STACK several edits) ─► evolve() ─┐
      ├── focus_2                                          ├─► N proposals
      └── focus_N                                         ┘
                                 │
                    screen_structural     drop no-ops + repeats
                    llm review            RANK only — do not cut
                    mini-eval v2          probe all; rank by net
                                          (gains minus weighted regressions)
                                 │
                    complementary merge  union additive edits whenever
                                          two changesets are complementary
                                          (no probe-gain requirement)
                                 │
                          k + merge → full eval
                          ties / unique-gain kept as next-round pivots
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from .fanout import ProposalSpec, _attach_changesets, fan_out
from .merge import merge_harness_configs
from .population import Archive, Node
from .screen import (
    Candidate,
    LLMComplete,
    ProbeRunner,
    changeset_signature,
    focus_tasks_from_notes,
    screen_llm_review,
    screen_structural,
    select_probe_tasks,
)
from .screen_v2 import (
    REVIEW_SYSTEM_V2,
    pick_complementary_group,
    score_probe,
    screen_mini_eval_v2,
)

logger = logging.getLogger(__name__)

__all__ = ["build_focus_notes_v2", "format_pivot_brief", "propose_and_screen_v2"]

_STACK_FOOTER = (
    "You MAY stack multiple complementary edits in this one candidate "
    "(e.g. a new processor AND a prompt reminder AND a tool tweak) if they "
    "address distinct failure modes visible in the trajectories. One "
    "candidate is allowed to contain several additive mechanisms; do not "
    "restrict yourself to a single-line change."
)

_SYNTH_FOCUS = (
    "**Synthesize across pivot harnesses.** Multiple live harnesses sit at "
    "similar scores with different per-task coverage. Read EACH pivot's "
    "`config` and `trajectories_dir` listed below. Keep mechanisms that "
    "uniquely solve tasks in one lineage without blindly stacking everything. "
    "A complementary union is the point of this slot."
)


def format_pivot_brief(
    pivots: Sequence[dict],
    *,
    current_config: Path | None = None,
) -> str:
    """Markdown table the meta-agent reads before proposing."""
    if not pivots:
        return ""
    lines = [
        "| id | score | config | trajectories | unique solves | unique losses | this proposal starts from |",
        "|----|------:|--------|--------------|---------------|---------------|---------------------------|",
    ]
    current = str(Path(current_config).resolve()) if current_config else ""
    for p in pivots:
        cfg = str(p.get("config") or "")
        traj = str(p.get("trajectories") or p.get("trajectories_dir") or "")
        uniq = ", ".join(p.get("unique_solves") or []) or "—"
        lost = ", ".join(p.get("unique_losses") or []) or "—"
        score = p.get("score")
        score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "—"
        marker = "yes" if current and Path(cfg).resolve() == Path(current).resolve() else ""
        lines.append(
            f"| `{p.get('id', '')}` | {score_s} | `{cfg}` | `{traj}` | {uniq} | {lost} | {marker} |"
        )
    return "\n".join(lines)


def _build_proposal_specs(
    *,
    n: int,
    focus_notes: Sequence[str],
    parent_config: Path,
    trajectories_dir: Path,
    parent_results: dict[str, bool],
    parent_node_id: str | None,
    pivots: Sequence[dict],
) -> list[ProposalSpec]:
    """Round-robin branch from each pivot; reserve the last slot to synthesize."""
    pivot_cfgs = [
        (
            Path(p["config"]),
            Path(p["trajectories"]) if p.get("trajectories") else Path(trajectories_dir),
            dict(p.get("parent_results") or {}),
            str(p.get("id") or ""),
        )
        for p in pivots
        if p.get("config")
    ]
    if not pivot_cfgs:
        pivot_cfgs = [(
            Path(parent_config), Path(trajectories_dir), dict(parent_results), parent_node_id or "",
        )]

    synthesize_last = n >= 3 and len(pivot_cfgs) >= 2
    specs: list[ProposalSpec] = []
    for i in range(n):
        synthesize = bool(synthesize_last and i == n - 1)
        if synthesize:
            cfg, traj, results, nid = pivot_cfgs[0]
            focus = _SYNTH_FOCUS
        else:
            cfg, traj, results, nid = pivot_cfgs[i % len(pivot_cfgs)]
            focus = focus_notes[i] if i < len(focus_notes) else _V2_GENERIC[i % len(_V2_GENERIC)]
        # A pivot without its own traj dir still sees the round's trajectories.
        if not traj.is_dir():
            traj = Path(trajectories_dir)
        if not results:
            results = dict(parent_results)
        specs.append(ProposalSpec(
            idx=i,
            focus=focus,
            parent_config=cfg,
            trajectories_dir=traj,
            synthesize=synthesize,
            parent_results=results,
            parent_node_id=nid or None,
        ))
    return specs


_V2_GENERIC: tuple[str, ...] = (
    "**Recovery from tool errors, stacked if needed.** Find where the agent "
    "hit a failing tool call and then repeated it or gave up. You may combine "
    "a recovery processor with a prompt reminder and a stop/verify tweak if "
    "the trajectory shows more than one cause.",

    "**Context hygiene plus pacing.** Find where the agent lost track of "
    "earlier findings or drowned in tool-result noise. You may stack a "
    "compaction/filter change with a step-budget reminder if both are implicated.",

    "**System prompt specificity, plus a mechanism if the agent already knew.** "
    "Find instructions the agent failed to follow. Edit the prompt; if the "
    "agent knew what to do but had no mechanism, also add a processor or tool.",

    "**Tool surface plus verification.** Find a task where the available tools "
    "forced an awkward workaround, or where the agent declared success without "
    "checking. Add/re-describe a tool and/or change completion criteria — both, "
    "if both are visible in the trajectory.",

    "**Loop-breaking plus step-budget.** Find repetition loops AND places the "
    "agent ran out of steps. Stack a loop detector with a pacing/reminder "
    "change when both show up.",

    "**Stopping and verification, stacked with recovery.** Find where the "
    "agent declared success without checking, or burned budget after the work "
    "was done. Combine completion criteria with a recovery path if the "
    "trajectory shows both.",
)


def build_focus_notes_v2(
    n: int,
    *,
    parent_results: dict[str, bool] | None = None,
    failure_digest: dict[str, str] | None = None,
) -> list[str]:
    """Like v1, but every note explicitly allows stacking, and one slot is a combo.

    Failure-task-first assignment is kept so the evidence gate still has a
    named trajectory to point at. The last reserved slot (when there are at
    least two failures and room in the batch) asks one sibling to fix TWO
    failed tasks in a single stacked candidate.
    """
    notes: list[str] = []
    failed = sorted(t for t, ok in (parent_results or {}).items() if not ok)

    # Reserve one combo slot when we have ≥2 failures and n ≥ 3, so a
    # dedicated sibling tries stacking across tasks rather than every sibling
    # only seeing one.
    reserve_combo = n >= 3 and len(failed) >= 2
    single_budget = n - (1 if reserve_combo else 0)

    for task in failed:
        if len(notes) >= single_budget:
            break
        hint = (failure_digest or {}).get(task, "")
        notes.append(
            f"**Task `{task}` fails.** Read that task's trajectory in "
            f"`trajectories_dir` first and diagnose why before proposing "
            f"anything. Fix the harness capability the failure exposes — not "
            f"the task. {_STACK_FOOTER}"
            + (f"\n\nObserved: {hint}" if hint else "")
        )

    if reserve_combo and len(notes) < n:
        a, b = failed[0], failed[1]
        notes.append(
            f"**Tasks `{a}` and `{b}` both fail.** Diagnose both trajectories. "
            "If they share a cause, one stacked harness change; if they have "
            f"distinct causes, stack both fixes in this candidate. {_STACK_FOOTER}"
        )

    i = 0
    while len(notes) < n:
        lever = _V2_GENERIC[i % len(_V2_GENERIC)]
        notes.append(f"{lever}\n\n{_STACK_FOOTER}")
        i += 1

    seen: set[str] = set()
    for j, note in enumerate(notes):
        if note in seen:
            note = (
                f"{note}\n\n(Proposal variant {j}: another sibling shares this "
                "lever — stack a different combination of mechanisms.)"
            )
            notes[j] = note
        seen.add(note)
    return notes[:n]


def _register(
    *,
    candidates: Sequence[Candidate],
    archive: Archive,
    parent: Node,
    focus_notes: Sequence[str],
) -> None:
    for c in candidates:
        node = archive.add(
            parent_id=c.source_parent_id or parent.id,
            config=str(c.config),
            round=parent.round + 1,
            signature=c.signature,
            status="pending" if c.alive else "screened_out",
        )
        c.node = node
        archive.mark(
            node.id,
            node.status,
            dropped_by=c.dropped_by,
            reason=c.drop_reason,
            llm_rank=c.llm_rank,
            probe_score=c.probe_score,
            net_score=c.net_score,
            predicted=c.predicted or None,
            focus=focus_notes[c.idx] if c.idx < len(focus_notes) else None,
            merged_from=list(c.merged_from) if c.merged_from else None,
        )


async def propose_and_screen_v2(
    *,
    meta_agent,
    archive: Archive,
    parent: Node,
    parent_config: Path,
    parent_results: dict[str, bool],
    trajectories_dir: Path,
    round_dir: Path,
    task_universe: Sequence[str],
    llm: LLMComplete | None,
    run_probe: ProbeRunner | None,
    n: int = 8,
    keep: int = 2,
    llm_keep: int | None = None,
    max_concurrent: int = 4,
    n_probe_solved: int = 2,
    n_probe_unsolved: int = 1,
    failure_digest: dict[str, str] | None = None,
    merge: bool = True,
    pivots: Sequence[dict] | None = None,
) -> list[Candidate]:
    """v2 propose → screen. Returns up to ``keep`` individuals plus an optional merge.

    ``llm_keep`` is accepted for signature compatibility with v1 but ignored:
    after structural screening every remaining candidate is ranked (not cut)
    and then probed. The only size cut is the net-score keep-slice, plus the
    complementary merge which is extra whenever two changesets are complementary.
    """
    del llm_keep  # v2 never cuts on LLM rank; kept in the signature for callers.
    round_dir = Path(round_dir)
    focus_notes = build_focus_notes_v2(
        n, parent_results=parent_results, failure_digest=failure_digest
    )
    pivot_list = list(pivots or [])
    specs = _build_proposal_specs(
        n=n,
        focus_notes=focus_notes,
        parent_config=parent_config,
        trajectories_dir=trajectories_dir,
        parent_results=parent_results,
        parent_node_id=parent.id,
        pivots=pivot_list,
    )
    pivot_brief = format_pivot_brief(pivot_list) if len(pivot_list) >= 2 else ""
    candidates = await fan_out(
        meta_agent=meta_agent,
        parent_config=parent_config,
        trajectories_dir=trajectories_dir,
        round_dir=round_dir,
        n=n,
        focus_notes=focus_notes,
        max_concurrent=max_concurrent,
        stack_edits=True,
        specs=specs,
        pivot_brief=pivot_brief or None,
    )
    if not candidates:
        logger.warning("[fanout-v2] no proposals survived generation")
        return []

    named_focus = focus_tasks_from_notes(focus_notes)
    for c in candidates:
        note = specs[c.idx].focus if c.idx < len(specs) else ""
        c.focus_tasks = focus_tasks_from_notes([note]) or list(named_focus)
        if not c.parent_results:
            c.parent_results = dict(parent_results)

    _attach_changesets(candidates, parent_config)
    alive = screen_structural(candidates, archive=archive)

    if alive and llm is not None:
        alive = await screen_llm_review(
            alive,
            llm=llm,
            keep=len(alive),
            parent_results=parent_results,
            system=REVIEW_SYSTEM_V2,
        )

    probed_pool = list(alive)
    probe_tasks: list[str] = []
    if alive and run_probe is not None:
        probe_tasks = select_probe_tasks(
            parent_results=parent_results,
            task_universe=task_universe,
            n_solved=n_probe_solved,
            n_unsolved=n_probe_unsolved,
            focus_tasks=named_focus,
            canary_mode="stable",
            archive_solved=[n.solved for n in archive.scored()],
            max_total=16,
        )
        logger.info(
            "[fanout-v2] probing %d candidate(s) on %s (focus+stable canaries)",
            len(alive), ",".join(probe_tasks),
        )
        alive = await screen_mini_eval_v2(
            alive,
            run_probe=run_probe,
            probe_tasks=probe_tasks,
            parent_results=parent_results,
            keep=keep,
        )
    else:
        for c in alive[keep:]:
            c.drop("keep_limit", f"outside top {keep} with no probe screen to rank on")
        alive = alive[:keep]

    if merge and len(probed_pool) >= 2:
        group = pick_complementary_group(probed_pool)
        if group:
            merged = await _try_merge(
                group=group,
                parent_config=parent_config,
                round_dir=round_dir,
                archive=archive,
                candidates=candidates,
                run_probe=run_probe,
                probe_tasks=probe_tasks,
                parent_results=parent_results,
            )
            if merged is not None:
                candidates = [*candidates, merged]
                # Complementary is enough: always send the union to full eval.
                if merged not in alive:
                    alive = [*alive, merged]

    _register(
        candidates=candidates,
        archive=archive,
        parent=parent,
        focus_notes=focus_notes,
    )
    logger.info(
        "[fanout-v2] round complete: %d proposed → %d to full eval (%s)",
        len(candidates), len(alive), ", ".join(f"c{c.idx}" for c in alive) or "none",
    )
    return alive


async def _try_merge(
    *,
    group: Sequence[Candidate],
    parent_config: Path,
    round_dir: Path,
    archive: Archive,
    candidates: Sequence[Candidate],
    run_probe: ProbeRunner | None,
    probe_tasks: Sequence[str],
    parent_results: dict[str, bool],
) -> Candidate | None:
    next_idx = max(c.idx for c in candidates) + 1
    seq = 0
    out_dir = (round_dir / f"m{seq}").resolve()
    while out_dir.exists() and any(out_dir.iterdir()):
        seq += 1
        out_dir = (round_dir / f"m{seq}").resolve()

    try:
        cfg = merge_harness_configs(
            parent_config,
            [c.config for c in group],
            out_dir,
            source_idxs=[c.idx for c in group],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[fanout-v2] merge failed: %s", exc)
        return None

    merged = Candidate(
        idx=next_idx,
        config=cfg,
        merged_from=tuple(c.idx for c in group),
    )
    _attach_changesets([merged], parent_config)
    if not merged.changeset:
        logger.info("[fanout-v2] merge produced empty changeset — skipping")
        return None
    sig = merged.signature or changeset_signature(merged.changeset)
    merged.signature = sig
    existing = {c.signature for c in candidates if c.signature}
    existing |= archive.signatures()
    if sig in existing:
        logger.info("[fanout-v2] merge signature %s already seen — skipping", sig)
        return None

    if run_probe is not None and probe_tasks:
        try:
            merged.probe_results = await run_probe(
                config=merged.config, tasks=list(probe_tasks), label=f"probe-m{merged.idx}"
            )
            score_probe(
                merged, probe_tasks=probe_tasks, parent_results=parent_results
            )
            logger.info(
                "[fanout-v2] merge c%d net %.2f (+%d/-%d) from %s",
                merged.idx,
                merged.net_score or 0.0,
                len(merged.probe_gained),
                len(merged.probe_regressed),
                ",".join(f"c{c.idx}" for c in group),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[fanout-v2] merge probe failed: %s", exc)

    return merged

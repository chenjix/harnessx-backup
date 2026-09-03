"""Fan out N harness proposals, screen them down to k, return the survivors.

This replaces the single ``await meta_agent.evolve(...)`` call that used to sit
at the top of each round. The economics that motivate it: a meta-agent call
costs seconds, a full benchmark eval costs minutes-to-hours. The old loop spent
its entire per-round eval budget on ONE unscreened draw. Here the same budget
buys the best 1-2 of N, and the N-1 losers are killed by screens that cost a
small fraction of a real eval.

Round shape::

    parent (from Archive.select_parent)
      │
      ├── focus_1 ─► evolve() ─┐
      ├── focus_2 ─► evolve() ─┤   N concurrent proposals,
      ├── ...                  ├─► each assigned a DIFFERENT focus
      └── focus_N ─► evolve() ─┘   so the batch explores N levers, not one
                                 │
                    screen_structural   (free)      drop no-ops + repeats
                    screen_llm_review   (seconds)   rank by predicted impact
                    screen_mini_eval    (one wave)  drop measured regressions
                                 │
                                 ▼
                          k survivors → full eval

v2 (holistic ranking + complementary merge, stacked proposals) lives in
``fanout_v2.propose_and_screen_v2`` and is selected with ``--fanout-mode v2``.
Tournament (no screens, full-eval every valid proposal) lives in
``fanout_tournament.propose_tournament`` and is selected with
``--fanout-mode tournament``.
This module is the v1 path and stays the default.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .population import Archive, Node
from .screen import (
    Candidate,
    LLMComplete,
    ProbeRunner,
    changeset_signature,
    screen_llm_review,
    screen_mini_eval,
    screen_structural,
    select_probe_tasks,
)

logger = logging.getLogger(__name__)

__all__ = ["ProposalSpec", "build_focus_notes", "fan_out", "propose_and_screen"]


# ─── focus assignment ──────────────────────────────────────────────────────

# Generic lever categories, used to pad the batch when there aren't enough
# observed failures to give every sibling its own. Ordered by how often each
# has actually moved the needle on TB2 harness runs, best first, so a small
# fan-out still gets the high-yield levers.
_GENERIC_FOCUSES: tuple[str, ...] = (
    "**Recovery from tool errors.** Find where the agent hit a failing tool "
    "call and then repeated it or gave up. Change the harness so the failure "
    "is surfaced legibly and a different approach is attempted.",

    "**Context hygiene.** Find where the agent lost track of earlier findings, "
    "re-read files it had already read, or drowned in tool-result noise. "
    "Change what the harness keeps, summarises, or filters.",

    "**System prompt specificity.** Find instructions the agent demonstrably "
    "failed to follow, or a missing instruction whose absence explains a "
    "failure. Edit the prompt template — not the tool set.",

    "**Tool surface.** Find a task where the available tools forced an awkward "
    "or many-step workaround. Add, remove, or re-describe a tool so the direct "
    "path exists.",

    "**Stopping and verification.** Find where the agent declared success "
    "without checking, or burned its budget after the work was already done. "
    "Change the harness's completion criteria.",

    "**Step budget allocation.** Find where the agent ran out of steps mid-task "
    "or wasted early steps on exploration that did not pay off. Change pacing, "
    "reminders, or planning structure.",
)

# Second axis, crossed with the levers above when a fan-out is wider than the
# lever list. Index 0 is empty so the first pass through the levers reads
# exactly as written.
_EDIT_STYLES: tuple[str, ...] = (
    "",
    "prefer REMOVING or simplifying an existing mechanism over adding a new "
    "one — if something in the harness is actively getting in the way, cutting "
    "it is a valid and often stronger fix.",
    "prefer the SMALLEST edit that could possibly work; a one-line prompt or "
    "parameter change that is clearly attributable beats a broad rewrite whose "
    "effect cannot be isolated.",
    "prefer a STRUCTURAL change — a new processor or tool — over prompt "
    "wording, if the evidence shows the agent knew what to do but had no "
    "mechanism to do it.",
)


def build_focus_notes(
    n: int,
    *,
    parent_results: dict[str, bool] | None = None,
    failure_digest: dict[str, str] | None = None,
) -> list[str]:
    """Return ``n`` distinct focus assignments, failure-derived ones first.

    A focus tied to a NAMED failing task beats a generic lever, because the
    evidence gate downstream requires the proposal to point at real trajectory
    evidence — a proposal that starts from a concrete failure has that evidence
    by construction.
    """
    notes: list[str] = []
    failed = sorted(t for t, ok in (parent_results or {}).items() if not ok)
    for task in failed:
        if len(notes) >= n:
            break
        hint = (failure_digest or {}).get(task, "")
        notes.append(
            f"**Task `{task}` fails.** Read that task's trajectory in "
            f"`trajectories_dir` first and diagnose why before proposing "
            f"anything. Fix the harness capability the failure exposes — not "
            f"the task."
            + (f"\n\nObserved: {hint}" if hint else "")
        )
    # Cross lever × edit-style once the levers run out. Plain modulo over
    # _GENERIC_FOCUSES silently hands two siblings the SAME assignment as soon
    # as n exceeds the lever count (n=8 vs 6 levers), which collapses them onto
    # one experiment — precisely what the fan-out exists to avoid. The style
    # axis is a real second dimension: "add a mechanism" and "remove one" are
    # different proposals even against the same lever.
    i = 0
    while len(notes) < n:
        lever = _GENERIC_FOCUSES[i % len(_GENERIC_FOCUSES)]
        style = _EDIT_STYLES[(i // len(_GENERIC_FOCUSES)) % len(_EDIT_STYLES)]
        notes.append(lever if not style else f"{lever}\n\nEdit style for this proposal: {style}")
        i += 1
    # Last-resort de-collision: lever × style is 24 distinct assignments, so a
    # fan-out wider than that would wrap. Distinct text is weaker diversity
    # than a distinct lever, but two siblings with byte-identical briefs are
    # strictly worse — they are one experiment billed twice.
    seen: set[str] = set()
    for j, note in enumerate(notes):
        if note in seen:
            note = f"{note}\n\n(Proposal variant {j}: another sibling shares this "
            f"lever — deliberately take a different concrete approach.)"
            notes[j] = note
        seen.add(note)
    return notes[:n]


# ─── fan-out ───────────────────────────────────────────────────────────────


@dataclass
class ProposalSpec:
    """One meta-agent call: which parent to edit, which trajectories to read."""

    idx: int
    focus: str
    parent_config: Path
    trajectories_dir: Path
    synthesize: bool = False
    parent_results: dict[str, bool] | None = None
    parent_node_id: str | None = None


async def fan_out(
    *,
    meta_agent,
    parent_config: Path,
    trajectories_dir: Path,
    round_dir: Path,
    n: int,
    focus_notes: Sequence[str],
    max_concurrent: int = 4,
    stack_edits: bool = False,
    specs: Sequence[ProposalSpec] | None = None,
    pivot_brief: str | None = None,
) -> list[Candidate]:
    """Run ``n`` evolve() calls concurrently, one per focus note.

    Each proposal gets its own ``round_dir/cN`` output directory — the
    meta-agent's sandbox uses output_dir as its Bash cwd, so siblings sharing
    one directory would overwrite each other's config.yaml.

    Concurrency is capped because every in-flight proposal holds a live harness
    with its own sandbox; the cap is about local resources, not API limits.
    A proposal that raises is dropped and logged, never fatal — losing 1 of N
    is a smaller loss than losing the round.
    """
    if specs is None:
        specs = [
            ProposalSpec(
                idx=i,
                focus=focus_notes[i] if i < len(focus_notes) else "",
                parent_config=Path(parent_config),
                trajectories_dir=Path(trajectories_dir),
            )
            for i in range(n)
        ]
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(spec: ProposalSpec) -> Candidate | None:
        out_dir = (round_dir / f"c{spec.idx}").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        focus = spec.focus
        if spec.synthesize:
            focus = (
                focus
                + "\n\nThis proposal is the SYNTHESIS slot: read every pivot "
                "harness and its trajectories before editing. Combine complementary "
                "mechanisms; do not copy a single parent wholesale."
            )
        async with sem:
            try:
                cfg = await meta_agent.evolve(
                    current_config=spec.parent_config,
                    trajectories_dir=spec.trajectories_dir,
                    output_dir=out_dir,
                    focus_note=focus,
                    stack_edits=stack_edits,
                    pivot_brief=pivot_brief,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[fanout] proposal %d failed: %s", spec.idx, exc)
                return None
        cand = Candidate(idx=spec.idx, config=Path(cfg).resolve())
        cand.parent_config = Path(spec.parent_config).resolve()
        cand.trajectories_dir = Path(spec.trajectories_dir).resolve()
        cand.parent_results = dict(spec.parent_results or {})
        cand.synthesize = bool(spec.synthesize)
        cand.source_parent_id = spec.parent_node_id
        cand.focus_tasks = []
        return cand

    results = await asyncio.gather(
        *(_one(s) for s in specs), return_exceptions=False
    )
    cands = [c for c in results if c is not None]
    logger.info("[fanout] %d/%d proposals produced a config", len(cands), n)
    return cands


def _attach_changesets(candidates: Sequence[Candidate], parent_config: Path) -> None:
    """Diff each candidate against the parent and fingerprint the result."""
    from harnessx.core.harness import HarnessConfig
    from harnessx.meta_harness.agent import compute_changeset

    # from_yaml() takes YAML TEXT; from_yaml_file() takes a path. Passing a
    # path to from_yaml() does not raise — OmegaConf parses the path string as
    # a scalar and yields a default config, so every candidate then diffs to an
    # empty changeset and the structural screen silently kills the whole round.
    try:
        before = HarnessConfig.from_yaml_file(parent_config)
    except Exception as exc:  # noqa: BLE001
        # Not recoverable: without a parent to diff against, every candidate
        # looks like a no-op. Fail loudly rather than emptying the round.
        raise RuntimeError(
            f"[fanout] parent config unreadable, cannot screen: {parent_config}"
        ) from exc

    for c in candidates:
        src = Path(c.parent_config).resolve() if c.parent_config else parent_config
        try:
            before = HarnessConfig.from_yaml_file(src)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[fanout] cand#%d parent unreadable (%s): %s", c.idx, src, exc)
            c.drop("structural", f"parent config unreadable: {exc}")
            continue
        try:
            after = HarnessConfig.from_yaml_file(c.config)
            c.changeset = compute_changeset(before, after)
            c.signature = changeset_signature(c.changeset)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[fanout] cand#%d changeset failed: %s", c.idx, exc)
            c.drop("structural", f"config unreadable: {exc}")


async def propose_and_screen(
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
) -> list[Candidate]:
    """One full round of propose → screen. Returns the ``keep`` survivors.

    Every candidate — survivor or casualty — is registered in the archive with
    its screen verdict, so a later audit can ask whether a screen was killing
    candidates it should have kept.
    """
    focus_notes = build_focus_notes(
        n, parent_results=parent_results, failure_digest=failure_digest
    )
    candidates = await fan_out(
        meta_agent=meta_agent,
        parent_config=parent_config,
        trajectories_dir=trajectories_dir,
        round_dir=round_dir,
        n=n,
        focus_notes=focus_notes,
        max_concurrent=max_concurrent,
    )
    if not candidates:
        logger.warning("[fanout] no proposals survived generation")
        return []

    _attach_changesets(candidates, parent_config)

    alive = screen_structural(candidates, archive=archive)

    if alive and llm is not None:
        alive = await screen_llm_review(
            alive,
            llm=llm,
            keep=llm_keep if llm_keep is not None else max(keep, (len(alive) + 1) // 2),
            parent_results=parent_results,
        )

    if alive and run_probe is not None:
        probe_tasks = select_probe_tasks(
            parent_results=parent_results,
            task_universe=task_universe,
            n_solved=n_probe_solved,
            n_unsolved=n_probe_unsolved,
        )
        logger.info("[fanout] probing %d candidate(s) on %s", len(alive), ",".join(probe_tasks))
        alive = await screen_mini_eval(
            alive,
            run_probe=run_probe,
            probe_tasks=probe_tasks,
            parent_results=parent_results,
            keep=keep,
        )
    else:
        # No probe screen: the keep-slice is doing the final cut, so attribute
        # it like any other drop. An unattributed cut leaves the candidate
        # sitting in the archive as `pending` forever, indistinguishable from
        # one that is genuinely still in flight.
        for c in alive[keep:]:
            c.drop("keep_limit", f"outside top {keep} with no probe screen to rank on")
        alive = alive[:keep]

    # Register everything, survivors last so their ids are stable per round.
    for c in candidates:
        node = archive.add(
            parent_id=parent.id,
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
            predicted=c.predicted or None,
            focus=focus_notes[c.idx] if c.idx < len(focus_notes) else None,
        )

    logger.info(
        "[fanout] round complete: %d proposed → %d to full eval (%s)",
        len(candidates), len(alive), ", ".join(f"c{c.idx}" for c in alive) or "none",
    )
    return alive

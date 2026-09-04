"""Tournament fan-out: no cheap screens, full-eval every valid harness.

Round shape::

    parent
      │
      ├── focus_1 ─► evolve() ─┐
      ├── ...                  ├─► N proposals (default 5)
      └── focus_N ─┘
                 │
        drop system errors / unreadable YAML / no-ops / intra-batch dupes
        (no LLM rank, no probe mini-eval, no keep-slice)
                 │
                 ▼
        all remaining → parallel full eval on the evolve-50 set
                 │
                 ▼
        keep the highest pass-rate as the next parent

The point is N independent measurements and N trajectory sets per round,
not a screened guess at which 1–2 of N to spend the eval budget on.
By default the first round uses N=5 and later rounds use two slots: one
continuation and one complementary synthesis. Their successes are harvested
together into the SFT corpus.

Selected with ``--fanout-mode tournament``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from .fanout import ProposalSpec, _attach_changesets, build_focus_notes, fan_out
from .fanout_v2 import format_pivot_brief
from .population import Archive, Node
from .screen import Candidate, changeset_signature

logger = logging.getLogger(__name__)

__all__ = ["propose_tournament"]


_GENERALIZATION_CONTRACT = """

**Generalization contract.** Treat the named task as evidence, not as the
target. First state the reusable failure class in terms observable by the
agent (tool result, context state, progress, or verification state). Inspect
at least two other trajectories for supporting or counter-evidence when they
exist. The harness edit must trigger from that general state: do not mention
task IDs, benchmark paths, filenames, expected answers, or task-specific
content in the prompt, processor, or tool. Explain why the mechanism should
help unseen tasks and identify which already-solved task class it could hurt.
If the evidence supports only one task, prefer a no-op over a special case.
""".strip()

_SYNTHESIS_FOCUS = """
**Complementary synthesis.** Read every pivot config and trajectory directory
in the pivot table. Combine only mechanisms whose per-task coverage is
complementary: preserve a mechanism that uniquely solves tasks, and use another
pivot to repair its unique regressions. Resolve conflicting prompts/processors
instead of blindly concatenating them. The result must still satisfy the
generalization contract and contain no task-specific trigger.
""".strip()


def _tournament_focus_notes(n: int, **kwargs) -> list[str]:
    # Ordinary fan-out assigns one failing task per sibling. With many failed
    # tasks that means all five tournament calls receive the same kind of
    # assignment and tend to invent near-identical processors. Cross a real
    # failed-task anchor with a distinct mechanism lever instead.
    results = kwargs.get("parent_results") or {}
    digest = kwargs.get("failure_digest") or {}
    failed = sorted(t for t, ok in results.items() if not ok)
    levers = build_focus_notes(n, parent_results={})
    notes = []
    for i, lever in enumerate(levers):
        evidence = ""
        if failed:
            task = failed[i % len(failed)]
            evidence = (
                f"Use failing task `{task}` only as the initial evidence anchor. "
                f"Read its trajectory, then search for the same failure class in "
                f"other trajectories before editing."
            )
            if digest.get(task):
                evidence += f"\n\nObserved: {digest[task]}"
        notes.append(f"{lever}\n\n{evidence}\n\n{_GENERALIZATION_CONTRACT}".strip())
    return notes


def _proposal_specs(*, n, focus_notes, parent_config, trajectories_dir,
                    parent_results, parent_id, pivots):
    usable = [p for p in (pivots or []) if p.get("config")]
    if not usable:
        usable = [{"config": str(parent_config), "trajectories": str(trajectories_dir),
                   "parent_results": parent_results, "id": parent_id}]
    # With two or more live lineages, reserve the final (including a 2-wide
    # low-cost later round) proposal for an explicit complementary synthesis.
    synth_last = n >= 2 and len(usable) >= 2
    specs = []
    for i in range(n):
        synth = synth_last and i == n - 1
        p = usable[0] if synth else usable[i % len(usable)]
        traj = Path(p.get("trajectories") or trajectories_dir)
        if not traj.is_dir():
            traj = Path(trajectories_dir)
        focus = (
            f"{_SYNTHESIS_FOCUS}\n\n{_GENERALIZATION_CONTRACT}"
            if synth else focus_notes[i]
        )
        specs.append(ProposalSpec(
            idx=i, focus=focus,
            parent_config=Path(p["config"]), trajectories_dir=traj,
            synthesize=synth,
            parent_results=dict(p.get("parent_results") or parent_results),
            parent_node_id=str(p.get("id") or parent_id),
        ))
    return specs


def _keep_distinct(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Drop anything that is not a real, distinct harness.

    System-error / unreadable configs are already marked by ``_attach_changesets``.
    No-ops and intra-batch duplicates are not *different* harnesses, so they
    would burn a 50-task eval to re-measure the parent. Previously-rejected
    signatures are NOT banned: tournament never screened them, so the archive
    status ``screened_out`` from a different mode must not leak in.
    """
    seen: dict[str, int] = {}
    for c in candidates:
        if not c.alive:
            continue
        if not c.changeset:
            c.drop("structural", "empty changeset (not a different harness)")
            continue
        sig = c.signature or changeset_signature(c.changeset)
        c.signature = sig
        if sig in seen:
            c.drop("structural", f"duplicate of cand#{seen[sig]} (sig {sig})")
            continue
        seen[sig] = c.idx
    return [c for c in candidates if c.alive]


async def propose_tournament(
    *,
    meta_agent,
    archive: Archive,
    parent: Node,
    parent_config: Path,
    parent_results: dict[str, bool],
    trajectories_dir: Path,
    round_dir: Path,
    task_universe: Sequence[str],
    n: int = 5,
    max_concurrent: int = 5,
    failure_digest: dict[str, str] | None = None,
    pivots: Sequence[dict] | None = None,
    **_ignored,
) -> list[Candidate]:
    """Propose ``n`` distinct harnesses; return every one that is not a system error.

    Extra kwargs (``llm``, ``run_probe``, ``keep``, …) are accepted and ignored so
    the call site can share one kwargs dict with v1/v2.
    """
    del task_universe  # kept in the signature so the call site stays uniform
    focus_notes = _tournament_focus_notes(
        n, parent_results=parent_results, failure_digest=failure_digest
    )
    specs = _proposal_specs(
        n=n, focus_notes=focus_notes, parent_config=parent_config,
        trajectories_dir=trajectories_dir, parent_results=parent_results,
        parent_id=parent.id, pivots=pivots,
    )
    candidates = await fan_out(
        meta_agent=meta_agent,
        parent_config=parent_config,
        trajectories_dir=trajectories_dir,
        round_dir=round_dir,
        n=n,
        focus_notes=focus_notes,
        max_concurrent=max_concurrent,
        specs=specs,
        pivot_brief=format_pivot_brief(pivots or []),
    )
    if not candidates:
        logger.warning("[tournament] no proposals survived generation")
        return []

    _attach_changesets(candidates, parent_config)
    alive = _keep_distinct(candidates)

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
            focus=specs[c.idx].focus if c.idx < len(specs) else None,
        )

    logger.info(
        "[tournament] %d proposed → %d to full eval (%s)",
        len(candidates),
        len(alive),
        ", ".join(f"c{c.idx}" for c in alive) or "none",
    )
    return alive

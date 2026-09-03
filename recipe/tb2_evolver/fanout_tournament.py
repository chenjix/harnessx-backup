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
Two rounds of N=5 yield 10 distinct harnesses (plus R0) whose successes
are harvested together into the SFT corpus.

Selected with ``--fanout-mode tournament``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from .fanout import _attach_changesets, build_focus_notes, fan_out
from .population import Archive, Node
from .screen import Candidate, changeset_signature

logger = logging.getLogger(__name__)

__all__ = ["propose_tournament"]


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
    **_ignored,
) -> list[Candidate]:
    """Propose ``n`` distinct harnesses; return every one that is not a system error.

    Extra kwargs (``llm``, ``run_probe``, ``keep``, …) are accepted and ignored so
    the call site can share one kwargs dict with v1/v2.
    """
    del task_universe  # kept in the signature so the call site stays uniform
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
        logger.warning("[tournament] no proposals survived generation")
        return []

    _attach_changesets(candidates, parent_config)
    alive = _keep_distinct(candidates)

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
            focus=focus_notes[c.idx] if c.idx < len(focus_notes) else None,
        )

    logger.info(
        "[tournament] %d proposed → %d to full eval (%s)",
        len(candidates),
        len(alive),
        ", ".join(f"c{c.idx}" for c in alive) or "none",
    )
    return alive

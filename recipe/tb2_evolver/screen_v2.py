"""v2 screens: holistic probe ranking + complementary-candidate grouping.

v1's mini-eval hard-drops a candidate that breaks more than one parent-solved
probe task. That killed proposals which newly solved a previously-failed
task (the thing the campaign actually wants) because they also nicked a
canary. v2 keeps every structurally-valid candidate through the probe, ranks
on net gain, and names complementary groups so a later merge can keep both
sides' upside.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from .screen import Candidate, ProbeRunner

logger = logging.getLogger(__name__)

__all__ = [
    "REGRESSION_WEIGHT",
    "REVIEW_SYSTEM_V2",
    "ADDITIVE_KEYS",
    "additive_set",
    "changesets_complementary",
    "score_probe",
    "screen_mini_eval_v2",
    "pick_complementary_group",
]

# One newly-solved probe task outweighs two regressions. A +1/-1 candidate
# is therefore a net win; a +1/-3 candidate ranks below a pure preserver.
REGRESSION_WEIGHT = 0.5

ADDITIVE_KEYS = (
    "processors_added",
    "tools_added",
    "templates_added",
    "processors_config_changed",
    "templates_changed",
)

REVIEW_SYSTEM_V2 = """\
You rank proposed edits to an agent harness config by predicted NET \
benchmark movement.

You will see the parent harness's recent per-task results and N candidate \
changesets. For EACH candidate, commit a prediction, then rank them.

Return ONLY a JSON object, no prose:

{
  "candidates": [
    {"idx": <int>,
     "rank_score": <float 0..1>,
     "should_pass": ["<task ids the edit should newly solve>"],
     "at_risk":     ["<already-solved task ids the edit could break>"],
     "rationale": "<one sentence>"}
  ]
}

Rank on mechanism and expected net: an edit that newly solves a failed \
cluster while risking one already-solved task can still be the right bet. \
Do NOT zero-rank a candidate solely because it lists at_risk tasks. \
Penalise only edits with no plausible mechanism for any observed failure.
"""


def additive_set(changeset: dict) -> frozenset[str]:
    items: list[str] = []
    for key in ADDITIVE_KEYS:
        for value in changeset.get(key) or []:
            items.append(f"{key}:{value}")
    return frozenset(items)


def changesets_complementary(a: dict, b: dict) -> bool:
    """True when each changeset has additive content the other lacks.

    A merge of a subset with its superset is a no-op (the union equals the
    larger one), so those pairs are rejected.
    """
    ua, ub = additive_set(a), additive_set(b)
    if not ua or not ub:
        return False
    return (not ua.issubset(ub)) and (not ub.issubset(ua))


def score_probe(
    c: Candidate,
    *,
    probe_tasks: Sequence[str],
    parent_results: dict[str, bool],
    regression_weight: float = REGRESSION_WEIGHT,
) -> None:
    """Fill gained / regressed / probe_score / net_score on ``c``."""
    if not c.probe_results or not probe_tasks:
        return
    parent = c.parent_results or parent_results
    gained = [
        t for t in probe_tasks
        if not parent.get(t) and c.probe_results.get(t, False)
    ]
    regressed = [
        t for t in probe_tasks
        if parent.get(t) and not c.probe_results.get(t, False)
    ]
    c.probe_gained = gained
    c.probe_regressed = regressed
    c.probe_score = sum(1 for t in probe_tasks if c.probe_results.get(t)) / len(probe_tasks)
    c.net_score = len(gained) - regression_weight * len(regressed)


async def screen_mini_eval_v2(
    candidates: Sequence[Candidate],
    *,
    run_probe: ProbeRunner,
    probe_tasks: Sequence[str],
    parent_results: dict[str, bool],
    keep: int,
    regression_weight: float = REGRESSION_WEIGHT,
) -> list[Candidate]:
    """Probe every alive candidate; rank by net; do NOT hard-drop regressions.

    A candidate that breaks parent-solved probe tasks stays in the pool so
    a later merge can combine its gains with a sibling that preserved those
    tasks. Only the keep-slice after ranking is a drop, and it is attributed
    as ``mini_eval`` / net-score so the archive still has a verdict.
    """
    alive = [c for c in candidates if c.alive]
    if not alive or not probe_tasks:
        return alive[:keep]

    async def _probe(c: Candidate) -> None:
        try:
            c.probe_results = await run_probe(
                config=c.config, tasks=list(probe_tasks), label=f"probe-c{c.idx}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[screen-v2] cand#%d probe failed: %s", c.idx, exc)
            c.probe_results = {}

    await asyncio.gather(*(_probe(c) for c in alive))

    for c in alive:
        score_probe(
            c,
            probe_tasks=probe_tasks,
            parent_results=parent_results,
            regression_weight=regression_weight,
        )
        if c.net_score is not None:
            logger.info(
                "[screen-v2] cand#%d net %.2f probe %.2f (+%d/-%d)",
                c.idx, c.net_score, c.probe_score or 0.0,
                len(c.probe_gained), len(c.probe_regressed),
            )

    ranked = list(alive)
    ranked.sort(
        key=lambda c: (
            -(c.net_score if c.net_score is not None else -999.0),
            -(c.probe_score if c.probe_score is not None else -1.0),
            -(c.llm_rank if c.llm_rank is not None else -1.0),
            c.idx,
        )
    )
    kept, cut = ranked[:keep], ranked[keep:]
    for c in cut:
        c.drop(
            "mini_eval",
            f"net {c.net_score} outside top {keep} (kept for merge consideration "
            f"until this cut; +{len(c.probe_gained)}/-{len(c.probe_regressed)})",
        )
    logger.info("[screen-v2] mini_eval: %d → %d (no regression hard-drop)", len(alive), len(kept))
    return kept


def pick_complementary_group(
    candidates: Sequence[Candidate],
    *,
    max_group: int = 3,
) -> list[Candidate] | None:
    """Pick 2–``max_group`` candidates whose additive edits are worth unioning.

    Complementary changesets are enough — we do not require a measured probe
    gain. Preference order when several pairs qualify:

      1. a gainer + a preserver (if probe data exists)
      2. two gainers with complementary changesets
      3. any complementary pair (including two unprobed or +0-gain siblings)

    Then greedily add further complementary members up to ``max_group``.
    """
    probed = [c for c in candidates if c.changeset and (c.probe_results or c.alive or c.full_solved)]
    pool = probed if len(probed) >= 2 else [c for c in candidates if c.changeset]
    if len(pool) < 2:
        return None

    def _unique_count(c: Candidate) -> int:
        return len(c.full_solved) if c.full_solved else len(c.probe_gained)

    def _pair_key(a: Candidate, b: Candidate) -> tuple:
        complementary = changesets_complementary(a.changeset, b.changeset)
        gain_preserve = bool(a.probe_gained and b.probe_results and not b.probe_regressed and a.idx != b.idx)
        two_gainers = bool(a.probe_gained and b.probe_gained)
        union_size = len(additive_set(a.changeset) | additive_set(b.changeset))
        net_sum = (a.net_score or 0.0) + (b.net_score or 0.0)
        unique_sum = _unique_count(a) + _unique_count(b)
        return (
            1 if complementary else 0,
            1 if gain_preserve else 0,
            1 if two_gainers else 0,
            unique_sum,
            union_size,
            net_sum,
            -a.idx,
            -b.idx,
        )

    best: tuple[Candidate, Candidate] | None = None
    best_key: tuple | None = None
    n = len(pool)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = pool[i], pool[j]
            if a.idx == b.idx:
                continue
            if not changesets_complementary(a.changeset, b.changeset):
                continue
            if b.probe_gained and (not a.probe_gained or (a.probe_regressed and not b.probe_regressed)):
                a, b = b, a
            key = _pair_key(a, b)
            if best_key is None or key > best_key:
                best, best_key = (a, b), key

    if best is None:
        return None

    group = [best[0], best[1]]
    union = additive_set(best[0].changeset) | additive_set(best[1].changeset)
    rest = sorted(
        (c for c in pool if c.idx not in {g.idx for g in group}),
        key=lambda c: (
            0 if c.probe_gained or c.full_solved else 1,
            -(c.net_score if c.net_score is not None else -999.0),
            c.idx,
        ),
    )
    for c in rest:
        if len(group) >= max_group:
            break
        extra = additive_set(c.changeset)
        if not extra or extra.issubset(union):
            continue
        group.append(c)
        union |= extra

    if len(group) < 2:
        return None
    logger.info(
        "[screen-v2] complementary group: %s",
        ", ".join(f"c{c.idx}(+{len(c.probe_gained)}/-{len(c.probe_regressed)})" for c in group),
    )
    return group

"""Cheap screens that cut a fan-out of N harness candidates down to k.

The old loop evaluated every candidate at full price. Since a fan-out makes
candidates nearly free (the meta-agent call is seconds; the eval is minutes),
the binding constraint is eval budget — so the job of this module is to spend
as little of it as possible before the survivors reach the real eval.

Three screens, ordered cheap → expensive, mirroring DarwinX's fail-fast layer
ordering (vendor/gate/gate.py) and its pre-final-eval placement rationale
(vendor/gate/matchfix_gate.py: the post-hoc gates "recorded regression
catastrophes but could not prevent them — they ran *after* the full
final-eval"):

    1. structural  — free.     Drop no-op edits and edits already tried.
    2. llm review  — seconds.  Rank by predicted impact; keep the top m.
    3. mini eval   — ~1 wave.  Run K probe tasks; drop measured regressions.

Every screen records WHY it dropped a candidate onto the node, so a campaign
can be audited after the fact for screens that were wrong — a screen that
keeps killing candidates which would have scored well is worse than no screen.

Screens take their side effects as injected callables (``LLMComplete``,
``ProbeRunner``) rather than importing the adapter, so each is unit-testable
against a fake and the module stays free of pipeline-state coupling.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence

from .population import Archive, Node

logger = logging.getLogger(__name__)

__all__ = [
    "Candidate",
    "changeset_signature",
    "focus_tasks_from_notes",
    "screen_structural",
    "screen_llm_review",
    "screen_mini_eval",
    "select_probe_tasks",
]


# ─── protocols ─────────────────────────────────────────────────────────────


class LLMComplete(Protocol):
    """`(system, user) -> completion text`."""

    def __call__(self, system: str, user: str) -> Awaitable[str]: ...


class ProbeRunner(Protocol):
    """`(config, tasks, label) -> {task_name: solved}`."""

    def __call__(
        self, *, config: Path, tasks: Sequence[str], label: str
    ) -> Awaitable[dict[str, bool]]: ...


# ─── candidate ─────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """One fan-out proposal, carried through the screens."""

    idx: int
    config: Path
    node: Node | None = None
    changeset: dict = field(default_factory=dict)
    signature: str | None = None
    dropped_by: str | None = None
    drop_reason: str | None = None
    llm_rank: float | None = None
    predicted: dict = field(default_factory=dict)
    probe_results: dict[str, bool] = field(default_factory=dict)
    probe_score: float | None = None
    probe_gained: list[str] = field(default_factory=list)
    probe_regressed: list[str] = field(default_factory=list)
    net_score: float | None = None
    merged_from: tuple[int, ...] | None = None
    focus_tasks: list[str] = field(default_factory=list)
    parent_results: dict[str, bool] = field(default_factory=dict)
    parent_config: Path | None = None
    trajectories_dir: Path | None = None
    synthesize: bool = False
    full_solved: list[str] = field(default_factory=list)
    full_score: float | None = None
    source_parent_id: str | None = None

    @property
    def alive(self) -> bool:
        return self.dropped_by is None

    def drop(self, screen: str, reason: str) -> None:
        # First drop wins: attribution should name the CHEAPEST screen that
        # would have caught it, which is the one that ran first.
        if self.dropped_by is None:
            self.dropped_by = screen
            self.drop_reason = reason
            logger.info("[screen] cand#%d dropped by %s — %s", self.idx, screen, reason)


def changeset_signature(changeset: dict) -> str:
    """Stable fingerprint of a changeset dict.

    Two candidates that touch the same levers in the same way are the same
    experiment; evaluating both twice buys one sample's worth of information
    for two samples' worth of GPU.
    """
    blob = json.dumps(changeset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ─── screen 1: structural (free) ───────────────────────────────────────────


def screen_structural(
    candidates: Sequence[Candidate],
    *,
    archive: Archive,
) -> list[Candidate]:
    """Drop no-op edits, intra-batch duplicates, and already-rejected edits."""
    rejected = archive.rejected_signatures()
    seen: dict[str, int] = {}

    for c in candidates:
        if not c.changeset:
            c.drop("structural", "empty changeset (no-op edit)")
            continue
        sig = c.signature or changeset_signature(c.changeset)
        c.signature = sig
        if sig in seen:
            c.drop("structural", f"duplicate of cand#{seen[sig]} (sig {sig})")
            continue
        if sig in rejected:
            c.drop("structural", f"signature {sig} already tried and rejected")
            continue
        seen[sig] = c.idx

    alive = [c for c in candidates if c.alive]
    logger.info("[screen] structural: %d → %d", len(candidates), len(alive))
    return alive


# ─── screen 2: LLM review (seconds) ────────────────────────────────────────

_REVIEW_SYSTEM = """\
You rank proposed edits to an agent harness config by how likely each is to \
raise the harness's benchmark pass rate.

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

Rank on mechanism, not ambition: an edit whose stated mechanism plausibly \
addresses an observed failure outranks a broad rewrite with no failure to \
point at. Penalise edits that touch surfaces unrelated to any observed \
failure — they carry regression risk with no upside.
"""


async def screen_llm_review(
    candidates: Sequence[Candidate],
    *,
    llm: LLMComplete,
    keep: int,
    parent_results: dict[str, bool] | None = None,
    system: str | None = None,
) -> list[Candidate]:
    """Rank candidates by predicted impact, keep the top ``keep``.

    Predictions are stored on each candidate so the next round can FALSIFY them
    against measured flips (DarwinX's predicted-impact contract,
    vendor/gate/predictions.py). A proposer whose predictions never land is a
    proposer whose ranking should stop being trusted.

    On any LLM failure this keeps the first ``keep`` candidates rather than
    dropping everything — a broken screen must not empty the round.
    """
    alive = [c for c in candidates if c.alive]
    if len(alive) <= keep:
        return alive

    summary = {
        "candidates": [
            {"idx": c.idx, "changeset": c.changeset} for c in alive
        ],
    }
    if parent_results:
        summary["parent_solved"] = sorted(t for t, ok in parent_results.items() if ok)
        summary["parent_failed"] = sorted(t for t, ok in parent_results.items() if not ok)

    try:
        raw = await llm(system or _REVIEW_SYSTEM, json.dumps(summary, indent=2))
        ranked = _parse_review(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screen] llm review failed (%s) — keeping first %d", exc, keep)
        return alive[:keep]

    if not ranked:
        logger.warning("[screen] llm review returned nothing usable — keeping first %d", keep)
        return alive[:keep]

    by_idx = {c.idx: c for c in alive}
    for entry in ranked:
        c = by_idx.get(entry.get("idx"))
        if c is None:
            continue
        try:
            c.llm_rank = float(entry.get("rank_score", 0.0))
        except (TypeError, ValueError):
            c.llm_rank = 0.0
        c.predicted = {
            "should_pass": list(entry.get("should_pass") or []),
            "at_risk": list(entry.get("at_risk") or []),
            "rationale": str(entry.get("rationale", "")),
        }

    # Unranked candidates sort last but keep a deterministic order.
    alive.sort(key=lambda c: (-(c.llm_rank if c.llm_rank is not None else -1.0), c.idx))
    survivors, cut = alive[:keep], alive[keep:]
    for c in cut:
        c.drop("llm_review", f"rank {c.llm_rank} outside top {keep}")
    logger.info("[screen] llm review: %d → %d", len(alive), len(survivors))
    return survivors


def _parse_review(raw: str) -> list[dict]:
    """Pull the candidates array out of a completion, tolerating code fences."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(obj, dict):
        entries = obj.get("candidates")
    elif isinstance(obj, list):
        entries = obj
    else:
        return []
    return [e for e in (entries or []) if isinstance(e, dict)]


# ─── screen 3: mini eval (one wave) ────────────────────────────────────────


_TASK_FAIL_RE = re.compile(r"\*\*Task `([^`]+)` fails")
_TASKS_BOTH_FAIL_RE = re.compile(
    r"\*\*Tasks `([^`]+)` and `([^`]+)` both fail"
)


def focus_tasks_from_notes(notes: Sequence[str]) -> list[str]:
    """Named failing tasks assigned to this fan-out (order preserved)."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(task: str) -> None:
        if task and task not in seen:
            seen.add(task)
            out.append(task)

    for note in notes:
        for a, b in _TASKS_BOTH_FAIL_RE.findall(note or ""):
            _add(a)
            _add(b)
        for task in _TASK_FAIL_RE.findall(note or ""):
            _add(task)
    return out


def _stable_canary_key(task: str, stability: float) -> tuple:
    """High stability first; hash tie-break so we never always pick lex-first IDs."""
    digest = hashlib.sha256(task.encode("utf-8")).hexdigest()
    return (-float(stability), digest)


def select_probe_tasks(
    *,
    parent_results: dict[str, bool],
    task_universe: Sequence[str],
    n_solved: int = 2,
    n_unsolved: int = 1,
    focus_tasks: Sequence[str] | None = None,
    canary_mode: str = "lex",
    archive_solved: Sequence[Sequence[str]] | None = None,
    max_total: int | None = None,
) -> list[str]:
    """Pick the probe set: canaries (parent-solved) plus upside (parent-failed).

    ``canary_mode="lex"`` (v1 default) takes lexicographic slices so a resumed
    campaign probes the same tasks. ``canary_mode="stable"`` ranks parent-solved
    tasks by how often scored archive nodes still pass them (true canaries),
    with a hash tie-break so the screen is not stuck on ``task_000024`` forever.

    ``focus_tasks`` (v2) are always included when the parent failed them — otherwise
    a proposer assigned to fix ``task_000028`` is scored on a different failure
    and the screen cannot see the thing it asked for.
    """
    universe = list(task_universe)
    solved = [t for t in universe if parent_results.get(t)]
    unsolved = [t for t in universe if not parent_results.get(t)]

    def _stability(task: str) -> float:
        if not archive_solved:
            return 1.0
        return sum(1 for s in archive_solved if task in s) / len(archive_solved)

    if canary_mode == "stable":
        canaries = sorted(solved, key=lambda t: _stable_canary_key(t, _stability(t)))[:n_solved]
        extra_unsolved = sorted(
            unsolved,
            key=lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest(),
        )
    else:
        canaries = sorted(solved)[:n_solved]
        extra_unsolved = sorted(unsolved)

    focus = [t for t in (focus_tasks or []) if t in universe]
    focus_unsolved = [t for t in focus if not parent_results.get(t)]

    probes: list[str] = []
    seen: set[str] = set()

    def _push(task: str) -> None:
        if task and task not in seen:
            if max_total is not None and len(probes) >= max_total:
                return
            seen.add(task)
            probes.append(task)

    # v1 (no named focus): canaries then unsolved, matching the historical order
    # tests pin. v2: assigned failures first so a proposer is scored on the task
    # it was asked to fix, then extra unsolved, then stable canaries.
    if focus_unsolved:
        for t in focus_unsolved:
            _push(t)
        for t in extra_unsolved[:n_unsolved]:
            _push(t)
        for t in canaries:
            _push(t)
    else:
        for t in canaries:
            _push(t)
        for t in extra_unsolved[:n_unsolved]:
            _push(t)
    if not probes:
        for t in universe[: max(1, n_solved + n_unsolved)]:
            _push(t)
    return probes


async def screen_mini_eval(
    candidates: Sequence[Candidate],
    *,
    run_probe: ProbeRunner,
    probe_tasks: Sequence[str],
    parent_results: dict[str, bool],
    keep: int,
    max_regressions: int = 1,
) -> list[Candidate]:
    """Run the probe set against each candidate; drop measured regressions.

    Candidates are probed CONCURRENTLY — the probe set is small and the eval
    runner already oversubscribes, so N candidates × K tasks is normally one
    wave rather than N sequential ones.

    A candidate that breaks more than ``max_regressions`` parent-solved probe
    tasks is dropped outright. The rest are ranked by probe score and the top
    ``keep`` go to full eval.
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
            # An infra failure is not evidence against the candidate. Leave it
            # alive with no probe score so it sorts below measured ones but can
            # still reach full eval if nothing else survives.
            logger.warning("[screen] cand#%d probe failed: %s", c.idx, exc)
            c.probe_results = {}

    await asyncio.gather(*(_probe(c) for c in alive))

    for c in alive:
        if not c.probe_results:
            continue
        parent = c.parent_results or parent_results
        regressed = [
            t for t in probe_tasks
            if parent.get(t) and not c.probe_results.get(t, False)
        ]
        gained = [
            t for t in probe_tasks
            if not parent.get(t) and c.probe_results.get(t, False)
        ]
        c.probe_gained = gained
        c.probe_regressed = regressed
        c.probe_score = sum(1 for t in probe_tasks if c.probe_results.get(t)) / len(probe_tasks)
        if len(regressed) > max_regressions:
            c.drop(
                "mini_eval",
                f"broke {len(regressed)} parent-solved probe task(s): {','.join(regressed[:3])}",
            )
            continue
        logger.info(
            "[screen] cand#%d probe %.2f (+%d/-%d)",
            c.idx, c.probe_score, len(gained), len(regressed),
        )

    survivors = [c for c in alive if c.alive]
    # Unprobed (infra-failed) candidates sort last via the -1.0 sentinel.
    survivors.sort(
        key=lambda c: (-(c.probe_score if c.probe_score is not None else -1.0), c.idx)
    )
    kept, cut = survivors[:keep], survivors[keep:]
    for c in cut:
        c.drop("mini_eval", f"probe score {c.probe_score} outside top {keep}")
    logger.info("[screen] mini_eval: %d → %d", len(alive), len(kept))
    return kept

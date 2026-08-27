"""Population archive + parent selection for the TB2 harness evolver.

Replaces the single-incumbent hill-climb (`state["best_so_far"]`, one config
per round, revert-on-regression) with a small archive of every candidate the
campaign has ever scored, plus a parent picker that samples from it.

Why the hill-climb underperformed
---------------------------------
The (1+1) loop drew ONE candidate per round and paid a full eval for it. Two
consequences, both observed on real runs:

1. A rejected candidate was gone for good — `_score_and_gate_tb2` reverted to
   the incumbent and the branch was never revisited. DarwinX hit the same wall
   with its score-only picker ("promising-but-undertested branches died off",
   vendor/gate/novelty.py) and fixed it by keeping every node and ranking
   parents by score AND novelty.
2. With sd ~3.0 on a 15-task eval and a gate tolerance of 1.87 tasks, a single
   draw is mostly noise. An archive lets the same config accumulate repeats,
   so `mean_score` converges instead of one lucky draw crowning a winner.

Novelty (GEA / arXiv:2602.04837 §3.1) — each node is a binary vector over the
task universe (z[t]=1 iff solved). Novelty is the mean cosine distance to the
M nearest neighbours. Ranking by ``score * sqrt(novelty)`` biases the picker
toward parents that are both good AND cover a different slice of the task set
than the rest of the archive.

The archive lives inside the existing ``harness_evolve_state.json`` under an
``archive`` key, so resume keeps working untouched.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "Node",
    "Archive",
    "cosine_distance",
    "knn_novelty",
]


# ─── node ──────────────────────────────────────────────────────────────────


@dataclass
class Node:
    """One scored (or pending) harness candidate."""

    id: str
    parent_id: str | None
    config: str
    round: int
    status: str = "pending"
    """``pending`` | ``screened_out`` | ``scored`` | ``incumbent``."""
    scores: list[float] = field(default_factory=list)
    """Every measurement of this config, in order. Mean is the unbiased bar."""
    solved: list[str] = field(default_factory=list)
    signature: str | None = None
    """Changeset fingerprint — two nodes with the same signature are the same
    edit and must not both be evaluated."""
    screen: dict = field(default_factory=dict)
    """Per-screen verdicts kept for post-hoc attribution (which screen killed
    what, and was it right)."""

    @property
    def mean_score(self) -> float | None:
        return (sum(self.scores) / len(self.scores)) if self.scores else None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "config": self.config,
            "round": self.round,
            "status": self.status,
            "scores": list(self.scores),
            "solved": list(self.solved),
            "signature": self.signature,
            "screen": dict(self.screen),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=str(d["id"]),
            parent_id=d.get("parent_id"),
            config=str(d.get("config", "")),
            round=int(d.get("round", 0)),
            status=str(d.get("status", "pending")),
            scores=[float(s) for s in (d.get("scores") or [])],
            solved=list(d.get("solved") or []),
            signature=d.get("signature"),
            screen=dict(d.get("screen") or {}),
        )


# ─── novelty ───────────────────────────────────────────────────────────────


def cosine_distance(a: Sequence[int], b: Sequence[int]) -> float:
    """1 - cosine similarity over two binary task vectors.

    Two nodes that solve nothing are treated as maximally similar (distance 0)
    rather than undefined — an all-zero node carries no exploratory signal, so
    rewarding it with high novelty would be exactly backwards.
    """
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return 1.0 - (dot / (na * nb))


def knn_novelty(
    target: Sequence[int],
    others: Iterable[Sequence[int]],
    *,
    k: int = 3,
) -> float:
    """Mean cosine distance from ``target`` to its ``k`` nearest neighbours.

    Returns 1.0 when there are no neighbours — the first node in an archive is
    maximally novel by definition, which is what makes the very first fan-out
    explore rather than collapse onto one lineage.
    """
    dists = sorted(cosine_distance(target, o) for o in others)
    if not dists:
        return 1.0
    near = dists[: max(1, k)]
    return sum(near) / len(near)


# ─── archive ───────────────────────────────────────────────────────────────


class Archive:
    """Every node the campaign has scored, backed by the run's state dict.

    Mutations write straight through to ``state["archive"]`` so the caller's
    existing ``_save_state`` keeps the archive durable with no extra plumbing.
    """

    def __init__(self, state: dict, *, task_universe: Sequence[str]):
        self._state = state
        self._raw = state.setdefault("archive", {})
        self.task_universe = list(task_universe)

    # -- access ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._raw)

    def nodes(self) -> list[Node]:
        return [Node.from_dict(d) for d in self._raw.values()]

    def get(self, node_id: str) -> Node | None:
        d = self._raw.get(node_id)
        return Node.from_dict(d) if d else None

    def scored(self) -> list[Node]:
        """Nodes with at least one real measurement — the only legal parents."""
        return [n for n in self.nodes() if n.scores]

    def signatures(self) -> set[str]:
        return {n.signature for n in self.nodes() if n.signature}

    def rejected_signatures(self) -> set[str]:
        """Signatures of edits already tried and found wanting. A fan-out that
        re-proposes one of these is burning eval budget on a known loss."""
        return {
            n.signature
            for n in self.nodes()
            if n.signature and n.status == "screened_out"
        }

    # -- mutation ----------------------------------------------------------

    def put(self, node: Node) -> Node:
        self._raw[node.id] = node.to_dict()
        return node

    def add(
        self,
        *,
        parent_id: str | None,
        config: str,
        round: int,
        signature: str | None = None,
        status: str = "pending",
    ) -> Node:
        node = Node(
            id=self._mint_id(config=config, round=round, signature=signature),
            parent_id=parent_id,
            config=config,
            round=round,
            status=status,
            signature=signature,
        )
        return self.put(node)

    def node_for_config(self, config: str, *, round: int) -> Node:
        """Find the node holding ``config``, or adopt it as a new root.

        Configs enter the archive from two directions — proposed by a fan-out
        (already registered) or carried in by the loop (R0's baseline, or a
        config a --resume restored). Looking up by path unifies both, so a
        re-measurement of an existing config appends to that node's scores
        instead of forking a duplicate whose mean would be a single draw again.
        """
        for node in self.nodes():
            if node.config == config:
                return node
        return self.add(parent_id=None, config=config, round=round, status="pending")

    def record_score(self, node_id: str, score: float, solved: Sequence[str]) -> None:
        """Append a measurement. Repeats accumulate rather than overwrite, so
        `mean_score` is an unbiased bar instead of a max over noisy draws."""
        d = self._raw.get(node_id)
        if d is None:
            logger.warning("record_score: unknown node %s", node_id)
            return
        d.setdefault("scores", []).append(float(score))
        d["solved"] = list(solved)
        if d.get("status") in (None, "pending"):
            d["status"] = "scored"

    def mark(self, node_id: str, status: str, **screen: object) -> None:
        d = self._raw.get(node_id)
        if d is None:
            return
        d["status"] = status
        if screen:
            d.setdefault("screen", {}).update(screen)

    def _mint_id(self, *, config: str, round: int, signature: str | None) -> str:
        seed = f"{round}:{signature or config}:{len(self._raw)}"
        return f"n{round}_{hashlib.sha256(seed.encode()).hexdigest()[:8]}"

    # -- vectors + selection ----------------------------------------------

    def vector(self, node: Node) -> list[int]:
        solved = set(node.solved)
        return [1 if t in solved else 0 for t in self.task_universe]

    def novelty_of(self, node: Node, *, k: int = 3) -> float:
        others = [self.vector(n) for n in self.scored() if n.id != node.id]
        return knn_novelty(self.vector(node), others, k=k)

    def rank_parents(self, *, k_neighbours: int = 3) -> list[tuple[float, Node]]:
        """Rank eligible parents by ``mean_score * sqrt(novelty)``, best first.

        Score alone collapses the campaign onto one lineage; novelty alone
        chases noise. The product is GEA's PN score — a parent must be both
        decent and covering ground the rest of the archive does not.
        """
        pool = self.scored()
        ranked: list[tuple[float, Node]] = []
        for n in pool:
            mean = n.mean_score or 0.0
            nov = self.novelty_of(n, k=k_neighbours)
            ranked.append((mean * math.sqrt(max(nov, 0.0)), n))
        # Deterministic tie-break on node id keeps resume reproducible.
        ranked.sort(key=lambda t: (-t[0], t[1].id))
        return ranked

    def select_parent(self, *, round: int, explore_every: int = 3) -> Node | None:
        """Pick the parent for the next fan-out.

        Every ``explore_every``-th round takes the most NOVEL eligible parent
        instead of the best PN one. Without that the picker still concentrates:
        PN ranks by a product, and a high enough score dominates a mediocre
        novelty term indefinitely. The forced explore round is what actually
        keeps undertested branches alive.
        """
        ranked = self.rank_parents()
        if not ranked:
            return None
        if explore_every > 0 and round > 0 and round % explore_every == 0:
            by_novelty = sorted(
                ((self.novelty_of(n), n) for _, n in ranked),
                key=lambda t: (-t[0], t[1].id),
            )
            pick = by_novelty[0][1]
            logger.info(
                "[archive] R%d EXPLORE — parent %s (novelty %.3f, mean %.3f)",
                round, pick.id, by_novelty[0][0], pick.mean_score or 0.0,
            )
            return pick
        pn, pick = ranked[0]
        logger.info(
            "[archive] R%d EXPLOIT — parent %s (PN %.3f, mean %.3f)",
            round, pick.id, pn, pick.mean_score or 0.0,
        )
        return pick

    def incumbent(self) -> Node | None:
        """Highest MEAN scorer — the config a regression reverts to."""
        pool = [n for n in self.scored() if n.mean_score is not None]
        if not pool:
            return None
        return max(pool, key=lambda n: (n.mean_score, -len(n.scores)))

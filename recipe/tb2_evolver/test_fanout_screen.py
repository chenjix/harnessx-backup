"""Unit tests for the fan-out archive + screens (no GPU, no LLM, no eval)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from recipe.tb2_evolver.population import Archive, Node, cosine_distance, knn_novelty
from recipe.tb2_evolver.screen import (
    Candidate,
    changeset_signature,
    screen_llm_review,
    screen_mini_eval,
    screen_structural,
    select_probe_tasks,
)
from recipe.tb2_evolver.fanout import build_focus_notes

TASKS = ["t1", "t2", "t3", "t4"]


class _Approx:
    """Tiny stand-in for pytest.approx so these run on a bare interpreter."""

    def __init__(self, value: float, tol: float = 1e-9):
        self.value, self.tol = float(value), tol

    def __eq__(self, other) -> bool:
        return abs(float(other) - self.value) <= self.tol

    def __repr__(self) -> str:
        return f"approx({self.value})"


class pytest:  # noqa: N801 - deliberately shadows the name the asserts use
    approx = staticmethod(_Approx)


# ─── novelty ───────────────────────────────────────────────────────────────


def test_cosine_distance_identical_is_zero():
    assert cosine_distance([1, 0, 1], [1, 0, 1]) == pytest.approx(0.0)


def test_cosine_distance_orthogonal_is_one():
    assert cosine_distance([1, 0], [0, 1]) == pytest.approx(1.0)


def test_cosine_distance_all_zero_is_not_novel():
    # An all-zero node solves nothing; treating it as maximally novel would
    # reward the worst possible parent.
    assert cosine_distance([0, 0], [1, 1]) == 0.0


def test_knn_novelty_no_neighbours_is_maximal():
    assert knn_novelty([1, 0], [], k=3) == 1.0


def test_knn_novelty_averages_nearest_only():
    # Two identical neighbours (d=0) and one orthogonal (d=1); k=2 must see 0.0.
    assert knn_novelty([1, 0], [[1, 0], [1, 0], [0, 1]], k=2) == pytest.approx(0.0)


# ─── archive ───────────────────────────────────────────────────────────────


def _archive() -> Archive:
    return Archive({}, task_universe=TASKS)


def test_record_score_accumulates_repeats_for_unbiased_mean():
    a = _archive()
    n = a.add(parent_id=None, config="c.yaml", round=0)
    a.record_score(n.id, 0.5, ["t1"])
    a.record_score(n.id, 0.3, ["t1"])
    got = a.get(n.id)
    assert got.scores == [0.5, 0.3]
    assert got.mean_score == pytest.approx(0.4)


def test_scored_excludes_unmeasured_nodes():
    a = _archive()
    a.add(parent_id=None, config="unmeasured.yaml", round=0)
    scored = a.add(parent_id=None, config="scored.yaml", round=0)
    a.record_score(scored.id, 1.0, TASKS)
    assert [n.id for n in a.scored()] == [scored.id]


def test_incumbent_is_highest_mean_not_luckiest_draw():
    a = _archive()
    lucky = a.add(parent_id=None, config="lucky.yaml", round=0)
    steady = a.add(parent_id=None, config="steady.yaml", round=0)
    # `lucky` peaked at 0.9 once but averages 0.5; `steady` holds 0.7.
    a.record_score(lucky.id, 0.9, ["t1", "t2", "t3"])
    a.record_score(lucky.id, 0.1, ["t1"])
    a.record_score(steady.id, 0.7, ["t1", "t2"])
    assert a.incumbent().id == steady.id


def test_rejected_signatures_only_lists_screened_out():
    a = _archive()
    ok = a.add(parent_id=None, config="a.yaml", round=0, signature="sig-ok")
    bad = a.add(parent_id=None, config="b.yaml", round=0, signature="sig-bad")
    a.record_score(ok.id, 1.0, TASKS)
    a.mark(bad.id, "screened_out")
    assert a.rejected_signatures() == {"sig-bad"}


def test_select_parent_exploits_best_pn_on_normal_rounds():
    a = _archive()
    strong = a.add(parent_id=None, config="strong.yaml", round=0)
    weak = a.add(parent_id=None, config="weak.yaml", round=0)
    a.record_score(strong.id, 0.9, ["t1", "t2"])
    a.record_score(weak.id, 0.1, ["t3"])
    assert a.select_parent(round=1, explore_every=3).id == strong.id


def test_select_parent_explores_on_every_third_round():
    a = _archive()
    # `strong` scores well but shares its solved set with a crowd of siblings;
    # `odd` is mediocre but covers a slice nothing else does.
    strong = a.add(parent_id=None, config="strong.yaml", round=0)
    a.record_score(strong.id, 0.9, ["t1", "t2"])
    for i in range(3):
        clone = a.add(parent_id=None, config=f"clone{i}.yaml", round=0)
        a.record_score(clone.id, 0.85, ["t1", "t2"])
    odd = a.add(parent_id=None, config="odd.yaml", round=0)
    a.record_score(odd.id, 0.4, ["t4"])
    assert a.select_parent(round=3, explore_every=3).id == odd.id


def test_select_parent_returns_none_on_empty_archive():
    assert _archive().select_parent(round=1) is None


def test_node_for_config_reuses_existing_node():
    a = _archive()
    first = a.node_for_config("c.yaml", round=0)
    a.record_score(first.id, 0.5, ["t1"])
    again = a.node_for_config("c.yaml", round=1)
    assert again.id == first.id
    a.record_score(again.id, 0.7, ["t1", "t2"])
    # Re-measurement must append, not fork a duplicate single-draw node.
    assert len(a.nodes()) == 1
    assert a.get(first.id).mean_score == pytest.approx(0.6)


def test_node_for_config_adopts_unknown_config_as_root():
    a = _archive()
    n = a.node_for_config("brand-new.yaml", round=2)
    assert n.parent_id is None and n.round == 2


def test_archive_writes_through_to_state_for_resume():
    state: dict = {}
    a = Archive(state, task_universe=TASKS)
    n = a.add(parent_id=None, config="c.yaml", round=0)
    a.record_score(n.id, 0.5, ["t1"])
    assert state["archive"][n.id]["scores"] == [0.5]
    # A fresh Archive over the same state must see the same node.
    assert Archive(state, task_universe=TASKS).get(n.id).mean_score == 0.5


# ─── screen 1: structural ──────────────────────────────────────────────────


def _cand(idx: int, changeset: dict) -> Candidate:
    c = Candidate(idx=idx, config=Path(f"/tmp/c{idx}.yaml"))
    c.changeset = changeset
    c.signature = changeset_signature(changeset) if changeset else None
    return c


def test_changeset_signature_is_key_order_independent():
    assert changeset_signature({"a": [1], "b": [2]}) == changeset_signature({"b": [2], "a": [1]})


def test_structural_drops_empty_changeset():
    cands = [_cand(0, {}), _cand(1, {"tools_added": ["X"]})]
    alive = screen_structural(cands, archive=_archive())
    assert [c.idx for c in alive] == [1]
    assert cands[0].dropped_by == "structural"


def test_structural_drops_intra_batch_duplicates():
    cands = [_cand(0, {"tools_added": ["X"]}), _cand(1, {"tools_added": ["X"]})]
    alive = screen_structural(cands, archive=_archive())
    assert [c.idx for c in alive] == [0]
    assert "duplicate" in cands[1].drop_reason


def test_structural_drops_already_rejected_signature():
    a = _archive()
    sig = changeset_signature({"tools_added": ["X"]})
    dead = a.add(parent_id=None, config="old.yaml", round=0, signature=sig)
    a.mark(dead.id, "screened_out")
    cands = [_cand(0, {"tools_added": ["X"]})]
    assert screen_structural(cands, archive=a) == []
    assert "already tried" in cands[0].drop_reason


# ─── screen 2: llm review ──────────────────────────────────────────────────


def test_llm_review_keeps_top_ranked():
    cands = [_cand(i, {"tools_added": [f"T{i}"]}) for i in range(3)]

    async def llm(system, user):
        return '{"candidates": [{"idx": 2, "rank_score": 0.9}, {"idx": 0, "rank_score": 0.1}, {"idx": 1, "rank_score": 0.5}]}'

    alive = asyncio.run(screen_llm_review(cands, llm=llm, keep=2))
    assert [c.idx for c in alive] == [2, 1]


def test_llm_review_tolerates_code_fences():
    cands = [_cand(i, {"tools_added": [f"T{i}"]}) for i in range(2)]

    async def llm(system, user):
        return '```json\n{"candidates": [{"idx": 1, "rank_score": 0.9}]}\n```'

    alive = asyncio.run(screen_llm_review(cands, llm=llm, keep=1))
    assert [c.idx for c in alive] == [1]


def test_llm_review_failure_keeps_first_k_not_empty():
    # A broken screen must never empty the round.
    cands = [_cand(i, {"tools_added": [f"T{i}"]}) for i in range(3)]

    async def llm(system, user):
        raise RuntimeError("gateway 500")

    alive = asyncio.run(screen_llm_review(cands, llm=llm, keep=2))
    assert len(alive) == 2


def test_llm_review_garbage_output_keeps_first_k():
    cands = [_cand(i, {"tools_added": [f"T{i}"]}) for i in range(3)]

    async def llm(system, user):
        return "I cannot comply."

    alive = asyncio.run(screen_llm_review(cands, llm=llm, keep=2))
    assert len(alive) == 2


def test_llm_review_noop_when_batch_already_small():
    cands = [_cand(0, {"tools_added": ["X"]})]
    calls = []

    async def llm(system, user):
        calls.append(1)
        return "{}"

    alive = asyncio.run(screen_llm_review(cands, llm=llm, keep=2))
    assert len(alive) == 1 and not calls


# ─── probe selection ───────────────────────────────────────────────────────


def test_probe_set_mixes_solved_and_unsolved():
    parent = {"t1": True, "t2": True, "t3": False, "t4": False}
    probes = select_probe_tasks(parent_results=parent, task_universe=TASKS, n_solved=2, n_unsolved=1)
    assert probes == ["t1", "t2", "t3"]


def test_probe_set_nonempty_when_parent_solved_everything():
    parent = {t: True for t in TASKS}
    probes = select_probe_tasks(parent_results=parent, task_universe=TASKS, n_solved=2, n_unsolved=1)
    assert probes == ["t1", "t2"]


def test_probe_set_nonempty_when_parent_solved_nothing():
    parent = {t: False for t in TASKS}
    probes = select_probe_tasks(parent_results=parent, task_universe=TASKS, n_solved=2, n_unsolved=1)
    assert probes and all(p in TASKS for p in probes)


# ─── screen 3: mini eval ───────────────────────────────────────────────────


PARENT = {"t1": True, "t2": True, "t3": False}
PROBES = ["t1", "t2", "t3"]


def test_mini_eval_drops_regression():
    good = _cand(0, {"tools_added": ["A"]})
    bad = _cand(1, {"tools_added": ["B"]})

    async def run_probe(*, config, tasks, label):
        # cand#1 breaks BOTH parent-solved probe tasks.
        if "c1" in str(config):
            return {"t1": False, "t2": False, "t3": False}
        return {"t1": True, "t2": True, "t3": True}

    alive = asyncio.run(screen_mini_eval(
        [good, bad], run_probe=run_probe, probe_tasks=PROBES,
        parent_results=PARENT, keep=2, max_regressions=1,
    ))
    assert [c.idx for c in alive] == [0]
    assert "broke 2" in bad.drop_reason


def test_mini_eval_tolerates_single_regression_under_threshold():
    c = _cand(0, {"tools_added": ["A"]})

    async def run_probe(*, config, tasks, label):
        return {"t1": True, "t2": False, "t3": True}  # -1 / +1

    alive = asyncio.run(screen_mini_eval(
        [c], run_probe=run_probe, probe_tasks=PROBES,
        parent_results=PARENT, keep=2, max_regressions=1,
    ))
    assert [x.idx for x in alive] == [0]


def test_mini_eval_infra_failure_does_not_condemn_candidate():
    # A crashed probe is not evidence against the edit.
    c = _cand(0, {"tools_added": ["A"]})

    async def run_probe(*, config, tasks, label):
        raise RuntimeError("docker died")

    alive = asyncio.run(screen_mini_eval(
        [c], run_probe=run_probe, probe_tasks=PROBES, parent_results=PARENT, keep=2,
    ))
    assert [x.idx for x in alive] == [0]
    assert c.probe_score is None


def test_mini_eval_ranks_by_probe_score():
    lo, hi = _cand(0, {"tools_added": ["A"]}), _cand(1, {"tools_added": ["B"]})

    async def run_probe(*, config, tasks, label):
        if "c1" in str(config):
            return {"t1": True, "t2": True, "t3": True}
        return {"t1": True, "t2": True, "t3": False}

    alive = asyncio.run(screen_mini_eval(
        [lo, hi], run_probe=run_probe, probe_tasks=PROBES, parent_results=PARENT, keep=1,
    ))
    assert [c.idx for c in alive] == [1]


# ─── focus notes ───────────────────────────────────────────────────────────


def test_focus_notes_are_distinct():
    notes = build_focus_notes(8, parent_results={"t1": True, "t2": False})
    assert len(notes) == 8 and len(set(notes)) == 8


def test_focus_notes_lead_with_observed_failures():
    notes = build_focus_notes(3, parent_results={"t1": False, "t2": True})
    assert "`t1`" in notes[0]


def test_focus_notes_distinct_beyond_lever_x_style_product():
    # 6 levers x 4 styles = 24; ask for more than that and the de-collision
    # fallback has to carry it.
    notes = build_focus_notes(30, parent_results={t: True for t in TASKS})
    assert len(notes) == 30 and len(set(notes)) == 30


def test_focus_notes_fall_back_to_generic_when_no_failures():
    notes = build_focus_notes(3, parent_results={t: True for t in TASKS})
    assert len(notes) == 3 and all("fails" not in n for n in notes)


# ─── runner (no pytest on this box) ────────────────────────────────────────

if __name__ == "__main__":
    import sys, traceback

    tests = sorted(
        (name, obj)
        for name, obj in list(globals().items())
        if name.startswith("test_") and callable(obj)
    )
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed.append(name)
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

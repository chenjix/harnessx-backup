"""Tests for fan-out v2: net-score ranking, complementary merge, stacked focuses.

Run as a script (pytest is not installed in the evolve venv)::

    /fsx/home/jixuan.chen/.venv/bin/python recipe/tb2_evolver/test_fanout_v2.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from recipe.tb2_evolver.fanout_v2 import build_focus_notes_v2, format_pivot_brief, propose_and_screen_v2
from recipe.tb2_evolver.merge import merge_harness_configs
from recipe.tb2_evolver.population import Archive
from recipe.tb2_evolver.screen import Candidate, changeset_signature
from recipe.tb2_evolver.screen_v2 import (
    changesets_complementary,
    pick_complementary_group,
    score_probe,
    screen_mini_eval_v2,
)

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "configs" / "baseline_tmax_harness.yaml"
TASKS = ["t1", "t2", "t3", "t4"]
PARENT_RESULTS = {"t1": True, "t2": True, "t3": False, "t4": False}
PROBES = ["t1", "t2", "t3"]
_REAL_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch"]

_VERIFY_NEEDLE = (
    "- _target_: benchmarks.terminal_bench_2.harness.CustomSelfVerifyProcessor\n"
    "  _hook_: '*'"
)


def _cand(idx: int, changeset: dict, **kw) -> Candidate:
    c = Candidate(idx=idx, config=Path(f"/tmp/c{idx}/config.yaml"), changeset=changeset)
    c.signature = changeset_signature(changeset) if changeset else None
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ─── focus notes ──────────────────────────────────────────────────────────


def test_v2_focus_notes_allow_stacking():
    notes = build_focus_notes_v2(4, parent_results=PARENT_RESULTS)
    assert len(notes) == 4 and len(set(notes)) == 4
    assert all("stack" in n.lower() or "MAY stack" in n for n in notes)
    # First notes still name the failed tasks; one slot is a two-task combo.
    joined = "\n".join(notes)
    assert "`t3`" in notes[0] and "`t4`" in notes[1]
    assert "`t3`" in joined and "`t4`" in joined
    assert any("both fail" in n for n in notes)


def test_v2_focus_notes_combo_when_enough_failures():
    notes = build_focus_notes_v2(3, parent_results={"a": False, "b": False, "c": True})
    assert any("both fail" in n for n in notes)


# ─── complementary detection ─────────────────────────────────────────────


def test_changesets_complementary_disjoint_processors():
    a = {"processors_added": ["LoopDetectionProcessor"]}
    b = {"processors_added": ["StuckResultBreaker"]}
    assert changesets_complementary(a, b)
    assert not changesets_complementary(a, {"processors_added": ["LoopDetectionProcessor"]})
    assert not changesets_complementary(a, {})


def test_pick_complementary_without_probe_gains():
    a = _cand(0, {"processors_added": ["A"]}, probe_gained=[], probe_regressed=[])
    b = _cand(1, {"processors_added": ["B"]}, probe_gained=[], probe_regressed=[])
    group = pick_complementary_group([a, b])
    assert group is not None
    assert {c.idx for c in group} == {0, 1}


def test_pick_gainer_plus_preserver():
    gainer = _cand(
        0, {"processors_added": ["A"]},
        probe_results={"t1": False, "t2": False, "t3": True},
        probe_gained=["t3"], probe_regressed=["t1", "t2"], net_score=0.0,
    )
    preserver = _cand(
        1, {"processors_added": ["B"]},
        probe_results={"t1": True, "t2": True, "t3": False},
        probe_gained=[], probe_regressed=[], net_score=0.0,
    )
    other = _cand(
        2, {"processors_added": ["A"]},  # subset of gainer — not complementary with it
        probe_results={"t1": True, "t2": True, "t3": False},
        probe_gained=[], probe_regressed=[], net_score=0.0,
    )
    group = pick_complementary_group([gainer, preserver, other])
    assert group is not None
    assert {c.idx for c in group} >= {0, 1}


# ─── mini-eval v2: no hard-drop ─────────────────────────────────────────────


def test_v2_mini_eval_keeps_regressor_that_gained():
    good = _cand(0, {"tools_added": ["A"]})
    bad = _cand(1, {"tools_added": ["B"]})

    async def run_probe(*, config, tasks, label):
        if "c1" in str(config):
            return {"t1": False, "t2": False, "t3": True}  # +1 / -2
        return {"t1": True, "t2": True, "t3": False}

    alive = asyncio.run(screen_mini_eval_v2(
        [good, bad], run_probe=run_probe, probe_tasks=PROBES,
        parent_results={"t1": True, "t2": True, "t3": False}, keep=2,
    ))
    assert {c.idx for c in alive} == {0, 1}
    assert bad.probe_gained == ["t3"]
    assert len(bad.probe_regressed) == 2
    assert bad.net_score == 0.0  # 1 - 0.5*2


def test_v2_mini_eval_ranks_by_net_not_raw_probe():
    # +1/-0 (net 1) beats a clean 2/3 preserver (net 0) even if probe_score ties
    # at keep=1.
    gainer = _cand(0, {"tools_added": ["A"]})
    preserver = _cand(1, {"tools_added": ["B"]})

    async def run_probe(*, config, tasks, label):
        if "c0" in str(config):
            return {"t1": True, "t2": True, "t3": True}  # +1/-0
        return {"t1": True, "t2": True, "t3": False}

    alive = asyncio.run(screen_mini_eval_v2(
        [gainer, preserver], run_probe=run_probe, probe_tasks=PROBES,
        parent_results={"t1": True, "t2": True, "t3": False}, keep=1,
    ))
    assert [c.idx for c in alive] == [0]
    assert gainer.net_score == 1.0


def test_score_probe_net_formula():
    c = _cand(0, {"tools_added": ["A"]})
    c.probe_results = {"t1": False, "t2": True, "t3": True}
    score_probe(c, probe_tasks=PROBES, parent_results={"t1": True, "t2": True, "t3": False})
    assert c.probe_gained == ["t3"]
    assert c.probe_regressed == ["t1"]
    assert c.net_score == 0.5  # 1 - 0.5*1


# ─── merge of two file:// processors ────────────────────────────────────────


def _inject_processor(text: str, file_uri: str) -> str:
    if _VERIFY_NEEDLE not in text:
        raise AssertionError("baseline no longer contains CustomSelfVerifyProcessor")
    block = f"- _target_: {file_uri}\n  _hook_: '*'\n{_VERIFY_NEEDLE}"
    return text.replace(_VERIFY_NEEDLE, block, 1)


def _child_with_processor(tmp: Path, name: str, cls: str) -> Path:
    d = tmp / name
    procs = d / "processors"
    procs.mkdir(parents=True)
    py = procs / f"{cls.lower()}.py"
    py.write_text(f"class {cls}:\n    pass\n")
    uri = f"file://{py.resolve()}::{cls}"
    cfg = d / "config.yaml"
    cfg.write_text(_inject_processor(BASELINE.read_text(), uri))
    (d / "system_prompt.txt").write_text(f"prompt from {name}\n")
    return cfg


def test_merge_unions_processors_and_rewrites_file_uris():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parent = tmp / "parent.yaml"
        shutil.copy(BASELINE, parent)
        (tmp / "system_prompt.txt").write_text("PARENT PROMPT\n")
        c0 = _child_with_processor(tmp, "c0", "ProcA")
        c1 = _child_with_processor(tmp, "c1", "ProcB")
        out = tmp / "m0"
        cfg = merge_harness_configs(parent, [c0, c1], out, source_idxs=[0, 1])
        text = cfg.read_text()
        assert "ProcA" in text and "ProcB" in text
        assert str(out.resolve()) in text
        assert str(c0.parent.resolve()) not in text
        assert str(c1.parent.resolve()) not in text
        assert (out / "processors" / "proca.py").is_file()
        assert (out / "processors" / "procb.py").is_file()
        # Gainer (first child) wins the prompt.
        assert (out / "system_prompt.txt").read_text() == "prompt from c0\n"
        assert (out / "merge.json").is_file()


def test_merge_does_not_apply_unilateral_removals():
    """Union starts from the parent list, so a child that dropped a processor
    cannot delete it unless the other children also lack it — and we never
    subtract, we only add. Parent processors always survive."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parent = tmp / "parent.yaml"
        shutil.copy(BASELINE, parent)
        c0 = _child_with_processor(tmp, "c0", "ProcA")
        # Child 1 is parent-identical plus a different processor.
        c1 = _child_with_processor(tmp, "c1", "ProcB")
        out = tmp / "m0"
        text = merge_harness_configs(parent, [c0, c1], out).read_text()
        assert "CustomSelfVerifyProcessor" in text
        assert "CompactionProcessor" in text


# ─── e2e propose_and_screen_v2 ─────────────────────────────────────────────


class FakeMetaAgent:
    def __init__(self, plan: dict[int, str] | None = None):
        self.plan = plan or {}
        self.seen_focuses: list[str] = []
        self.calls = 0
        self.stack_edits: list[bool] = []

    async def evolve(self, *, current_config, trajectories_dir, output_dir, focus_note=None, **kw):
        idx = self.calls
        self.calls += 1
        self.seen_focuses.append(focus_note or "")
        self.stack_edits.append(bool(kw.get("stack_edits")))
        out = Path(output_dir) / "config.yaml"
        text = Path(current_config).read_text()
        mode = self.plan.get(idx, _REAL_TOOLS[idx % len(_REAL_TOOLS)])
        if mode == "noop":
            pass
        else:
            text = text.replace(
                "  builtin:\n  - Bash",
                f"  builtin:\n  - Bash\n  - {mode}",
            )
        out.write_text(text)
        return out


def _run_v2(**kw):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parent_cfg = tmp / "parent.yaml"
        shutil.copy(BASELINE, parent_cfg)
        archive = Archive({}, task_universe=TASKS)
        parent = archive.node_for_config(str(parent_cfg), round=0)
        archive.record_score(parent.id, 0.5, ["t1", "t2"])
        defaults = dict(
            meta_agent=kw.pop("meta_agent"),
            archive=archive,
            parent=parent,
            parent_config=parent_cfg,
            parent_results=PARENT_RESULTS,
            trajectories_dir=tmp,
            round_dir=tmp / "R1",
            task_universe=TASKS,
            llm=None,
            run_probe=None,
            n=4,
            keep=2,
        )
        defaults.update(kw)
        pivots = defaults.get("pivots")
        if pivots:
            for p in pivots:
                p["config"] = str(parent_cfg)
                p.setdefault("trajectories", str(tmp))
                p.setdefault("parent_results", dict(PARENT_RESULTS))
            defaults["pivots"] = pivots
        survivors = asyncio.run(propose_and_screen_v2(**defaults))
        return survivors, archive


def test_v2_synthesis_slot_when_multiple_pivots():
    agent = FakeMetaAgent()
    _run_v2(
        meta_agent=agent, n=3, keep=3, merge=False,
        pivots=[
            {"id": "n0", "score": 0.6, "unique_solves": ["t3"], "unique_losses": []},
            {"id": "n1", "score": 0.6, "unique_solves": ["t4"], "unique_losses": ["t1"]},
        ],
    )
    assert any("Synthesize" in f for f in agent.seen_focuses)
    assert agent.calls == 3


def test_v2_passes_stack_edits_to_evolve():
    agent = FakeMetaAgent()
    _run_v2(meta_agent=agent, n=3, keep=3)
    assert agent.stack_edits and all(agent.stack_edits)


def test_v2_noop_still_dropped_structurally():
    agent = FakeMetaAgent({0: "noop"})
    survivors, archive = _run_v2(meta_agent=agent, n=4, keep=4)
    assert 0 not in {c.idx for c in survivors}
    dropped = [n for n in archive.nodes() if n.status == "screened_out"]
    assert any(n.screen.get("dropped_by") == "structural" for n in dropped)


def test_v2_does_not_hard_drop_a_gaining_regressor():
    agent = FakeMetaAgent()

    async def run_probe(*, config, tasks, label):
        if "/c0/" in str(config):
            return {"t1": False, "t2": False, "t3": True}
        return {"t1": True, "t2": True, "t3": False}

    survivors, archive = _run_v2(
        meta_agent=agent, n=4, keep=4, run_probe=run_probe, merge=False,
    )
    assert 0 in {c.idx for c in survivors}
    # Nobody should have been dropped for "broke N parent-solved".
    for n in archive.nodes():
        reason = str(n.screen.get("reason") or "")
        assert "broke " not in reason


def test_v2_llm_ranks_but_does_not_cut_before_probe():
    agent = FakeMetaAgent()
    probed: list[str] = []

    async def llm(system, user):
        assert "NET" in system
        return '{"candidates": [{"idx": 3, "rank_score": 0.9}, {"idx": 0, "rank_score": 0.1}]}'

    async def run_probe(*, config, tasks, label):
        probed.append(label)
        return {t: True for t in tasks}

    survivors, _ = _run_v2(
        meta_agent=agent, n=4, keep=2, llm=llm, run_probe=run_probe, merge=False,
    )
    # All 4 structurally-valid candidates must have been probed (v1 would
    # have cut to the LLM's top 2 first).
    assert len(probed) == 4
    assert {c.idx for c in survivors} <= {0, 1, 2, 3}


def test_v2_merges_gainer_and_preserver():
    agent = FakeMetaAgent()

    async def run_probe(*, config, tasks, label):
        p = str(config)
        if "/m" in p:
            return {"t1": True, "t2": True, "t3": True}
        if "/c0/" in p:
            return {"t1": False, "t2": False, "t3": True}  # +1/-2, often cut
        return {"t1": True, "t2": True, "t3": False}

    survivors, archive = _run_v2(
        meta_agent=agent, n=4, keep=1, run_probe=run_probe, merge=True,
    )
    merged = [c for c in survivors if c.merged_from]
    assert merged, "expected a complementary merge in the survivor list"
    assert 0 in merged[0].merged_from, "gainer c0 should be in the merge"
    assert len(merged[0].merged_from) >= 2
    added = set(merged[0].changeset.get("tools_added") or [])
    assert "Read" in added  # c0's tool
    assert added & {"Write", "Edit", "Glob"}


def test_v2_subset_pair_is_not_merged_when_identical_additive():
    # Two candidates with the SAME tool are duplicates at structural; only one
    # lives. A pair where one is a subset should not produce a merge.
    a = _cand(0, {"tools_added": ["Read", "Write"]}, probe_results={"t1": True}, probe_gained=[], probe_regressed=[])
    b = _cand(1, {"tools_added": ["Read"]}, probe_results={"t1": True}, probe_gained=[], probe_regressed=[])
    assert pick_complementary_group([a, b]) is None


def test_format_pivot_brief_lists_unique_solves():
    text = format_pivot_brief([
        {"id": "n0", "score": 0.6, "config": "/tmp/a.yaml", "trajectories": "/tmp/t0",
         "unique_solves": ["t3"], "unique_losses": []},
        {"id": "n1", "score": 0.6, "config": "/tmp/b.yaml", "trajectories": "/tmp/t1",
         "unique_solves": ["t4"], "unique_losses": ["t1"]},
    ])
    assert "n0" in text and "n1" in text
    assert "t3" in text and "t4" in text


def test_v2_merges_complementary_even_without_probe_gain():
    agent = FakeMetaAgent()

    async def run_probe(*, config, tasks, label):
        return {t: False for t in tasks}  # +0 gained for everyone

    survivors, _ = _run_v2(
        meta_agent=agent, n=4, keep=1, run_probe=run_probe, merge=True,
    )
    merged = [c for c in survivors if c.merged_from]
    assert merged, "complementary changesets should merge even with no probe gain"


# ─── runner ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    tests = sorted(
        (n, o) for n, o in list(globals().items()) if n.startswith("test_") and callable(o)
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

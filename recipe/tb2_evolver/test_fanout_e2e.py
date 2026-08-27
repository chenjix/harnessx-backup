"""End-to-end test of propose_and_screen with fake agent / llm / probe runner.

Uses REAL HarnessConfig YAML on disk (derived from the TB2 baseline) so the
changeset + signature path is exercised for real; only the three expensive
seams — meta-agent, LLM, benchmark runner — are faked.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from recipe.tb2_evolver.fanout import propose_and_screen
from recipe.tb2_evolver.population import Archive

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "configs" / "baseline_tmax_harness.yaml"
TASKS = ["t1", "t2", "t3", "t4"]
# Real builtin tool names — an unknown name may not survive config load, which
# would make the candidate diff to an empty changeset for the wrong reason.
_REAL_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch"]
PARENT_RESULTS = {"t1": True, "t2": True, "t3": False, "t4": False}


class FakeMetaAgent:
    """Writes a config per proposal, varying the tool list so the candidates
    have genuinely different changesets — except the pair we force to collide."""

    def __init__(self, plan: dict[int, str]):
        self.plan = plan
        self.seen_focuses: list[str] = []
        self.calls = 0

    async def evolve(self, *, current_config, trajectories_dir, output_dir, focus_note=None):
        idx = self.calls
        self.calls += 1
        self.seen_focuses.append(focus_note or "")
        out = Path(output_dir) / "config.yaml"
        text = Path(current_config).read_text()
        mode = self.plan.get(idx, _REAL_TOOLS[idx % len(_REAL_TOOLS)])
        if mode == "noop":
            pass  # byte-identical to the parent -> empty changeset
        elif mode == "broken":
            text = "{{{ not yaml"
        else:
            # Add a distinct builtin tool so compute_changeset sees tools_added.
            text = text.replace(
                "  builtin:\n  - Bash",
                f"  builtin:\n  - Bash\n  - {mode}",
            )
        out.write_text(text)
        return out


def _run(**kw):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parent_cfg = tmp / "parent.yaml"
        shutil.copy(BASELINE, parent_cfg)
        state: dict = {}
        archive = Archive(state, task_universe=TASKS)
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
        survivors = asyncio.run(propose_and_screen(**defaults))
        return survivors, archive, state


# ─── tests ─────────────────────────────────────────────────────────────────


def test_distinct_proposals_all_survive_structural():
    agent = FakeMetaAgent({})
    survivors, archive, _ = _run(meta_agent=agent, n=4, keep=4)
    assert agent.calls == 4
    assert len(survivors) == 4
    assert len({c.signature for c in survivors}) == 4


def test_each_proposal_gets_a_distinct_focus():
    agent = FakeMetaAgent({})
    _run(meta_agent=agent, n=4, keep=4)
    assert len(set(agent.seen_focuses)) == 4
    # Parent failed t3 and t4, so the first two focuses must name them.
    assert "`t3`" in agent.seen_focuses[0] and "`t4`" in agent.seen_focuses[1]


def test_noop_and_broken_proposals_are_screened_out():
    agent = FakeMetaAgent({0: "noop", 1: "broken"})
    survivors, archive, _ = _run(meta_agent=agent, n=4, keep=4)
    assert {c.idx for c in survivors} == {2, 3}
    dropped = [n for n in archive.nodes() if n.status == "screened_out"]
    assert len(dropped) == 2
    assert all(n.screen.get("dropped_by") == "structural" for n in dropped)


def test_duplicate_proposals_collapse_to_one():
    # Two proposals adding the SAME tool are one experiment, not two.
    agent = FakeMetaAgent({0: "Dup", 1: "Dup"})
    survivors, _, _ = _run(meta_agent=agent, n=4, keep=4)
    assert len(survivors) == 3


def test_previously_rejected_signature_is_not_re_evaluated():
    agent = FakeMetaAgent({})
    # First round establishes what the signatures look like...
    survivors, archive, state = _run(meta_agent=agent, n=4, keep=4)
    banned = survivors[0].signature
    # ...now replay with that signature already marked screened_out.
    agent2 = FakeMetaAgent({})
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parent_cfg = tmp / "parent.yaml"
        shutil.copy(BASELINE, parent_cfg)
        st: dict = {}
        arch = Archive(st, task_universe=TASKS)
        parent = arch.node_for_config(str(parent_cfg), round=0)
        arch.record_score(parent.id, 0.5, ["t1", "t2"])
        dead = arch.add(parent_id=None, config="old.yaml", round=0, signature=banned)
        arch.mark(dead.id, "screened_out")
        out = asyncio.run(propose_and_screen(
            meta_agent=agent2, archive=arch, parent=parent, parent_config=parent_cfg,
            parent_results=PARENT_RESULTS, trajectories_dir=tmp, round_dir=tmp / "R1",
            task_universe=TASKS, llm=None, run_probe=None, n=4, keep=4,
        ))
    assert banned not in {c.signature for c in out}


def test_probe_screen_drops_the_regressor():
    agent = FakeMetaAgent({})

    async def run_probe(*, config, tasks, label):
        # c0 breaks both parent-solved probe tasks; everyone else is clean.
        if "/c0/" in str(config):
            return {"t1": False, "t2": False, "t3": False}
        return {"t1": True, "t2": True, "t3": True}

    survivors, archive, _ = _run(meta_agent=agent, n=4, keep=3, run_probe=run_probe)
    assert 0 not in {c.idx for c in survivors}
    c0 = [n for n in archive.nodes() if n.screen.get("dropped_by") == "mini_eval"]
    assert c0 and "broke 2" in c0[0].screen["reason"]


def test_llm_screen_narrows_before_probing():
    agent = FakeMetaAgent({})
    probed: list[str] = []

    async def llm(system, user):
        return '{"candidates": [{"idx": 3, "rank_score": 0.9}, {"idx": 2, "rank_score": 0.8}, {"idx": 1, "rank_score": 0.2}, {"idx": 0, "rank_score": 0.1}]}'

    async def run_probe(*, config, tasks, label):
        probed.append(label)
        return {t: True for t in tasks}

    survivors, _, _ = _run(
        meta_agent=agent, n=4, keep=2, llm=llm, run_probe=run_probe, llm_keep=2
    )
    # Only the LLM's top 2 should have cost a probe run.
    assert sorted(probed) == ["probe-c2", "probe-c3"]
    assert {c.idx for c in survivors} == {2, 3}


def test_all_proposals_are_archived_with_verdicts():
    agent = FakeMetaAgent({0: "noop"})
    survivors, archive, state = _run(meta_agent=agent, n=4, keep=2)
    # 1 parent + 4 proposals, and the archive round-trips through state.
    assert len(archive.nodes()) == 5
    assert len(state["archive"]) == 5
    kept = [n for n in archive.nodes() if n.status == "pending" and n.parent_id]
    assert len(kept) == len(survivors)


def test_total_generation_failure_returns_empty_not_crash():
    class DeadAgent:
        calls = 0

        async def evolve(self, **kw):
            raise RuntimeError("meta-agent down")

    survivors, _, _ = _run(meta_agent=DeadAgent(), n=3, keep=2)
    assert survivors == []


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

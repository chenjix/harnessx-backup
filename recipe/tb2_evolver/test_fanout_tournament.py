"""Tournament fan-out: every non-error harness reaches full eval."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from recipe.tb2_evolver.fanout_tournament import propose_tournament
from recipe.tb2_evolver.population import Archive
from recipe.tb2_evolver.run import _full_eval_survivors
from recipe.tb2_evolver.screen import Candidate

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "configs" / "baseline_tmax_harness.yaml"
TASKS = ["t1", "t2", "t3", "t4"]
_REAL_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
PARENT_RESULTS = {"t1": True, "t2": True, "t3": False, "t4": False}


class FakeMetaAgent:
    def __init__(self, plan: dict[int, str]):
        self.plan = plan
        self.seen_focuses: list[str] = []
        self.calls = 0

    async def evolve(self, *, current_config, trajectories_dir, output_dir, focus_note=None, **_kw):
        idx = self.calls
        self.calls += 1
        self.seen_focuses.append(focus_note or "")
        out = Path(output_dir) / "config.yaml"
        text = Path(current_config).read_text()
        mode = self.plan.get(idx, _REAL_TOOLS[idx % len(_REAL_TOOLS)])
        if mode == "noop":
            pass
        elif mode == "broken":
            text = "{{{ not yaml"
        else:
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
            n=5,
            max_concurrent=5,
        )
        defaults.update(kw)
        survivors = asyncio.run(propose_tournament(**defaults))
        return survivors, archive, state


def test_five_distinct_all_reach_full_eval():
    agent = FakeMetaAgent({})
    survivors, archive, _ = _run(meta_agent=agent, n=5)
    assert agent.calls == 5
    assert len(survivors) == 5
    assert len({c.signature for c in survivors}) == 5
    pending = [n for n in archive.nodes() if n.status == "pending"]
    assert len(pending) == 5


def test_each_proposal_gets_a_distinct_focus():
    agent = FakeMetaAgent({})
    _run(meta_agent=agent, n=5)
    assert len(set(agent.seen_focuses)) == 5
    assert "`t3`" in agent.seen_focuses[0] and "`t4`" in agent.seen_focuses[1]


def test_system_error_and_noop_are_dropped_others_kept():
    agent = FakeMetaAgent({0: "noop", 1: "broken"})
    survivors, archive, _ = _run(meta_agent=agent, n=5)
    assert {c.idx for c in survivors} == {2, 3, 4}
    dropped = [n for n in archive.nodes() if n.status == "screened_out"]
    assert len(dropped) == 2


def test_no_keep_slice_even_when_keep_would_have_been_1():
    """Tournament ignores keep: a 5-wide batch with 4 valid harnesses evals all 4."""
    agent = FakeMetaAgent({0: "noop"})
    survivors, _, _ = _run(meta_agent=agent, n=5)
    assert len(survivors) == 4


def test_llm_and_probe_kwargs_are_ignored():
    agent = FakeMetaAgent({})

    async def boom_llm(*_a, **_k):
        raise AssertionError("tournament must not call the LLM screen")

    async def boom_probe(**_k):
        raise AssertionError("tournament must not probe")

    survivors, _, _ = _run(
        meta_agent=agent,
        llm=boom_llm,
        run_probe=boom_probe,
        keep=1,
    )
    assert len(survivors) == 5


def test_full_eval_picks_highest_score():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state: dict = {}
        archive = Archive(state, task_universe=TASKS)
        parent = archive.node_for_config(str(tmp / "p.yaml"), round=0)
        cands = []
        for i, n_pass in enumerate([2, 4, 1, 3, 2]):
            cfg = tmp / f"c{i}.yaml"
            shutil.copy(BASELINE, cfg)
            cand = Candidate(idx=i, config=cfg)
            cand.node = archive.add(
                parent_id=parent.id, config=str(cfg), round=1, status="pending",
            )
            cand._n_pass = n_pass  # type: ignore[attr-defined]
            cands.append(cand)

        class Adapter:
            async def resolve_trajectories_for_round(self, **kw):
                idx = int(str(kw["job_suffix"]).split("c")[-1].split("-")[0])
                n_pass = cands[idx]._n_pass
                traj = tmp / f"traj-c{idx}"
                traj.mkdir()
                for t in TASKS[:n_pass]:
                    d = traj / t
                    d.mkdir()
                    (d / "result.json").write_text(
                        '{"task_name": "%s", "verifier_result": {"rewards": {"reward": 1}}}' % t
                    )
                for t in TASKS[n_pass:]:
                    d = traj / t
                    d.mkdir()
                    (d / "result.json").write_text(
                        '{"task_name": "%s", "verifier_result": {"rewards": {"reward": 0}}}' % t
                    )
                return traj

        winner, kept = asyncio.run(
            _full_eval_survivors(
                survivors=cands,
                archive=archive,
                adapter=Adapter(),
                run_root=tmp / "run",
                input_round=0,
                task_names=TASKS,
                force_eval=True,
                job_suffix_traj=True,
            )
        )
        assert winner.idx == 1
        assert winner.full_score == 1.0
        assert winner.trajectories_dir is not None
        assert winner.trajectories_dir.name == "traj-c1"
        # job_suffix_traj: Adapter parsed fe-cN-traj
        assert kept[0].idx == 1


def test_sft_discover_picks_up_fe_c_traj_dirs():
    src = REPO / "recipe" / "tb2_sft" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import build_tmax_evolve_sft as T  # noqa: E402

    with tempfile.TemporaryDirectory() as td:
        bench = Path(td)
        tag = "tmax-coev-rep19-i1"
        (bench / f"{tag}-r0-traj").mkdir()
        (bench / f"{tag}-r0-fe-c0-traj").mkdir()
        (bench / f"{tag}-r0-fe-c1-traj").mkdir()
        (bench / f"{tag}-r1-fe-c3-traj").mkdir()
        found = T._discover_traj_dirs(run_tags=[tag], traj_dirs=[], bench_root=bench)
        names = {p.name for p in found}
        assert f"{tag}-r0-traj" in names
        assert f"{tag}-r0-fe-c0-traj" in names
        assert f"{tag}-r0-fe-c1-traj" in names
        assert f"{tag}-r1-fe-c3-traj" in names
        assert len(found) == 4

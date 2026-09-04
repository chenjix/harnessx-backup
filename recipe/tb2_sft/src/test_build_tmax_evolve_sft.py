#!/usr/bin/env python3
"""Unit tests for coevolve SFT corpus construction. Run: python this_file.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import build_tmax_evolve_sft as T  # noqa: E402
import filter_and_build_sft as F  # noqa: E402
import plan_tmax_sftgen as P  # noqa: E402


def _meta(*, task: str, run: str, n_tools: int, quality: float = 0.0) -> F.TrialMeta:
    return F.TrialMeta(
        run=run,
        trial_dir=f"/tmp/{run}/{task}",
        task=task,
        reward=1.0,
        model="test",
        n_tool_turns=n_tools,
        n_recovered_tools=0,
        n_steps=n_tools,
        source_bucket="tmax_evolve",
        quality_score=quality,
        has_structured_tools=True,
        exception=False,
    )


def _write_harness(d: Path, yaml_body: str, prompt: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "config.yaml"
    cfg.write_text(yaml_body, encoding="utf-8")
    (d / "system_prompt.txt").write_text(prompt, encoding="utf-8")
    return cfg


def test_winner_only_keeps_matching_fe_c_via_sidecar():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        winner = _write_harness(root / "winner", "processors: [a]\n", "WINNER PROMPT")
        loser = _write_harness(root / "loser", "processors: [b]\n", "LOSER PROMPT")
        keep = root / "tmax-coev-rep19-i2-r0-fe-c1-traj"
        drop = root / "tmax-coev-rep19-i2-r0-fe-c0-traj"
        keep.mkdir()
        drop.mkdir()
        (keep / "harness_config.yaml").write_text("processors: [a]\n", encoding="utf-8")
        (keep / "system_prompt.txt").write_text("WINNER PROMPT", encoding="utf-8")
        (drop / "harness_config.yaml").write_text("processors: [b]\n", encoding="utf-8")
        (drop / "system_prompt.txt").write_text("LOSER PROMPT", encoding="utf-8")
        kept, dropped = T.filter_traj_dirs_to_harness(
            [keep, drop], eval_harness=winner, runs_root=root / "runs"
        )
        assert kept == [keep], kept
        assert dropped == [drop], dropped
        # loser yaml must not match winner
        assert T._fingerprint_harness(loser) != T._fingerprint_harness(winner)


def test_winner_only_heuristic_and_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tag = "tmax-coev-rep19-i2"
        c1 = _write_harness(
            root / "runs" / tag / "_meta_v2" / "R1" / "c1",
            "id: c1\n",
            "c1 prompt",
        )
        _write_harness(
            root / "runs" / tag / "_meta_v2" / "R1" / "c0",
            "id: c0\n",
            "c0 prompt",
        )
        eval_h = _write_harness(root / "eval", "id: c1\n", "c1 prompt")
        win = root / f"{tag}-r0-fe-c1-traj"
        lose = root / f"{tag}-r0-fe-c0-traj"
        unmatched = root / "random-other-traj"
        win.mkdir()
        lose.mkdir()
        unmatched.mkdir()
        kept, dropped = T.filter_traj_dirs_to_harness(
            [win, lose, unmatched], eval_harness=eval_h, runs_root=root / "runs"
        )
        assert kept == [win], kept
        assert lose in dropped and unmatched in dropped
        assert c1.is_file()


def test_legacy_discover_keeps_all_fe_c_dirs():
    with tempfile.TemporaryDirectory() as td:
        bench = Path(td)
        tag = "tmax-coev-rep19-i1"
        (bench / f"{tag}-r0-traj").mkdir()
        (bench / f"{tag}-r0-fe-c0-traj").mkdir()
        (bench / f"{tag}-r0-fe-c1-traj").mkdir()
        found = T._discover_traj_dirs(run_tags=[tag], traj_dirs=[], bench_root=bench)
        names = {p.name for p in found}
        assert f"{tag}-r0-traj" in names
        assert f"{tag}-r0-fe-c0-traj" in names
        assert f"{tag}-r0-fe-c1-traj" in names
        assert len(found) == 3


def test_system_prompt_prepended_and_replaces_existing():
    msgs = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok", "tool_calls": []},
    ]
    out = T.inject_system_prompt(msgs, "  NEW SYS  \n")
    assert out[0] == {"role": "system", "content": "NEW SYS"}
    assert [m["role"] for m in out] == ["system", "user", "assistant"]
    empty = T.inject_system_prompt(msgs, "  ")
    assert empty[0]["content"] == "old"


def test_build_pairs_puts_system_in_prompt():
    meta = _meta(task="t1", run="tmax-coev-rep19-i2-r0-fe-c1-traj", n_tools=1)
    messages = [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "running",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": "{\"command\": \"ls\"}"},
                }
            ],
        },
    ]
    pairs = T.build_pairs(
        [(meta, messages)], max_pairs_per_traj=32, system_text="EVAL SYSTEM"
    )
    assert pairs, "expected at least one pair"
    prompt = pairs[0]["prompt"]
    assert prompt[0]["role"] == "system"
    assert prompt[0]["content"] == "EVAL SYSTEM"


def test_quality_prefers_compact_fast_successes():
    compact = _meta(task="a", run="r", n_tools=12)
    loopy = _meta(task="b", run="r", n_tools=45)
    assert T._evolve_sft_quality_score(compact) > T._evolve_sft_quality_score(loopy)
    trivial = _meta(task="c", run="r", n_tools=2)
    assert T._evolve_sft_quality_score(compact) > T._evolve_sft_quality_score(trivial)
    same = _meta(task="d", run="r", n_tools=16)
    fast = T._evolve_sft_quality_score(same, 30.0)
    slow = T._evolve_sft_quality_score(same, 500.0)
    assert fast > slow


def test_max_prev_frac_caps_old_but_not_when_current_empty():
    current = [
        (_meta(task=f"c{i}", run="tmax-coev-i2-r0-traj", n_tools=20, quality=2.0 - i * 0.01), [])
        for i in range(3)
    ]
    old = [
        (_meta(task=f"o{i}", run="tmax-coev-i1-r0-traj", n_tools=20, quality=1.0 - i * 0.01), [])
        for i in range(10)
    ]
    selected = T.select_trajs(
        current + old,
        max_trajs=0,
        seed=0,
        prefer_runs=("tmax-coev-i2",),
        max_prev_frac=0.4,
    )
    n_old = sum(1 for m, _ in selected if m.run.startswith("tmax-coev-i1"))
    n_cur = sum(1 for m, _ in selected if m.run.startswith("tmax-coev-i2"))
    assert n_cur == 3
    assert n_old == 2  # 0.4/0.6 * 3 = 2
    assert n_old / len(selected) <= 0.4 + 1e-9

    only_old = T.select_trajs(
        old,
        max_trajs=0,
        seed=0,
        prefer_runs=("tmax-coev-i2",),
        max_prev_frac=0.4,
    )
    assert len(only_old) == 10


def test_resolve_sft_system_prompt_uses_sibling():
    with tempfile.TemporaryDirectory() as td:
        cfg = _write_harness(Path(td) / "h", "x: 1\n", "sibling text")
        assert T.resolve_sft_system_prompt(cfg).strip() == "sibling text"


def test_sftgen_plan_reuses_evolve_not_sftgen_dir():
    with tempfile.TemporaryDirectory() as td:
        bench = Path(td)
        tag = "tmax-coev-rep22-i1"
        evol = bench / f"{tag}-r0-traj"
        evol.mkdir()
        (evol / "task_aaa.result.json").write_text("{}", encoding="utf-8")
        (evol / "task_bbb.result.json").write_text("{}", encoding="utf-8")
        sft = bench / f"{tag}-sftgen-r0-traj"
        sft.mkdir()
        (sft / "task_ccc.result.json").write_text("{}", encoding="utf-8")
        fe = bench / f"{tag}-r0-fe-c1-traj"
        fe.mkdir()
        (fe / "task_aaa.result.json").write_text("{}", encoding="utf-8")
        ids = P.unique_task_ids(bench_root=bench, run_tag=tag)
        assert ids == {"task_aaa", "task_bbb"}, ids
        payload = P.plan(bench_root=bench, run_tag=tag, target_size=100)
        assert payload["n_reuse"] == 2
        assert payload["n_new"] == 98
        evolve_list = Path(td) / "evolve.json"
        evolve_list.write_text('["task_aaa", "task_bbb", "task_ddd"]\n', encoding="utf-8")
        payload2 = P.plan(
            bench_root=bench, run_tag=tag, target_size=100, evolve_tasks=evolve_list
        )
        assert payload2["n_reuse"] == 3
        assert payload2["n_new"] == 97


def main() -> int:
    tests = [
        test_winner_only_keeps_matching_fe_c_via_sidecar,
        test_winner_only_heuristic_and_fail_closed,
        test_legacy_discover_keeps_all_fe_c_dirs,
        test_system_prompt_prepended_and_replaces_existing,
        test_build_pairs_puts_system_in_prompt,
        test_quality_prefers_compact_fast_successes,
        test_max_prev_frac_caps_old_but_not_when_current_empty,
        test_resolve_sft_system_prompt_uses_sibling,
        test_sftgen_plan_reuses_evolve_not_sftgen_dir,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
    if failed:
        print(f"{failed}/{len(tests)} failed")
        return 1
    print(f"{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

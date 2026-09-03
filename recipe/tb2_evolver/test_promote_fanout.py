"""Regression test: a fan-out winner's sidecars must be promoted from ITS dir.

`promote_round_output` copies system_prompt.txt / processors/ / tools/ /
templates/ out of `rx_output_dir`. Under fan-out the winner's config lives in
`R{n}/cN/`, so passing the round ROOT as rx_output_dir silently drops every
sidecar edit — including system-prompt edits, one of the meta-agent's main
levers. The config still promotes, so the loss is invisible: the run keeps
going with the prompt reset to the seed.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from recipe.tb2_evolver.tmax_adapter import TmaxRoundAdapter

REPO = Path(__file__).resolve().parents[2]
BASELINE = REPO / "configs" / "baseline_tmax_harness.yaml"


def _adapter(tmp: Path) -> TmaxRoundAdapter:
    envs = tmp / "envs.jsonl"
    envs.write_text('{"task_id": "t1"}\n')
    tasks = tmp / "tasks.json"
    tasks.write_text('["t1"]')
    cfg = tmp / "baseline.yaml"
    shutil.copy(BASELINE, cfg)
    return TmaxRoundAdapter(
        baseline_config=cfg, task_names=["t1"], envs_jsonl=envs,
        tasks_json=tasks, jobs_dir=tmp / "jobs",
    )


def _winner_layout(tmp: Path) -> tuple[Path, Path]:
    """Build `R1/c3/{config.yaml,system_prompt.txt,processors/}` and return
    (winner_dir, winner_config)."""
    win = tmp / "evolve" / "R1" / "c3"
    win.mkdir(parents=True)
    shutil.copy(BASELINE, win / "config.yaml")
    (win / "system_prompt.txt").write_text("EVOLVED PROMPT — the winner's edit\n")
    procs = win / "processors"
    procs.mkdir()
    (procs / "my_proc.py").write_text("# evolved processor\n")
    return win, win / "config.yaml"


def test_promoting_from_winner_dir_carries_sidecars():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        adapter = _adapter(tmp)
        win, cfg = _winner_layout(tmp)
        run_root = tmp / "run"
        promoted = adapter.promote_round_output(
            run_root=run_root, output_round=1, rx_output_dir=win, output_config=cfg,
        )
        prompt = Path(promoted).parent / "system_prompt.txt"
        assert prompt.is_file(), "system_prompt.txt was not promoted"
        assert "the winner's edit" in prompt.read_text()
        assert (run_root / "R1" / "evolve" / "processors" / "my_proc.py").is_file(), \
            "processors/ was not promoted"


def test_copy_config_as_round_carries_sibling_prompt():
    from recipe.tb2_evolver.run import _copy_config_as_round

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_dir = tmp / "incumbent"
        src_dir.mkdir()
        shutil.copy(BASELINE, src_dir / "config.yaml")
        (src_dir / "system_prompt.txt").write_text("CHAINED PROMPT\n")
        run_root = tmp / "run"
        promoted = _copy_config_as_round(
            run_root=run_root, output_round=3, config=src_dir / "config.yaml"
        )
        assert (Path(promoted).parent / "system_prompt.txt").read_text() == "CHAINED PROMPT\n"
        assert (run_root / "R3" / "evolve" / "system_prompt.txt").read_text() == "CHAINED PROMPT\n"


def test_materialize_bundle_copies_sibling_prompt():
    from recipe.tb2_evolver.run import _materialize_config_bundle

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_dir = tmp / "prev"
        src_dir.mkdir()
        shutil.copy(BASELINE, src_dir / "config.yaml")
        (src_dir / "system_prompt.txt").write_text("SEEDED FROM PREVIOUS ITER\n")
        dest = tmp / "R0" / "config.yaml"
        copied = _materialize_config_bundle(src_dir / "config.yaml", dest, tmp / "R0" / "assets")
        assert "system_prompt.txt" in copied
        assert (dest.parent / "system_prompt.txt").read_text() == "SEEDED FROM PREVIOUS ITER\n"


def test_promoting_from_round_root_loses_the_prompt_edit():
    # Documents the bug the fix prevents: same winner, wrong rx_output_dir.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        adapter = _adapter(tmp)
        win, cfg = _winner_layout(tmp)
        round_root = win.parent  # R1/ — has no sidecars of its own
        run_root = tmp / "run"
        promoted = adapter.promote_round_output(
            run_root=run_root, output_round=1, rx_output_dir=round_root, output_config=cfg,
        )
        prompt = Path(promoted).parent / "system_prompt.txt"
        assert "the winner's edit" not in (prompt.read_text() if prompt.is_file() else "")
        assert not (run_root / "R1" / "evolve" / "processors").exists()


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

import json
from pathlib import Path

from recipe.tmax_eval.paired_gate import bundle_fingerprint, paired_report


def test_bundle_fingerprint_ignores_bundle_location(tmp_path: Path):
    hashes = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        processor = root / "guard.py"
        processor.write_text("class Guard: pass\n")
        (root / "system_prompt.txt").write_text("same prompt\n")
        config = root / "config.yaml"
        config.write_text(f"_target_: file://{processor}::Guard\n")
        hashes.append(bundle_fingerprint(config))
    assert hashes[0] == hashes[1]


def test_bundle_fingerprint_includes_processor_source(tmp_path: Path):
    processor = tmp_path / "guard.py"
    processor.write_text("class Guard: pass\n")
    config = tmp_path / "config.yaml"
    config.write_text(f"_target_: file://{processor}::Guard\n")
    before = bundle_fingerprint(config)
    processor.write_text("class Guard: changed = True\n")
    assert bundle_fingerprint(config) != before


def _job(path: Path, rewards: dict[str, int]) -> Path:
    path.mkdir()
    (path / "summary.json").write_text(json.dumps({
        "results": [{"task_id": task, "reward": reward, "status": "ok"}
                    for task, reward in rewards.items()]
    }))
    return path


def test_paired_report_counts_task_level_flips(tmp_path: Path):
    baseline = _job(tmp_path / "baseline", {"a": 1, "b": 0, "c": 1})
    candidate = _job(tmp_path / "candidate", {"a": 1, "b": 1, "c": 0})
    report = paired_report(baseline, candidate)
    assert (report["paired_wins"], report["paired_losses"], report["paired_ties"]) == (1, 1, 1)
    assert report["one_sided_sign_test_p"] == 0.75

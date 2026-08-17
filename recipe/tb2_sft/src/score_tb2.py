#!/usr/bin/env python3
"""Aggregate TB2 harbor job results into a score table.

Usage:
  python3 score_tb2.py [glob-pattern ...]

Reports per-job: n tasks, pass count, pass rate, and how many trials produced
zero output tokens (the signature of a provider/transport failure rather than a
genuine task failure).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / ".benchmarks" / "tb2"
REPORTS = ROOT / "outputs" / "reports"


def score_job(job_dir: Path) -> dict | None:
    results = sorted(job_dir.glob("*/result.json"))
    if not results:
        return None
    rows = []
    for rp in results:
        try:
            d = json.loads(rp.read_text())
        except Exception:
            continue
        reward = (d.get("verifier_result") or {}).get("rewards", {}).get("reward")
        agent = d.get("agent_result") or {}
        rows.append(
            {
                "task": d.get("task_name"),
                "reward": reward,
                "out_tokens": agent.get("n_output_tokens"),
                "exception": bool(d.get("exception_info")),
                "model": ((d.get("agent_info") or {}).get("model_info") or {}).get("name"),
            }
        )
    if not rows:
        return None
    n = len(rows)
    passed = sum(1 for r in rows if (r["reward"] or 0) >= 1.0)
    zero_tok = sum(1 for r in rows if not r["out_tokens"])
    return {
        "job": job_dir.name,
        "model": rows[0]["model"],
        "n": n,
        "passed": passed,
        "rate": passed / n,
        "zero_output_trials": zero_tok,
        "tasks_passed": sorted(r["task"] for r in rows if (r["reward"] or 0) >= 1.0),
        "rows": rows,
    }


def main() -> None:
    patterns = sys.argv[1:] or ["tb2-*"]
    dirs: list[Path] = []
    for pat in patterns:
        dirs.extend(sorted(p for p in BENCH.glob(pat) if p.is_dir()))
    out = []
    for d in dirs:
        s = score_job(d)
        if s:
            out.append(s)
    out.sort(key=lambda s: s["job"])
    print(f"{'job':<52} {'model':<14} {'n':>3} {'pass':>5} {'rate':>7} {'0-tok':>6}")
    print("-" * 95)
    for s in out:
        print(
            f"{s['job']:<52} {str(s['model']):<14} {s['n']:>3} {s['passed']:>5} "
            f"{s['rate']:>6.1%} {s['zero_output_trials']:>6}"
        )
    REPORTS.mkdir(parents=True, exist_ok=True)
    output = REPORTS / "scores.json"
    output.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {output}")


if __name__ == "__main__":
    main()

"""Low-cost paired evaluation helpers for the Tmax co-evolution gate."""
from __future__ import annotations
import argparse, hashlib, json, math, re
from pathlib import Path

_FILE_URI = re.compile(r"file://([^\s:'\"#]+)")

def bundle_fingerprint(config: Path) -> str:
    config = config.resolve(); files = {config}
    prompt = config.parent / "system_prompt.txt"
    if prompt.is_file(): files.add(prompt.resolve())
    for dirname in ("templates", "processors"):
        root = config.parent / dirname
        if root.is_dir(): files.update(p.resolve() for p in root.rglob("*") if p.is_file())
    yaml_text = config.read_text(encoding="utf-8")
    for raw in _FILE_URI.findall(yaml_text):
        path = Path(raw.split("::", 1)[0])
        if path.is_file(): files.add(path.resolve())
    digest = hashlib.sha256()
    for path in sorted(files, key=str):
        role = "config.yaml" if path == config else path.name
        data = path.read_bytes()
        if path == config:
            data = _FILE_URI.sub(lambda m: f"file://external/{Path(m.group(1)).name}", data.decode()).encode()
        digest.update(role.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()

def _rows(path: Path) -> dict[str, dict]:
    data = json.loads((path / "summary.json").read_text())
    return {str(x["task_id"]): x for x in data.get("results", [])}

def paired_report(baseline: Path, candidate: Path) -> dict:
    base, cand = _rows(baseline), _rows(candidate); tids = sorted(base.keys() & cand.keys())
    wins = losses = ties = 0; details = []
    for tid in tids:
        b, c = int(base[tid].get("reward") or 0), int(cand[tid].get("reward") or 0)
        outcome = "tie"
        if c > b: wins += 1; outcome = "win"
        elif c < b: losses += 1; outcome = "loss"
        else: ties += 1
        details.append({"task_id": tid, "baseline": b, "candidate": c, "outcome": outcome})
    n = wins + losses
    p = sum(math.comb(n, k) for k in range(wins, n + 1)) / 2**n if n else 1.0
    return {"n_tasks": len(tids), "paired_wins": wins, "paired_losses": losses,
            "paired_ties": ties, "one_sided_sign_test_p": p, "per_task": details}

def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("fingerprint"); fp.add_argument("config", type=Path)
    cp = sub.add_parser("compare"); cp.add_argument("baseline", type=Path); cp.add_argument("candidate", type=Path); cp.add_argument("--output", type=Path); cp.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args()
    if args.cmd == "fingerprint": print(bundle_fingerprint(args.config)); return 0
    report = paired_report(args.baseline, args.candidate)
    report["alpha"] = args.alpha
    report["accepted"] = (report["paired_wins"] > report["paired_losses"] and
                          report["one_sided_sign_test_p"] < args.alpha)
    text = json.dumps(report, indent=2) + "\n"
    if args.output: args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(text)
    print(json.dumps({k: v for k, v in report.items() if k != "per_task"}, indent=2)); return 0 if report["accepted"] else 1
if __name__ == "__main__": raise SystemExit(main())

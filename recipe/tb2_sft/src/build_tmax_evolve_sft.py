#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build a LoRA SFT corpus from Tmax harness-evolve trajectories.

Scans ``.benchmarks/tmax/<run_tag>-r*-traj/`` (or explicit traj dirs), keeps
successful trajectories, expands them into conversational prompt/completion
pairs, and writes ``train.jsonl`` / ``eval.jsonl`` / ``summary.json``.

The coevolve holdout gate evaluates one harness (the incumbent after the
harness ratchet). Training on every tournament candidate's successes, with
no system prompt and a score that prefers short easy traces, taught the
model a different distribution than eval. Defaults here match that eval:

  - reward == 1 and status == ok, structured tool_calls, 2..60 tools
  - optional holdout-task exclusion
  - ``--eval-harness``: keep only traj dirs rolled out under that YAML
    (drops losing ``fe-c*`` candidates) and prepend its ``system_prompt.txt``
  - quality score prefers longer / slower successes over 7-step clones
  - ``--per-task`` default 2 (not 8)
  - ``--max-prev-frac`` caps older-iteration trajs so they cannot dominate

Example:
  python -m recipe.tb2_sft.src.build_tmax_evolve_sft \\
    --run-tag tmax-coev-rep19-i2 \\
    --eval-harness recipe/tb2_evolver/runs/tmax-coev-rep19-i2/R1/config.yaml \\
    --name tmax_coevolve_i2 \\
    --min-trajs 20
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import filter_and_build_sft as F  # noqa: E402

_CTRL_C_RE = re.compile(r"\x03|ctrl[_-]?c", re.IGNORECASE)
_TRAJ_DIR_RE = re.compile(r"^(?P<tag>.+)-r(?P<round>\d+)(?:-fe-c(?P<cand>\d+))?-traj$")
_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tmax"
_DEFAULT_DATA = _ROOT / "recipe" / "tb2_sft" / "data"
_DEFAULT_EVOLVE_RUNS = _ROOT / "recipe" / "tb2_evolver" / "runs"
_DEFAULT_SYSTEM_PROMPT = _ROOT / "configs" / "tmax_system_prompt.txt"


def _load_task_ids(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    items = raw if isinstance(raw, list) else raw.get("tasks") or raw.get("task_names") or []
    if isinstance(raw, dict) and not items:
        items = list(raw.keys())
    for x in items:
        if isinstance(x, str):
            out.add(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id") or x.get("task")
            if tid:
                out.add(str(tid))
    return out


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize evolve-traj messages into filter_and_build_sft's internal shape."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(m.get("tool_call_id") or ""),
                    "name": str(m.get("name") or "Bash"),
                    "content": m.get("content") or "[empty tool result]",
                }
            )
            continue
        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            tcs = m.get("tool_calls")
            if tcs:
                fixed = []
                for i, tc in enumerate(tcs):
                    tc = dict(tc)
                    fn = dict(tc.get("function") or {})
                    name = str(fn.get("name") or "Bash")
                    if name.lower() == "bash":
                        name = "Bash"
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        arg_str = json.dumps(args, ensure_ascii=False)
                    else:
                        arg_str = str(args or "{}")
                    fixed.append(
                        {
                            "id": str(tc.get("id") or f"call_{i}"),
                            "type": "function",
                            "function": {"name": name, "arguments": arg_str},
                        }
                    )
                item["tool_calls"] = fixed
            out.append(item)
            continue
        out.append({"role": role, "content": m.get("content") or ""})
    return out


def _n_tools(messages: list[dict[str, Any]]) -> int:
    return sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")


def _has_ctrl_c(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        if _CTRL_C_RE.search(str(m.get("content") or "")):
            return True
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            if _CTRL_C_RE.search(str(fn.get("arguments") or "")):
                return True
    return False


def _discover_traj_dirs(
    *,
    run_tags: list[str],
    traj_dirs: list[Path],
    bench_root: Path,
) -> list[Path]:
    found: list[Path] = []
    for tag in run_tags:
        found.extend(sorted(bench_root.glob(f"{tag}-r*-traj")))
    for d in traj_dirs:
        p = Path(d)
        if p.is_dir():
            found.append(p)
    # stable unique
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def _parse_traj_dir_name(name: str) -> tuple[str, int, int | None] | None:
    m = _TRAJ_DIR_RE.match(name)
    if not m:
        return None
    cand = m.group("cand")
    return m.group("tag"), int(m.group("round")), int(cand) if cand is not None else None


def _evolve_state_path(runs_root: Path, tag: str) -> Path | None:
    for p in (
        runs_root / tag / "_meta_v2" / "_meta_scratch" / "harness_evolve_state.json",
        runs_root / tag / "_meta" / "_meta_scratch" / "harness_evolve_state.json",
    ):
        if p.is_file():
            return p
    return None


def _fingerprint_harness(cfg: Path | None) -> tuple[bytes, bytes] | None:
    """(yaml_bytes, prompt_bytes). Missing prompt is empty bytes, not a skip."""
    if cfg is None:
        return None
    path = Path(cfg)
    if not path.is_file():
        return None
    yaml_b = path.read_bytes()
    prompt_p = path.parent / "system_prompt.txt"
    prompt_b = prompt_p.read_bytes() if prompt_p.is_file() else b""
    return yaml_b, prompt_b


def _index_traj_to_config(runs_root: Path, tags: list[str]) -> dict[Path, Path]:
    """Map rollout traj dirs to the harness YAML they were evaluated with."""
    out: dict[Path, Path] = {}
    for tag in tags:
        state_p = _evolve_state_path(runs_root, tag)
        if state_p is None:
            continue
        try:
            state = json.loads(state_p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for rec in state.get("history") or []:
            if not isinstance(rec, dict):
                continue
            traj = rec.get("trajectories_dir")
            cfg = rec.get("input_config") or rec.get("gated_config")
            if traj and cfg:
                tp, cp = Path(traj), Path(cfg)
                if tp.is_dir() and cp.is_file():
                    out[tp.resolve()] = cp.resolve()
            for row in rec.get("tournament_last_round") or []:
                if not isinstance(row, dict):
                    continue
                t = row.get("trajectories")
                c = row.get("config")
                if t and c:
                    tp, cp = Path(t), Path(c)
                    if tp.is_dir() and cp.is_file():
                        out[tp.resolve()] = cp.resolve()
    return out


def _heuristic_traj_config(traj_dir: Path, runs_root: Path) -> Path | None:
    parsed = _parse_traj_dir_name(traj_dir.name)
    if parsed is None:
        return None
    tag, rnd, cand = parsed
    if cand is not None:
        p = runs_root / tag / "_meta_v2" / f"R{rnd + 1}" / f"c{cand}" / "config.yaml"
    else:
        p = runs_root / tag / f"R{rnd}" / "config.yaml"
    return p if p.is_file() else None


def resolve_traj_harness(
    traj_dir: Path,
    *,
    runs_root: Path,
    state_index: dict[Path, Path],
) -> Path | None:
    sidecar = traj_dir / "harness_config.yaml"
    if sidecar.is_file():
        return sidecar
    mapped = state_index.get(traj_dir.resolve())
    if mapped is not None and mapped.is_file():
        return mapped
    return _heuristic_traj_config(traj_dir, runs_root)


def filter_traj_dirs_to_harness(
    traj_dirs: list[Path],
    *,
    eval_harness: Path,
    runs_root: Path,
) -> tuple[list[Path], list[Path]]:
    """Keep dirs rolled out under ``eval_harness``. Unmatched dirs are dropped."""
    want = _fingerprint_harness(eval_harness)
    if want is None:
        return [], list(traj_dirs)
    tags: list[str] = []
    seen_tags: set[str] = set()
    for d in traj_dirs:
        parsed = _parse_traj_dir_name(d.name)
        if parsed and parsed[0] not in seen_tags:
            seen_tags.add(parsed[0])
            tags.append(parsed[0])
    state_index = _index_traj_to_config(runs_root, tags)
    kept: list[Path] = []
    dropped: list[Path] = []
    for d in traj_dirs:
        got = _fingerprint_harness(resolve_traj_harness(d, runs_root=runs_root, state_index=state_index))
        if got == want:
            kept.append(d)
        else:
            dropped.append(d)
    return kept, dropped


def resolve_sft_system_prompt(eval_harness: Path | None) -> str:
    """Same preference as holdout eval: sibling prompt, else repo default."""
    if eval_harness is not None:
        cfg = Path(eval_harness)
        sibling = cfg.parent / "system_prompt.txt"
        if sibling.is_file():
            return sibling.read_text(encoding="utf-8")
        templates = cfg.parent / "templates"
        if templates.is_dir():
            j2s = sorted(templates.glob("*.j2"))
            if j2s:
                return j2s[0].read_text(encoding="utf-8")
    if _DEFAULT_SYSTEM_PROMPT.is_file():
        return _DEFAULT_SYSTEM_PROMPT.read_text(encoding="utf-8")
    return ""


def inject_system_prompt(
    messages: list[dict[str, Any]], system_text: str
) -> list[dict[str, Any]]:
    text = (system_text or "").strip()
    if not text:
        return list(messages)
    rest = [m for m in messages if m.get("role") != "system"]
    return [{"role": "system", "content": text}] + rest


def _evolve_sft_quality_score(meta: F.TrialMeta, elapsed_s: float | None = None) -> float:
    """Prefer compact, non-loop successes over long/slow traces.

    The previous score *added* for more tool turns (up to 40) and longer
    wall-clock, which ranked 4B retry-loops above a clean 12-step solve.
    Isolated from ``filter_and_build_sft._quality_score`` (that one still
    prefers compact traces for the non-coevolve parquet pipeline).
    """
    score = 1.0 * meta.reward
    n = int(meta.n_tool_turns)
    # Sweet spot ~8–20 tool turns: enough work, not a retry loop.
    if n < 8:
        score -= 0.20 * (8 - n) / 8.0
    elif n <= 20:
        score += 0.10 * (n - 8) / 12.0
    else:
        score -= 0.40 * min(n - 20, 40) / 40.0
    if isinstance(elapsed_s, (int, float)) and elapsed_s > 0:
        e = float(elapsed_s)
        if e <= 180:
            score += 0.10 * (1.0 - e / 180.0)
        elif e <= 400:
            score -= 0.05 * (e - 180.0) / 220.0
        else:
            score -= 0.20 + 0.10 * min(e - 400.0, 600.0) / 600.0
    return score


def scan_successes(
    traj_dirs: list[Path],
    *,
    min_tools: int,
    max_tools: int,
    exclude_tasks: set[str],
) -> tuple[list[tuple[F.TrialMeta, list[dict[str, Any]]]], dict[str, int]]:
    stats = {
        "result_files": 0,
        "reward_pass": 0,
        "missing_messages": 0,
        "bad_status": 0,
        "tool_gate": 0,
        "ctrl_c": 0,
        "holdout_excluded": 0,
        "kept_raw": 0,
    }
    kept: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []

    for traj_dir in traj_dirs:
        run_name = traj_dir.name
        for rp in sorted(traj_dir.glob("*.result.json")):
            if rp.name == "result.json":
                continue
            stats["result_files"] += 1
            try:
                result = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                continue
            reward = result.get("reward")
            if reward is None:
                vr = result.get("verifier_result") or {}
                reward = (vr.get("rewards") or {}).get("reward")
            if not isinstance(reward, (int, float)) or float(reward) <= 0:
                continue
            stats["reward_pass"] += 1

            status = str(result.get("status") or "ok")
            if status not in {"ok", "success", "passed"}:
                stats["bad_status"] += 1
                continue

            task_id = str(result.get("task_id") or rp.name.replace(".result.json", ""))
            if task_id in exclude_tasks:
                stats["holdout_excluded"] += 1
                continue

            mp = traj_dir / f"{task_id}.messages.json"
            if not mp.is_file():
                # some layouts use stem without task_ prefix mismatch
                alt = traj_dir / rp.name.replace(".result.json", ".messages.json")
                mp = alt if alt.is_file() else mp
            if not mp.is_file():
                stats["missing_messages"] += 1
                continue

            try:
                raw_msgs = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                stats["missing_messages"] += 1
                continue
            if isinstance(raw_msgs, dict):
                raw_msgs = raw_msgs.get("messages") or []
            if not isinstance(raw_msgs, list) or not raw_msgs:
                stats["missing_messages"] += 1
                continue

            messages = _normalize_messages(raw_msgs)
            if _has_ctrl_c(messages):
                stats["ctrl_c"] += 1
                continue

            n_tools = _n_tools(messages)
            has_struct = n_tools > 0 and any(
                m.get("role") == "assistant" and m.get("tool_calls") for m in messages
            )
            if not has_struct or not (min_tools <= n_tools <= max_tools):
                stats["tool_gate"] += 1
                continue

            meta = F.TrialMeta(
                run=run_name,
                trial_dir=str(mp),
                task=task_id,
                reward=float(reward),
                model=str(result.get("model") or "evolve"),
                n_tool_turns=n_tools,
                n_recovered_tools=0,
                n_steps=int(result.get("n_steps") or result.get("steps") or len(messages)),
                source_bucket="tmax_evolve",
                quality_score=0.0,
                has_structured_tools=True,
                exception=False,
            )
            elapsed = result.get("elapsed_s")
            elapsed_f = float(elapsed) if isinstance(elapsed, (int, float)) else None
            meta.quality_score = _evolve_sft_quality_score(meta, elapsed_f)
            kept.append((meta, messages))
            stats["kept_raw"] += 1

    return kept, stats


def dedupe_by_task(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    per_task: int = 1,
) -> list[tuple[F.TrialMeta, list[dict[str, Any]]]]:
    """Keep up to ``per_task`` highest-quality successes per task_id.

    ``per_task=1`` is strict unique-task dedupe. Raising it (e.g. 2–3) is how
    a 50-task evolve set can still yield 50–100 demos across rounds without
    cloning identical traces — different rounds usually differ.
    """
    buckets: dict[str, list[tuple[F.TrialMeta, list[dict[str, Any]]]]] = {}
    for meta, msgs in rows:
        buckets.setdefault(meta.task, []).append((meta, msgs))
    out: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    for task, items in buckets.items():
        items.sort(key=lambda x: (-x[0].quality_score, -x[0].n_tool_turns, x[0].run))
        # Drop near-duplicates: same n_tools and same first assistant snippet.
        uniq: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
        seen_sig: set[str] = set()
        for meta, msgs in items:
            first_asst = next((m for m in msgs if m.get("role") == "assistant"), {})
            sig = f"{meta.n_tool_turns}|{str(first_asst.get('content') or '')[:120]}"
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            uniq.append((meta, msgs))
            if len(uniq) >= max(1, per_task):
                break
        out.extend(uniq)
    return out


def _rank_and_cap(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    max_trajs: int,
    seed: int,
) -> list[tuple[F.TrialMeta, list[dict[str, Any]]]]:
    ranked = sorted(rows, key=lambda x: (-x[0].quality_score, x[0].n_tool_turns, x[0].task))
    # max_trajs <= 0 disables the cap: keep every trajectory that cleared the
    # quality gates and the per-task cap.
    if max_trajs <= 0 or len(ranked) <= max_trajs:
        return ranked
    # Keep top half by quality, sample the rest for diversity.
    keep_top = max(max_trajs // 2, 1)
    head = ranked[:keep_top]
    tail = ranked[keep_top:]
    rng = random.Random(seed)
    need = max_trajs - len(head)
    if need > 0:
        head.extend(rng.sample(tail, min(need, len(tail))))
    return head[:max_trajs]


def select_trajs(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    max_trajs: int,
    seed: int,
    prefer_runs: tuple[str, ...] = (),
    max_prev_frac: float = 1.0,
) -> list[tuple[F.TrialMeta, list[dict[str, Any]]]]:
    """Rank and cap at ``max_trajs``, filling ``prefer_runs`` demos first.

    With a rotating evolve set, the corpus for iteration k mixes this
    iteration's trajectories with every earlier iteration's. Preferring the
    current run tag keeps the new material in. ``max_prev_frac`` then caps how
    large the older-iteration slice can be (old / selected <= frac). If this
    iteration produced no preferred trajs, the old pool is kept as-is so an
    empty current slice cannot wipe the corpus.
    """
    if not prefer_runs:
        return _rank_and_cap(rows, max_trajs=max_trajs, seed=seed)

    def _is_preferred(meta: F.TrialMeta) -> bool:
        return any(str(meta.run).startswith(pref) for pref in prefer_runs)

    preferred = [r for r in rows if _is_preferred(r[0])]
    rest = [r for r in rows if not _is_preferred(r[0])]
    ranked_pref = _rank_and_cap(preferred, max_trajs=0, seed=seed)
    ranked_rest = _rank_and_cap(rest, max_trajs=0, seed=seed)
    if ranked_pref and 0.0 <= max_prev_frac < 1.0:
        # old / (current + old) <= frac  →  old <= frac/(1-frac) * current
        max_old = int(max_prev_frac / max(1e-9, 1.0 - max_prev_frac) * len(ranked_pref))
        ranked_rest = ranked_rest[:max_old]
    if max_trajs <= 0:
        return ranked_pref + ranked_rest
    out = _rank_and_cap(ranked_pref, max_trajs=max_trajs, seed=seed)
    remaining = max_trajs - len(out)
    if remaining > 0:
        out.extend(_rank_and_cap(ranked_rest, max_trajs=remaining, seed=seed))
    return out


def build_pairs(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    max_pairs_per_traj: int,
    system_text: str = "",
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    pair_cap = max_pairs_per_traj if max_pairs_per_traj > 0 else 10**9
    for meta, messages in rows:
        record = {
            "messages": inject_system_prompt(messages, system_text),
            "task": meta.task,
            "run": meta.run,
            "reward": meta.reward,
            "weight": 1.0,
            "source_bucket": meta.source_bucket,
            "quality_score": meta.quality_score,
        }
        pairs.extend(F.expand_turn_pairs(record, max_pairs=pair_cap))
    # Light pair-level dedupe on (task, response prefix).
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for p in pairs:
        key = f"{p.get('task')}|{str(p.get('response') or '')[:240]}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-tag", action="append", default=[], help="Evolve run tag (repeatable)")
    ap.add_argument("--traj-dir", action="append", default=[], type=Path, help="Explicit traj dir")
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--name", required=True, help="Dataset name under recipe/tb2_sft/data/")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--min-trajs", type=int, default=80)
    ap.add_argument(
        "--max-trajs",
        type=int,
        default=0,
        help="Cap on selected trajectories; <= 0 keeps every survivor (default)",
    )
    ap.add_argument(
        "--per-task",
        type=int,
        default=2,
        help="Max demos kept per task_id across rounds (default 2)",
    )
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument(
        "--max-pairs-per-traj",
        type=int,
        default=32,
        help="Assistant turns kept per traj (default 32; <=0 = all)",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--prefer-run-tag",
        action="append",
        default=[],
        help="Fill the --max-trajs budget from these run tags first (repeatable). "
        "Used by the coevolve loop so a cumulative corpus cannot crowd out the "
        "current iteration's fresh successes.",
    )
    ap.add_argument(
        "--max-prev-frac",
        type=float,
        default=0.4,
        help="When --prefer-run-tag is set, older-iteration trajs may be at most "
        "this fraction of the selected set (default 0.4). Ignored if the current "
        "tag produced no trajs, so the old pool is not wiped.",
    )
    ap.add_argument(
        "--eval-harness",
        type=Path,
        default=None,
        help="Harness YAML the holdout eval actually used. Injects its "
        "system_prompt.txt into every training example. With --winner-only, "
        "also drops traj dirs rolled out under a different YAML.",
    )
    ap.add_argument(
        "--winner-only",
        action="store_true",
        help="Keep only traj dirs whose harness fingerprint matches --eval-harness. "
        "Unmatched dirs (losing fe-c* candidates) are dropped.",
    )
    ap.add_argument(
        "--evolve-runs-root",
        type=Path,
        default=_DEFAULT_EVOLVE_RUNS,
        help="recipe/tb2_evolver/runs — used to map traj dirs back to configs",
    )
    ap.add_argument(
        "--exclude-tasks",
        type=Path,
        default=_ROOT / "recipe" / "tb2_evolver" / "tasks_tmax_only200.json",
        help="Holdout task list to exclude from SFT (default: 102 holdout)",
    )
    ap.add_argument("--no-exclude-holdout", action="store_true")
    ap.add_argument("--eval-ratio", type=float, default=0.05)
    args = ap.parse_args()

    if not args.run_tag and not args.traj_dir:
        ap.error("pass --run-tag and/or --traj-dir")
    if args.winner_only and args.eval_harness is None:
        ap.error("--winner-only requires --eval-harness")
    if not (0.0 <= args.max_prev_frac <= 1.0):
        ap.error("--max-prev-frac must be in [0, 1]")

    exclude = set() if args.no_exclude_holdout else _load_task_ids(args.exclude_tasks)
    traj_dirs = _discover_traj_dirs(
        run_tags=list(args.run_tag),
        traj_dirs=list(args.traj_dir or []),
        bench_root=args.bench_root,
    )
    if not traj_dirs:
        print("ERROR: no trajectory dirs found", file=sys.stderr)
        return 2

    dropped_dirs: list[Path] = []
    discovered_n = len(traj_dirs)
    if args.winner_only and args.eval_harness is not None:
        eval_h = Path(args.eval_harness).resolve()
        if not eval_h.is_file():
            print(f"ERROR: --eval-harness not found: {eval_h}", file=sys.stderr)
            return 2
        traj_dirs, dropped_dirs = filter_traj_dirs_to_harness(
            traj_dirs, eval_harness=eval_h, runs_root=Path(args.evolve_runs_root)
        )
        print(
            f"winner-only: kept {len(traj_dirs)}/{discovered_n} traj dir(s) "
            f"matching {eval_h}"
        )
        for d in dropped_dirs:
            print(f"  drop {d.name}")
        if not traj_dirs:
            print(
                "ERROR: winner-only filter dropped every traj dir "
                "(no rollout matched --eval-harness)",
                file=sys.stderr,
            )
            return 2

    print(f"scanning {len(traj_dirs)} traj dir(s):")
    for d in traj_dirs:
        print(f"  - {d}")

    system_text = resolve_sft_system_prompt(args.eval_harness)
    if system_text.strip():
        print(f"system prompt: {len(system_text)} chars")
    else:
        print("WARNING: no system prompt resolved; pairs will have no system turn")

    raw, scan_stats = scan_successes(
        traj_dirs,
        min_tools=args.min_tools,
        max_tools=args.max_tools,
        exclude_tasks=exclude,
    )
    deduped = dedupe_by_task(raw, per_task=args.per_task)
    selected = select_trajs(
        deduped,
        max_trajs=args.max_trajs,
        seed=args.seed,
        prefer_runs=tuple(args.prefer_run_tag or []),
        max_prev_frac=args.max_prev_frac,
    )

    if len(selected) < args.min_trajs:
        print(
            f"WARNING: only {len(selected)} unique success trajs "
            f"(wanted >= {args.min_trajs}). Proceeding anyway.",
            file=sys.stderr,
        )

    pairs = build_pairs(
        selected,
        max_pairs_per_traj=args.max_pairs_per_traj,
        system_text=system_text,
    )
    if not pairs:
        print("ERROR: no supervised pairs produced", file=sys.stderr)
        return 2

    train, ev = F.split_pairs(pairs, eval_ratio=args.eval_ratio, seed=args.seed)
    if not ev:
        # tiny corpus: peel one pair for eval.jsonl so train_sft.sh's require_file passes
        ev = train[-1:]
        train = train[:-1] or ev

    out = args.out_root / args.name
    out.mkdir(parents=True, exist_ok=True)
    F.write_jsonl(out / "train.jsonl", train)
    F.write_jsonl(out / "eval.jsonl", ev)

    n_from_pref = sum(
        1
        for m, _ in selected
        if any(str(m.run).startswith(p) for p in (args.prefer_run_tag or []))
    )
    summary = {
        "name": args.name,
        "traj_dirs": [str(d) for d in traj_dirs],
        "traj_dirs_dropped": [str(d) for d in dropped_dirs],
        "n_traj_dirs_discovered": discovered_n,
        "n_traj_dirs_kept": len(traj_dirs),
        "eval_harness": str(args.eval_harness) if args.eval_harness else None,
        "winner_only": bool(args.winner_only),
        "system_prompt_chars": len(system_text),
        "scan": scan_stats,
        "n_raw_kept": len(raw),
        "n_unique_tasks": len({m.task for m, _ in deduped}),
        "n_after_per_task_cap": len(deduped),
        "n_selected_trajs": len(selected),
        "per_task": args.per_task,
        "prefer_run_tags": list(args.prefer_run_tag or []),
        "max_prev_frac": args.max_prev_frac,
        "n_selected_from_preferred": n_from_pref,
        "n_selected_from_prev": len(selected) - n_from_pref,
        "max_pairs_per_traj": args.max_pairs_per_traj,
        "n_train_pairs": len(train),
        "n_eval_pairs": len(ev),
        "min_trajs": args.min_trajs,
        "max_trajs": args.max_trajs,
        "min_tools": args.min_tools,
        "max_tools": args.max_tools,
        "exclude_holdout_n": len(exclude),
        "selected_tasks": [m.task for m, _ in selected],
        "selected_meta": [
            {
                "task": m.task,
                "run": m.run,
                "n_tools": m.n_tool_turns,
                "quality_score": m.quality_score,
            }
            for m, _ in selected
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # also dump trial metas for debugging
    (out / "selected_trials.json").write_text(
        json.dumps([asdict(m) for m, _ in selected], indent=2),
        encoding="utf-8",
    )

    print(
        f"wrote {out}: trajs={len(selected)} "
        f"train_pairs={len(train)} eval_pairs={len(ev)} "
        f"(raw_pass_gate={scan_stats['kept_raw']} "
        f"unique_tasks={len({m.task for m, _ in deduped})} "
        f"after_per_task={len(deduped)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

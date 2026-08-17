#!/usr/bin/env python3
"""Evaluate a model on Tmax taxonomy tasks (train-on-test for tmax_only200).

Does NOT use Terminal-Bench / Harbor. Pipeline per task:

  1. Singularity container_def → Docker image (cached)
  2. Start container, optional pytest test_initial_state
  3. Agent: full HarnessX (processors + tools + system prompt) when
     ``--harness-config`` is set; otherwise the thin Bash-only loop
  4. pytest test_final_state → reward 0/1

Usage (vLLM already up, or via scripts/evaluate_tmax.sh)::

  python -m recipe.tmax_eval.run_eval \\
    --tasks-json recipe/tb2_evolver/tasks_tmax_only200.json \\
    --job-name tmax-base9b \\
    --api-base http://127.0.0.1:8300/v1 \\
    --model Qwen/Qwen3.5-9B \\
    --harness-config configs/baseline_tmax_harness.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import agent_loop, docker_env

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENVS = (
    ROOT
    / "recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
)
DEFAULT_TASKS = ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"


def _load_task_ids(path: Path) -> list[str]:
    obj = json.loads(path.read_text())
    if isinstance(obj, list):
        return [str(x) for x in obj]
    if isinstance(obj, dict):
        if "task_ids" in obj:
            return [str(x) for x in obj["task_ids"]]
        if "tasks" in obj and obj["tasks"] and isinstance(obj["tasks"][0], str):
            return [str(x) for x in obj["tasks"]]
        if "tasks" in obj and obj["tasks"] and isinstance(obj["tasks"][0], dict):
            return [str(t["task_id"]) for t in obj["tasks"]]
    raise SystemExit(f"unrecognized tasks json shape: {path}")


def _load_envs(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            out[str(row["task_id"])] = row
    return out


def _endpoints(args: argparse.Namespace) -> list[str]:
    if args.endpoints_file:
        eps = json.loads(Path(args.endpoints_file).read_text())
        return [str(e) for e in eps]
    env_file = os.environ.get("TB2_ENDPOINTS_FILE")
    if env_file and Path(env_file).is_file():
        return [str(e) for e in json.loads(Path(env_file).read_text())]
    base = args.api_base or os.environ.get("TB2_API_BASE")
    if not base:
        raise SystemExit("need --api-base or --endpoints-file or TB2_ENDPOINTS_FILE")
    return [base]


def _model_name(args: argparse.Namespace) -> str:
    return args.model or os.environ.get("TB2_MODEL") or os.environ.get("MODEL") or ""


def _resolve_system_prompt(
    harness_config: Path | None,
    system_prompt_file: Path | None,
) -> str | None:
    """Resolve Tmax agent system prompt for an evolve round.

    Preference order:
      1. --system-prompt-file
      2. system_prompt.txt next to harness config (MetaAgent can write this)
      3. templates/*.j2 under the harness config directory
      4. None → agent_loop default
    """
    if system_prompt_file is not None and system_prompt_file.is_file():
        return system_prompt_file.read_text(encoding="utf-8")
    if harness_config is not None:
        cfg = Path(harness_config).resolve()
        sibling = cfg.parent / "system_prompt.txt"
        if sibling.is_file():
            return sibling.read_text(encoding="utf-8")
        templates = cfg.parent / "templates"
        if templates.is_dir():
            j2s = sorted(templates.glob("*.j2"))
            if j2s:
                return j2s[0].read_text(encoding="utf-8")
    env_prompt = (os.environ.get("TMAX_SYSTEM_PROMPT_FILE") or "").strip()
    if env_prompt and Path(env_prompt).is_file():
        return Path(env_prompt).read_text(encoding="utf-8")
    return None


def run_one(
    task: dict[str, Any],
    *,
    api_base: str,
    model: str,
    work_root: Path,
    out_dir: Path,
    max_steps: int,
    temperature: float,
    rebuild: bool,
    skip_initial: bool,
    keep_container: bool,
    network: str,
    system_prompt: str | None = None,
    harness_config: Path | None = None,
) -> dict[str, Any]:
    tid = task["task_id"]
    t0 = time.time()
    result: dict[str, Any] = {
        "task_id": tid,
        "domain": task.get("domain"),
        "reward": 0,
        "status": "error",
        "elapsed_s": 0,
        "api_base": api_base,
        "model": model,
    }
    container = None
    try:
        image = docker_env.build_image(
            tid, task["container_def"], work_root, rebuild=rebuild
        )
        result["image"] = image
        container = docker_env.start_container(tid, image, network=network)
        result["container"] = container

        if not skip_initial and task.get("test_initial_state"):
            init = docker_env.run_pytest(
                container,
                task["test_initial_state"],
                dest="/tmp/test_initial_state.py",
            )
            result["initial_pytest"] = {
                "passed": init["passed"],
                "rc": init["rc"],
                "output_tail": init["output"][-1500:],
            }
            if not init["passed"]:
                # Still continue — some fixtures are agent-facing checks; but
                # record the failure for diagnosis.
                result["initial_pytest_failed"] = True

        def _exec(cmd: str, timeout: float) -> tuple[int, str]:
            return docker_env.exec_in(container, cmd, timeout=timeout)

        if harness_config is not None:
            # Full HarnessX path: processors + tool_registry + system prompt.
            from . import harness_runner

            agent = harness_runner.run_harness_agent(
                harness_config=Path(harness_config),
                container=container,
                instruction=str(task["description"]),
                api_base=api_base,
                model=model,
                max_steps=max_steps,
                temperature=temperature,
                api_key=os.environ.get("TB2_API_KEY", "EMPTY"),
                journal_dir=out_dir / tid / "oh_runs",
            )
            result["agent"] = {
                "steps": agent.steps,
                "finished": agent.finished,
                "error": agent.error,
                "exit_reason": agent.exit_reason,
                "backend": "harnessx",
            }
            messages = agent.messages
        else:
            # Legacy thin loop (prompt-only, fixed Bash tool). Kept for smoke /
            # debug when no HarnessConfig is supplied.
            raw_max = (os.environ.get("TMAX_MAX_TOKENS") or "4096").strip()
            try:
                thin_max_tokens = int(raw_max)
            except ValueError:
                thin_max_tokens = 4096
            agent = agent_loop.run_agent(
                api_base=api_base,
                model=model,
                instruction=str(task["description"]),
                exec_fn=_exec,
                max_steps=max_steps,
                temperature=temperature,
                api_key=os.environ.get("TB2_API_KEY", "EMPTY"),
                system_prompt=system_prompt,
                max_tokens=thin_max_tokens if thin_max_tokens > 0 else None,
            )
            result["agent"] = {
                "steps": agent.steps,
                "finished": agent.finished,
                "error": agent.error,
                "backend": "thin",
            }
            messages = agent.messages
        # Persist transcript (truncate tool outputs already capped).
        (out_dir / f"{tid}.messages.json").write_text(
            json.dumps(messages, indent=2, ensure_ascii=False) + "\n"
        )

        final = docker_env.run_pytest(
            container,
            task["test_final_state"],
            dest="/tmp/test_final_state.py",
        )
        result["final_pytest"] = {
            "passed": final["passed"],
            "rc": final["rc"],
            "output_tail": final["output"][-2000:],
        }
        result["reward"] = 1 if final["passed"] else 0
        result["status"] = "ok" if agent.error is None else "agent_error"
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-3000:]
    finally:
        if container and not keep_container:
            try:
                docker_env.stop_container(container)
            except Exception:
                pass
        result["elapsed_s"] = round(time.time() - t0, 1)
        (out_dir / f"{tid}.result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        )
        # Trial-dir layout expected by harness evolve (read_per_task_results).
        trial = out_dir / tid
        trial.mkdir(parents=True, exist_ok=True)
        tb2_shaped = {
            "task_name": tid,
            "task_id": tid,
            "verifier_result": {"rewards": {"reward": float(result.get("reward") or 0)}},
            "reward": result.get("reward"),
            "status": result.get("status"),
            "elapsed_s": result.get("elapsed_s"),
            "error": result.get("error"),
            "agent": result.get("agent"),
            "final_pytest": result.get("final_pytest"),
            "model": model,
        }
        (trial / "result.json").write_text(
            json.dumps(tb2_shaped, indent=2, ensure_ascii=False) + "\n"
        )
        msg_src = out_dir / f"{tid}.messages.json"
        if msg_src.is_file():
            (trial / "messages.json").write_text(msg_src.read_text())
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks-json", type=Path, default=DEFAULT_TASKS)
    ap.add_argument("--envs-jsonl", type=Path, default=DEFAULT_ENVS)
    ap.add_argument("--job-name", required=True)
    ap.add_argument("--jobs-dir", type=Path, default=ROOT / ".benchmarks/tmax")
    ap.add_argument("--work-root", type=Path, default=ROOT / ".tmax_eval_work")
    ap.add_argument("--api-base", default=None)
    ap.add_argument("--endpoints-file", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-steps", type=int, default=int(os.environ.get("TMAX_MAX_STEPS", "80")))
    ap.add_argument("--temperature", type=float, default=float(os.environ.get("TB2_TEMPERATURE", "0")))
    ap.add_argument("--concurrent", type=int, default=int(os.environ.get("TMAX_CONCURRENT", "1")))
    ap.add_argument("--limit", type=int, default=0, help="only first N tasks (debug)")
    ap.add_argument("--task-id", action="append", default=[], help="run only these task ids")
    ap.add_argument("--rebuild-images", action="store_true")
    ap.add_argument("--skip-initial", action="store_true")
    ap.add_argument("--keep-container", action="store_true")
    ap.add_argument("--network", default=os.environ.get("TMAX_DOCKER_NETWORK", "bridge"))
    ap.add_argument("--resume", action="store_true", help="skip tasks with existing result.json")
    ap.add_argument(
        "--harness-config",
        type=Path,
        default=None,
        help=(
            "HarnessConfig YAML. When set, runs the full HarnessX pipeline "
            "(processors + tool_registry + SiblingSystemPromptBuilder reading "
            "sibling system_prompt.txt). When omitted, uses the thin Bash loop."
        ),
    )
    ap.add_argument(
        "--system-prompt-file",
        type=Path,
        default=None,
        help="Thin-loop only: override system prompt text file",
    )
    args = ap.parse_args(argv)

    model = _model_name(args)
    if not model:
        raise SystemExit("model name required (--model or TB2_MODEL)")
    endpoints = _endpoints(args)
    task_ids = _load_task_ids(args.tasks_json)
    if args.task_id:
        want = set(args.task_id)
        task_ids = [t for t in task_ids if t in want]
    if args.limit and args.limit > 0:
        task_ids = task_ids[: args.limit]

    envs = _load_envs(args.envs_jsonl)
    missing = [t for t in task_ids if t not in envs]
    if missing:
        raise SystemExit(
            f"{len(missing)} task ids missing from {args.envs_jsonl}: {missing[:5]}"
        )

    system_prompt = _resolve_system_prompt(args.harness_config, args.system_prompt_file)
    harness_config = Path(args.harness_config).resolve() if args.harness_config else None
    if harness_config is not None and not harness_config.is_file():
        raise SystemExit(f"harness config not found: {harness_config}")
    if harness_config is not None:
        os.environ["TB2_HARNESS_CONFIG"] = str(harness_config)
        os.environ["TMAX_HARNESS_CONFIG"] = str(harness_config)

    out_dir = args.jobs_dir / args.job_name
    out_dir.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)

    # Claim queue with round-robin endpoints.
    lock = threading.Lock()
    results: list[dict[str, Any]] = []
    pending = []
    for i, tid in enumerate(task_ids):
        rpath = out_dir / f"{tid}.result.json"
        if args.resume and rpath.is_file():
            try:
                prev = json.loads(rpath.read_text())
                if prev.get("status") in ("ok", "agent_error") and "reward" in prev:
                    results.append(prev)
                    print(f"[skip] {tid} (resume reward={prev.get('reward')})")
                    continue
            except Exception:
                pass
        pending.append((tid, endpoints[i % len(endpoints)]))

    print(
        f"[tmax_eval] job={args.job_name} model={model} tasks={len(pending)} "
        f"(resume_kept={len(results)}) endpoints={len(endpoints)} "
        f"concurrent={args.concurrent} "
        f"harness={'full:' + str(harness_config) if harness_config else 'thin'}"
    )

    def _worker(item: tuple[str, str]) -> dict[str, Any]:
        tid, api_base = item
        print(f"[start] {tid} @ {api_base}")
        res = run_one(
            envs[tid],
            api_base=api_base,
            model=model,
            work_root=args.work_root,
            out_dir=out_dir,
            max_steps=args.max_steps,
            temperature=args.temperature,
            rebuild=args.rebuild_images,
            skip_initial=args.skip_initial,
            keep_container=args.keep_container,
            network=args.network,
            system_prompt=system_prompt,
            harness_config=harness_config,
        )
        print(
            f"[done]  {tid} reward={res.get('reward')} status={res.get('status')} "
            f"in {res.get('elapsed_s')}s"
        )
        return res

    if args.concurrent <= 1:
        for item in pending:
            results.append(_worker(item))
    else:
        with ThreadPoolExecutor(max_workers=args.concurrent) as ex:
            futs = [ex.submit(_worker, item) for item in pending]
            for fut in as_completed(futs):
                with lock:
                    results.append(fut.result())

    # Summary
    by_id = {r["task_id"]: r for r in results}
    ordered = [by_id[t] for t in task_ids if t in by_id]
    passed = sum(1 for r in ordered if r.get("reward") == 1)
    total = len(ordered)
    summary = {
        "job_name": args.job_name,
        "model": model,
        "n_tasks": total,
        "n_passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "tasks_json": str(args.tasks_json),
        "envs_jsonl": str(args.envs_jsonl),
        "by_domain": {},
        "results": [
            {
                "task_id": r["task_id"],
                "reward": r.get("reward"),
                "status": r.get("status"),
                "domain": r.get("domain"),
                "elapsed_s": r.get("elapsed_s"),
                "error": r.get("error"),
            }
            for r in ordered
        ],
    }
    from collections import defaultdict

    dom: dict[str, list[int]] = defaultdict(list)
    for r in ordered:
        dom[str(r.get("domain") or "?")].append(int(r.get("reward") or 0))
    summary["by_domain"] = {
        d: {"passed": sum(v), "total": len(v)} for d, v in sorted(dom.items())
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    # Aggregate result.json in the shape TB2RoundAdapter.read_round_score expects.
    (out_dir / "result.json").write_text(
        json.dumps(
            {
                "stats": {
                    "metrics": [{"mean": summary["pass_rate"]}],
                    "n_passed": passed,
                    "n_tasks": total,
                }
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"\n===== {args.job_name}: {passed}/{total} "
        f"({100*summary['pass_rate']:.1f}%) ====="
    )
    for d, s in summary["by_domain"].items():
        print(f"  {d:24s} {s['passed']}/{s['total']}")
    print(f"summary: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

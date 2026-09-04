# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""TB2 + meta_harness experiment runner.

Workflow:
1) Load task list from a JSON file (list of task name strings).
2) Materialize subset directory as symlinks into r0-dir trial results.
3) Use subset directory directly as trajectory root (no markdown ingestion).
4) Instantiate ``MetaAgent`` once and call ``meta_agent.evolve`` each
   round (recipe-level for-loop).

Example:
python -m recipe.tb2_evolver.run \\
  --r0-dir .benchmarks/tb2/r0-baseline \\
  --tasks recipe/tb2_evolver/tasks.json \\
  --run-tag tb2-evolver-r0-10tasks \\
  --num-rounds 1

# Resume same run-tag and continue one more round with same session_id
python -m recipe.tb2_evolver.run \\
  --run-tag tb2-evolver-r0-10tasks \\
  --resume \\
  --num-rounds 1
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for _line in path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


# Priority: recipe-local .env first, then project-root .env as fallback.
_RECIPE_ENV = Path(__file__).resolve().parent / ".env"
_PROJECT_ENV = Path(_PROJECT_ROOT) / ".env"
_load_env_file(_RECIPE_ENV)
_load_env_file(_PROJECT_ENV)

from harnessx.core.model_config import ModelConfig
from harnessx.meta_harness import MetaAgent
from recipe.tb2_evolver.tb2_trajspec import TB2RoundAdapter, read_per_task_results
from recipe.tb2_evolver.fanout import propose_and_screen
from recipe.tb2_evolver.fanout_tournament import propose_tournament
from recipe.tb2_evolver.fanout_v2 import format_pivot_brief, propose_and_screen_v2
from recipe.tb2_evolver.population import Archive
from recipe.tb2_evolver.tmax_adapter import TmaxRoundAdapter


def _save_state(state_path: Path, state: dict) -> None:
    """Atomic-rename write of the evolve state JSON."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    # Use a unique temp file per write to avoid races when multiple
    # concurrent callbacks save state around the same time.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{state_path.name}.",
        suffix=".tmp",
        dir=state_path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, state_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger("tb2_metav2")

_RECIPE_DIR = Path(__file__).resolve().parent
RUNS_DIR = _RECIPE_DIR / "runs"


def _env_str(name: str, default: str) -> str:
    value = (os.environ.get(name) or "").strip()
    return value if value else default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; fallback to default %s", name, raw, default)
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; fallback to default %s", name, raw, default)
        return int(default)


DEFAULT_META_MODEL = _env_str("META_MODEL", "anthropic/YOUR_PROVIDER/claude-sonnet-4-6")
DEFAULT_PROVIDER_ID = _env_str("PROVIDER_ID", "YOUR_PROVIDER_ID")
DEFAULT_EVOLVE_COST_CAP_USD: "float | None" = (
    None
    if (os.environ.get("EVOLVE_COST_CAP_USD") or "").strip().lower() in ("", "none", "null")
    else _env_float("EVOLVE_COST_CAP_USD", 30.0)
)
DEFAULT_EVOLVE_MAX_STEPS = _env_int("EVOLVE_MAX_STEPS", 200)
DEFAULT_EVOLVE_EARLY_REMINDER_STEP = _env_int("EVOLVE_EARLY_REMINDER_STEP", 80)
DEFAULT_EVOLVE_REMINDER_STEP = _env_int("EVOLVE_REMINDER_STEP", 120)
DEFAULT_EVOLVE_WALL_CLOCK_S = _env_int("EVOLVE_WALL_CLOCK_S", 3600)
DEFAULT_TASK_TIMEOUT = _env_int("TB2_TASK_TIMEOUT", 900)


def _make_provider(model: str, provider_id: str = None):
    from harnessx.providers.anthropic_provider import AnthropicProvider
    from harnessx.providers.litellm_provider import LiteLLMProvider

    if model.startswith("anthropic/"):
        model_name = model[len("anthropic/") :]
        return AnthropicProvider(
            model=model_name,
            base_url=os.environ.get("ANTHROPIC_API_BASE"),
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )

    # AWS Bedrock (``bedrock/us.anthropic.claude-opus-4-8``, ``bedrock/qwen...``).
    # Auth is SigV4 from the ambient credential chain — on this cluster the
    # SageMaker execution role — so there is no API key to pass, and none of the
    # gateway plumbing below applies:
    #   * X-Api-Key / X-Model-Provider-Id are Salesforce-Gateway-specific headers.
    #     Bedrock ignores unknown headers at best; at worst injecting them
    #     perturbs the signed request. Send neither.
    #   * an explicit api_key would override litellm's SigV4 resolution and 401.
    # Region comes from AWS_REGION_NAME (litellm's own name for it), falling back
    # to the standard AWS_REGION/AWS_DEFAULT_REGION, then us-west-2.
    if model.startswith("bedrock/"):
        region = (
            os.environ.get("AWS_REGION_NAME")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-west-2"
        )
        bedrock_kwargs: dict = {"aws_region_name": region}
        meta_reasoning_effort = (os.environ.get("META_REASONING_EFFORT") or "").strip()
        if meta_reasoning_effort:
            bedrock_kwargs["reasoning_effort"] = meta_reasoning_effort
        return LiteLLMProvider(model, **bedrock_kwargs)

    extra_headers: dict[str, str] = {}
    if provider_id:
        extra_headers["X-Model-Provider-Id"] = provider_id
    # Salesforce Gateway often accepts/needs X-Api-Key.
    sfg_api_key = (os.environ.get("SFG_API_KEY") or os.environ.get("GATEWAY_API_KEY") or "").strip()
    if sfg_api_key:
        extra_headers.setdefault("X-Api-Key", sfg_api_key)

    provider_kwargs: dict = {"extra_headers": extra_headers or None}
    # GPT/o-series style reasoning knob exposed by many OpenAI-compatible gateways.
    meta_reasoning_effort = (os.environ.get("META_REASONING_EFFORT") or "").strip()
    if meta_reasoning_effort:
        provider_kwargs["reasoning_effort"] = meta_reasoning_effort
    # Pass api_key explicitly rather than relying on litellm's own OPENAI_API_KEY
    # env-var auto-detection. Without this, spawned sub-agents (e.g. the
    # meta-harness's trajectory-digester worker, which inherits this same
    # provider instance when it doesn't override `model`) have hit
    # "OpenAIException - Missing credentials" even though the parent's own
    # calls succeeded moments earlier — an explicit api_key removes that
    # dependency on litellm re-resolving the env var correctly in whatever
    # nested async context the sub-agent call runs in.
    #
    # Scope it to OpenAI-compatible models only. An explicit api_key overrides
    # litellm's own per-provider resolution, so applying it unconditionally
    # would send the OpenAI/SF-gateway key as the credential for e.g.
    # `gemini/...` or `deepseek/...` and 401 — models that worked fine before,
    # because litellm resolved GEMINI_API_KEY/DEEPSEEK_API_KEY itself.
    prefix = (model.split("/", 1)[0] if "/" in model else "").lower()
    if prefix in ("", "openai", "azure"):
        openai_api_key = (os.environ.get("OPENAI_API_KEY") or sfg_api_key or "").strip()
        if openai_api_key:
            provider_kwargs["api_key"] = openai_api_key
    return LiteLLMProvider(model, **provider_kwargs)


def _env_is_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _auth_preflight(model: str, provider_id: str | None) -> None:
    """Fail fast with actionable guidance when model auth is clearly missing."""
    found: list[str] = []

    def _check_any(vars_: list[str]) -> bool:
        nonlocal found
        matched = [k for k in vars_ if _env_is_set(k)]
        found.extend(matched)
        return bool(matched)

    if model.startswith("anthropic/"):
        required = ["ANTHROPIC_API_KEY"]
        if not _check_any(required):
            raise RuntimeError(
                "Anthropic provider selected but auth is missing.\n"
                f"- model: {model}\n"
                f"- checked env: {', '.join(required)}\n"
                "Set `ANTHROPIC_API_KEY` (gateway keys are fine if your base_url routes through a proxy)."
            )
    elif model.startswith("bedrock/"):
        # Bedrock authenticates with SigV4 from the ambient credential chain
        # (env vars, ~/.aws, or an instance/execution role), so scanning for a
        # *_API_KEY env var proves nothing — the generic branch below would
        # reject a perfectly working setup, or pass one that has no credentials
        # at all just because some unrelated key happens to be exported.
        # Resolve real credentials instead, which is the thing that matters.
        region = (
            os.environ.get("AWS_REGION_NAME")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-west-2"
        )
        try:
            import botocore.session

            creds = botocore.session.get_session().get_credentials()
        except Exception as exc:  # pragma: no cover - botocore always present with litellm
            raise RuntimeError(
                f"Bedrock model selected ({model}) but botocore is unavailable: {exc}"
            ) from exc
        if creds is None:
            raise RuntimeError(
                "Bedrock provider selected but no AWS credentials could be resolved.\n"
                f"- model: {model}\n"
                f"- region: {region}\n"
                "Bedrock uses SigV4, not an API key. Provide credentials via an\n"
                "instance/execution role, ~/.aws/credentials, or AWS_ACCESS_KEY_ID +\n"
                "AWS_SECRET_ACCESS_KEY, then re-run.\n"
                "Verify with: python -c \"import boto3;"
                "print(boto3.client('sts', region_name='" + region + "').get_caller_identity())\""
            )
        logger.info(
            "Bedrock auth preflight OK: model=%s region=%s credential_method=%s",
            model,
            region,
            getattr(creds, "method", "unknown"),
        )
    else:
        prefix = (model.split("/", 1)[0] if "/" in model else "").lower()
        prefix_candidates: dict[str, list[str]] = {
            "openai": ["OPENAI_API_KEY", "SFG_API_KEY", "GATEWAY_API_KEY"],
            "deepseek": ["DEEPSEEK_API_KEY"],
            "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
            "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        }
        provider_vars: list[str] = []
        if provider_id:
            provider_vars.append(f"{provider_id.upper()}_API_KEY")
        generic = [
            "LITELLM_API_KEY",
            "OPENAI_API_KEY",
            "SFG_API_KEY",
            "GATEWAY_API_KEY",
            "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "TOGETHER_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ]
        candidates = provider_vars + prefix_candidates.get(prefix, []) + generic
        # Deduplicate while preserving order for readable error text.
        deduped: list[str] = []
        seen: set[str] = set()
        for k in candidates:
            if k and k not in seen:
                seen.add(k)
                deduped.append(k)
        if not _check_any(deduped):
            provider_hint = f"\n- provider-id hint: set `{provider_id.upper()}_API_KEY`" if provider_id else ""
            raise RuntimeError(
                "LiteLLM provider selected but no obvious auth env var is set.\n"
                f"- model: {model}\n"
                f"- checked env: {', '.join(deduped)}"
                f"{provider_hint}\n"
                "Set one credential env var before running."
            )

    if found:
        logger.info(
            "Auth preflight passed for model=%s (detected: %s)",
            model,
            ", ".join(dict.fromkeys(found)),
        )


def _dump_baseline_config(round_dir: Path, timeout_seconds: int = 900) -> Path:
    from benchmarks.terminal_bench_2.harness import make_tb2_harness_config

    cfg = make_tb2_harness_config(timeout_seconds=timeout_seconds)
    config_path = round_dir / "config.yaml"
    cfg.to_yaml_file(config_path)
    logger.info("Dumped baseline config → %s", config_path)
    return config_path


def _reward_of_trial_dir(trial_dir: Path) -> float | None:
    rp = trial_dir / "result.json"
    if not rp.is_file():
        return None
    try:
        result = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return None
    vr = result.get("verifier_result") or {}
    rw = (vr.get("rewards") or {}).get("reward")
    if isinstance(rw, (int, float)):
        return float(rw)
    return None


def _load_trials_from_tasks_json(r0_dir: Path, tasks_json: Path) -> list[Path]:
    """Return sorted trial dirs from r0_dir matching the task names in tasks_json."""
    task_names: list[str] = json.loads(tasks_json.read_text(encoding="utf-8"))
    if not isinstance(task_names, list) or not task_names:
        raise ValueError(f"--tasks file must be a non-empty JSON list: {tasks_json}")

    # Build a map from task_name -> trial dir (prefer result.json task_name field,
    # fall back to directory name prefix before the first '__').
    name_to_dir: dict[str, Path] = {}
    for p in r0_dir.iterdir():
        if not p.is_dir():
            continue
        rp = p / "result.json"
        if rp.is_file():
            try:
                task_name = json.loads(rp.read_text(encoding="utf-8")).get("task_name") or ""
            except Exception:
                task_name = ""
        else:
            task_name = p.name.split("__")[0]
        if task_name:
            name_to_dir[task_name] = p

    picked: list[Path] = []
    missing: list[str] = []
    for name in task_names:
        if name in name_to_dir:
            picked.append(name_to_dir[name])
        else:
            missing.append(name)
    if missing:
        raise RuntimeError(f"Tasks not found in {r0_dir}: {missing}\nAvailable: {sorted(name_to_dir)}")
    return sorted(picked, key=lambda p: p.name)


def _print_eval_summary(eval_records: list[dict], title: str) -> None:
    passed = sum(1 for r in eval_records if r.get("reward") and r["reward"] > 0)
    total = len(eval_records)
    print(f"\n{title}: {passed}/{total} passed ({100 * passed / total:.1f}%)")
    print()
    print(f"  {'task_name':<45} {'reward':>6}  {'elapsed':>8}  {'tokens':>8}")
    print("  " + "-" * 72)
    for r in eval_records:
        status = "PASS" if r.get("reward") and r["reward"] > 0 else "FAIL"
        rew = f"{r.get('reward', '?')}"
        elapsed = f"{r.get('elapsed_s', '?')}s"
        tokens = f"{r.get('total_tokens', '?'):,}" if r.get("total_tokens") else "?"
        print(f"  [{status}] {r['task_name']:<41} {rew:>6}  {elapsed:>8}  {tokens:>8}")


def _parse_iso_utc(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _collect_eval_records_from_trials(trials: list[Path]) -> list[dict]:
    records: list[dict] = []
    for t in trials:
        rp = t / "result.json"
        reward = 0.0
        elapsed_s: float | None = None
        total_tokens: int | None = None
        task_name = t.name
        if rp.is_file():
            try:
                obj = json.loads(rp.read_text(encoding="utf-8"))
                task_name = str(obj.get("task_name") or t.name)
                vr = obj.get("verifier_result") or {}
                reward_raw = (vr.get("rewards") or {}).get("reward")
                if isinstance(reward_raw, (int, float)):
                    reward = float(reward_raw)
                ts0 = _parse_iso_utc(obj.get("started_at"))
                ts1 = _parse_iso_utc(obj.get("finished_at"))
                if ts0 is not None and ts1 is not None and ts1 >= ts0:
                    elapsed_s = round(ts1 - ts0, 1)
                ar = obj.get("agent_result") or {}
                ni = ar.get("n_input_tokens")
                no = ar.get("n_output_tokens")
                if isinstance(ni, int) and isinstance(no, int):
                    total_tokens = ni + no
            except Exception:
                pass
        records.append(
            {
                "task_name": task_name,
                "reward": reward,
                "elapsed_s": elapsed_s,
                "total_tokens": total_tokens,
            }
        )
    return records


def _nodes_to_pivot_dicts(nodes, task_universe: list[str], incumbent) -> list[dict]:
    inc_solved = set(incumbent.solved or []) if incumbent is not None else set()
    out: list[dict] = []
    for n in nodes:
        solved = list(n.solved or [])
        sset = set(solved)
        out.append({
            "id": n.id,
            "config": n.config,
            "trajectories": n.trajectories,
            "score": n.mean_score,
            "parent_results": {t: t in sset for t in task_universe},
            "unique_solves": sorted(sset - inc_solved)[:12],
            "unique_losses": sorted(inc_solved - sset)[:12],
        })
    return out


async def _full_eval_survivors(
    *,
    survivors: list,
    archive,
    adapter,
    run_root: Path,
    input_round: int,
    task_names: list[str],
    parent_solved: list[str] | None = None,
    tie_eps: float = 0.0,
    force_eval: bool = False,
    eval_concurrent: int = 0,
    job_suffix_traj: bool = False,
    min_unique_pivot: int = 1,
):
    """Full-eval the screened survivors and return (primary, kept_pivots).

    With a single survivor this is a NO-OP: the loop re-evaluates whatever it
    promotes at the top of the next round anyway, so measuring here would buy
    the same number for twice the GPU. Tournament mode sets ``force_eval`` so
    every candidate (even a lone survivor) is measured here — those trajectories
    are the SFT harvest.

    With k > 1 the evaluations run CONCURRENTLY under distinct job suffixes.
    Ties and unique-gain children are all kept as pivots even when they do not
    beat the primary on raw pass rate.
    """
    if len(survivors) == 1 and not force_eval:
        logger.info("[R%d] single survivor — deferring its eval to the next round", input_round)
        return survivors[0], list(survivors)

    sem = asyncio.Semaphore(max(1, int(eval_concurrent))) if eval_concurrent else None

    async def _one(cand):
        suffix = f"fe-c{cand.idx}-traj" if job_suffix_traj else f"fe-c{cand.idx}"

        async def _run():
            traj = await adapter.resolve_trajectories_for_round(
                run_root=run_root,
                input_round=input_round,
                current_config=Path(cand.config),
                task_names_override=list(task_names),
                job_suffix=suffix,
            )
            return cand, read_per_task_results(traj), traj

        if sem is None:
            return await _run()
        async with sem:
            return await _run()

    outcomes = await asyncio.gather(
        *(_one(c) for c in survivors), return_exceptions=True
    )

    parent_set = set(parent_solved or [])
    scored: list[tuple[float, object]] = []
    for item in outcomes:
        if isinstance(item, BaseException):
            logger.warning("[R%d] survivor full-eval raised: %s", input_round, item)
            continue
        cand, results, traj = item
        solved = [t for t in task_names if results.get(t)]
        score = (len(solved) / len(task_names)) if task_names else 0.0
        cand.full_solved = solved
        cand.full_score = score
        cand.trajectories_dir = Path(traj)
        if cand.node is not None:
            archive.record_score(cand.node.id, score, solved, trajectories=str(traj))
        logger.info(
            "[R%d] survivor c%d full-eval %.4f (%d/%d) unique=+%d",
            input_round, cand.idx, score, len(solved), len(task_names),
            len(set(solved) - parent_set),
        )
        scored.append((score, cand))

    if not scored:
        logger.warning("[R%d] all survivor evals failed — falling back to screen order",
                       input_round)
        return survivors[0], list(survivors)

    def _candidate_parent_set(cand) -> set[str]:
        own = getattr(cand, "parent_results", None) or {}
        return {t for t, ok in own.items() if ok} if own else parent_set

    def _unique(cand) -> int:
        return len(
            set(getattr(cand, "full_solved", None) or [])
            - _candidate_parent_set(cand)
        )

    scored.sort(key=lambda t: (-t[0], -_unique(t[1]), t[1].idx))
    best_score, primary = scored[0]
    kept = []
    seen = set()
    for score, cand in scored:
        unique = (
            set(getattr(cand, "full_solved", None) or [])
            - _candidate_parent_set(cand)
        )
        tied = score >= best_score - max(0.0, float(tie_eps))
        if tied or len(unique) >= max(1, int(min_unique_pivot)):
            if cand.idx not in seen:
                kept.append(cand)
                seen.add(cand.idx)
    if primary not in kept:
        kept.insert(0, primary)
    if primary.node is not None:
        archive.mark(primary.node.id, "scored")
    logger.info(
        "[R%d] fan-out primary: c%d @ %.4f; pivots=%s",
        input_round, primary.idx, best_score,
        ", ".join(f"c{c.idx}" for c in kept),
    )
    return primary, kept


def _score_and_gate_tb2(
    *,
    round_idx: int,
    round_score: float | None,
    round_config,
    best: tuple[float, object, int] | None,
    tolerance: float,
    incumbent_mean: float | None = None,
    unique_gained: Sequence[str] | None = None,
) -> tuple[str, str, tuple | None, object | None]:
    """Gate an *evaluated* TB2 config against the historical best pass rate.

    ``round_score`` must belong to ``round_config`` (the config that produced the
    trajectories just scored). Do **not** pass an unevaluated promoted successor.

    Mirrors ``recipe.tau2_evolver.run._score_and_gate``. Returns
    ``(decision, reason, new_best, reverted_config_or_None)`` where ``decision`` is
    ``"accept"`` | ``"reject"`` | ``"skip"``.

    ``tolerance`` is in pass-rate units (0.0667 ~= 1 task of 15). A negative tolerance
    disables gating entirely, restoring unconditional promotion.
    """
    if tolerance < 0:
        return "skip", "gating disabled (tolerance < 0)", best, None
    if round_score is None:
        return "skip", "no score available for this round — promoting unchanged", best, None
    if best is None:
        return "accept", "first scored round — establishing baseline", (round_score, round_config, round_idx), None

    best_score, best_cfg, best_idx = best
    # Compare against the incumbent's MEAN over every time it was measured, not
    # the single draw that happened to crown it. `best_so_far` is a max over noisy
    # rounds, so its stored score is biased high by about one sd; using it as the
    # bar rejected re-measurements of the incumbent itself (17 -> then 14 and 11
    # for one byte-identical config). The mean is unbiased and costs nothing —
    # the repeats are already in the state file.
    bar = incumbent_mean if incumbent_mean is not None else best_score
    basis = "mean" if incumbent_mean is not None else "single"
    if round_score >= bar - tolerance:
        # Promote on the same basis: a candidate becomes the incumbent only when
        # it beats the incumbent's mean, so one lucky draw cannot seize the crown
        # and lock out everything that follows. A TIE is still taken: the config
        # stays in the lineage (not reverted) even if it does not replace best_so_far.
        new_best = (round_score, round_config, round_idx) if round_score > bar else best
        return (
            "accept",
            f"score {round_score:.4f} >= incumbent({basis}) {bar:.4f} - tol {tolerance:.4f}",
            new_best,
            None,
        )
    gained = [t for t in (unique_gained or []) if t]
    if gained:
        return (
            "accept",
            f"score {round_score:.4f} < incumbent({basis}) {bar:.4f} - tol {tolerance:.4f} "
            f"but unique solves {','.join(gained[:4])} — keep as pivot, incumbent unchanged",
            best,
            None,
        )
    return (
        "reject",
        f"score {round_score:.4f} < incumbent({basis}) {bar:.4f} - tol {tolerance:.4f} "
        f"-> revert to R{best_idx}",
        best,
        best_cfg,
    )


# ---------------------------------------------------------------------------
# Closing the evolve feedback loop
#
# Three defects, all of which let the loop degrade into a random walk, are
# addressed by the helpers below. They were found by re-reading four real rounds
# on three chains:
#
# 1. Winner's curse. `best_score` was a single noisy draw, and `best_so_far` is
#    the max over rounds — biased upward by roughly one sd. Measured directly:
#    one identical config scored 17, 14, 11 in three rounds of the same run
#    (sd 3.0, range 6) while the gate tolerance was 1.87 tasks. The lucky first
#    draw of 17 then rejected every later measurement of that very same config.
#    Fix: compare against the MEAN of every measurement of the incumbent.
#
# 2. Open loop. `learnings.md` carries `gating_outcome:`/`gating_attribution:`
#    fields and the meta-agent is shown a "lever scoreboard" built from them —
#    but nothing ever wrote them, so every entry read `pending` and the whole
#    scoreboard was empty. The agent could not tell which of its own edits had
#    ever worked. Fix: write the gate's verdict back into the journal entry.
#
# 3. Promised-but-absent context. TASK.md tells the agent to read
#    `history/OVERVIEW.md`, `history/R{i}_per_task.json` and `R{i}_to_R{j}.diff`.
#    None of those files were ever produced. Fix: emit them.
# ---------------------------------------------------------------------------

_FILE_URI_RE = re.compile(r"file://(/[^\s'\"]+?\.(?:py|j2|jinja|jinja2|txt|md))")


def _copy_sibling_system_prompt(src_config: Path, dest_dir: Path) -> str | None:
    """Copy ``system_prompt.txt`` sitting next to *src_config* into *dest_dir*.

    The Tmax eval prompt builder reads the sibling of the *active* YAML, not a
    ``file://`` URI inside it. Copying only the YAML (or only ``file://``
    processor assets) therefore reseeds the next iteration with the stock
    5-line prompt even when the previous round's winner edited the prompt.
    Returns the destination path when a sibling was copied, else None.
    """
    src = Path(src_config).expanduser().resolve().parent / "system_prompt.txt"
    if not src.is_file():
        return None
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "system_prompt.txt"
    shutil.copy2(src, dest)
    return str(dest)


def _materialize_config_bundle(src_config: Path, dest_config: Path, assets_dir: Path) -> list[str]:
    """Copy *src_config* to *dest_config* with every local asset it references
    copied alongside it, and the `file://` URIs rewritten to the copies.

    An evolved harness is not one file. When the meta-agent writes a processor it
    emits `_target_: file:///…/runs/<iter-1>/_meta_v2/R1/processors/guard.py::Guard`
    — an absolute path into the iteration that produced it. Seeding the next
    iteration by copying only the YAML therefore produces a config that silently
    depends on the previous iteration's directory: archiving or cleaning that
    iteration breaks the running harness, and any further edit to the processor
    mutates an iteration whose results are already recorded.

    Materializing the bundle makes each iteration's starting harness stand on its
    own, which is what "iteration k+1 continues from iteration k's best harness"
    has to mean if the chain is to survive housekeeping.
    """
    text = src_config.read_text(encoding="utf-8")
    copied: list[str] = []
    seen: dict[str, str] = {}

    def _sub(m: re.Match) -> str:
        path = m.group(1)
        if path in seen:
            return f"file://{seen[path]}"
        p = Path(path)
        if not p.is_file():
            # Leave unresolvable references untouched: rewriting them to a
            # non-existent copy would turn a loud startup error into a confusing
            # one, and canonicalize will catch it either way.
            return m.group(0)
        assets_dir.mkdir(parents=True, exist_ok=True)
        # Keep the last directory component so `processors/x.py` and
        # `templates/x.j2` do not collide on basename alone.
        dest = assets_dir / p.parent.name / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        seen[path] = str(dest.resolve())
        copied.append(f"{p.parent.name}/{p.name}")
        return f"file://{dest.resolve()}"

    dest_config.parent.mkdir(parents=True, exist_ok=True)
    dest_config.write_text(_FILE_URI_RE.sub(_sub, text), encoding="utf-8")
    if _copy_sibling_system_prompt(src_config, dest_config.parent):
        copied.append("system_prompt.txt")
    return copied


def _config_digest(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return "unknown"


def _record_config_score(state: dict, config_path, score: float | None) -> None:
    """Append one measurement to this config's score list."""
    if score is None:
        return
    scores = state.setdefault("config_scores", {})
    scores.setdefault(_config_digest(config_path), []).append(float(score))


def _mean_score_for(state: dict, config_path) -> float | None:
    vals = (state.get("config_scores") or {}).get(_config_digest(config_path))
    return (sum(vals) / len(vals)) if vals else None


def _update_journal_outcome(memo_path, round_no: int, outcome: str, attribution: str) -> bool:
    """Fill in `gating_outcome:`/`gating_attribution:` for one journal round.

    The journal is the meta-agent's only cross-round memory, and its frontmatter
    is machine-read to build the lever scoreboard. Rewriting in place (rather than
    appending a note) is what makes the scoreboard populate.
    """
    p = Path(memo_path)
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return False
    # Scope the edit to this round's frontmatter block: the fields repeat once
    # per round, so an unscoped replace would rewrite every earlier verdict too.
    m = re.search(rf"(<!--\s*journal:frontmatter\s*\nround:\s*{round_no}\b.*?-->)", text, re.S)
    if not m:
        return False
    block = m.group(1)
    new = re.sub(r"gating_outcome:\s*\S+", f"gating_outcome: {outcome}", block)
    new = re.sub(r"gating_attribution:\s*.*", f"gating_attribution: {attribution}", new)
    if new == block:
        return False
    try:
        p.write_text(text[: m.start(1)] + new + text[m.end(1) :], encoding="utf-8")
    except Exception:
        return False
    return True


def _write_history_context(
    scratch: Path,
    *,
    state: dict,
    run_root: Path,
    task_names: list[str],
) -> None:
    """Emit the `history/` files TASK.md tells the agent to read."""
    hist_dir = scratch / "history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    rounds = state.get("history") or []
    per_round = state.get("per_round_task_results") or {}

    lines = ["# Round-by-round history", ""]
    lines.append("Scores are pass counts on the same task set, measured once per round.")
    lines.append("The same config measured twice can differ by several tasks — treat a")
    lines.append("difference smaller than the spread of `repeats` as noise, not signal.")
    lines.append("")
    lines.append("| Round | Config | Score | Repeats of this config | Gate | Changed |")
    lines.append("|------:|--------|------:|-----------------------|------|---------|")
    cfg_scores = state.get("config_scores") or {}
    for h in rounds:
        cfg = h.get("input_config") or ""
        dig = _config_digest(cfg) if cfg else "-"
        reps = cfg_scores.get(dig) or []
        sc = h.get("score")
        n = round(sc * len(task_names)) if isinstance(sc, (int, float)) else "?"
        reps_txt = ", ".join(str(round(v * len(task_names))) for v in reps) or "-"
        lines.append(
            f"| R{h.get('input_round')} | `{dig}` | {n}/{len(task_names)} | {reps_txt} | "
            f"{h.get('gate_decision', '-')} | {'yes' if h.get('changed') else 'no'} |"
        )
    lines.append("")
    bsf = state.get("best_so_far") or {}
    if bsf:
        lines.append(f"Incumbent: R{bsf.get('round')} (gate compares against the MEAN of its "
                     f"repeats, not its best single draw).")
    (hist_dir / "OVERVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    for rnd, results in per_round.items():
        try:
            (hist_dir / f"R{rnd}_per_task.json").write_text(
                json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            pass

    for h in rounds:
        i, j = h.get("input_round"), h.get("output_round")
        a, b = run_root / f"R{i}" / "config.yaml", run_root / f"R{j}" / "config.yaml"
        if not (a.is_file() and b.is_file()):
            continue
        try:
            diff = "\n".join(difflib.unified_diff(
                a.read_text(encoding="utf-8").splitlines(),
                b.read_text(encoding="utf-8").splitlines(),
                fromfile=f"R{i}", tofile=f"R{j}", lineterm="", n=1))
            (hist_dir / f"R{i}_to_R{j}.diff").write_text(diff or "(no change)\n", encoding="utf-8")
        except Exception:
            pass


def _best_to_state_dict(best: tuple[float, object, int] | None) -> dict | None:
    if best is None:
        return None
    score, cfg, idx = best
    return {"score": float(score), "config": str(cfg), "round": int(idx)}


def _best_from_state(state: dict) -> tuple[float, Path, int] | None:
    """Restore best-so-far across ``--resume`` so gating does not re-baseline.

    Prefers the explicit ``best_so_far`` field. Falls back to reconstructing from
    history entries that recorded a score for the *evaluated* (input) config.
    """
    bsf = state.get("best_so_far")
    if isinstance(bsf, dict) and bsf.get("config") is not None and bsf.get("score") is not None:
        cfg = Path(str(bsf["config"]))
        if cfg.is_file():
            return (float(bsf["score"]), cfg.resolve(), int(bsf.get("round", -1)))

    best: tuple[float, Path, int] | None = None
    for rr in state.get("history") or []:
        if not isinstance(rr, dict):
            continue
        score = rr.get("score")
        if score is None:
            continue
        # Prefer the config that was actually gated/evaluated.
        cfg_s = rr.get("gated_config") or rr.get("input_config")
        idx = rr.get("input_round")
        if cfg_s is None or idx is None:
            continue
        cfg = Path(str(cfg_s))
        if not cfg.is_file():
            continue
        cand = (float(score), cfg.resolve(), int(idx))
        if best is None or cand[0] > best[0]:
            best = cand
    return best


def _copy_config_as_round(*, run_root: Path, output_round: int, config: Path) -> Path:
    """Install ``config`` as ``R{output_round}/config.yaml`` without a meta evolve."""
    config = Path(config).resolve()
    if not config.is_file():
        raise FileNotFoundError(f"config to copy not found: {config}")
    round_dir = run_root / f"R{output_round}"
    round_dir.mkdir(parents=True, exist_ok=True)
    promoted = round_dir / "config.yaml"
    shutil.copy2(config, promoted)
    _copy_sibling_system_prompt(config, round_dir)
    evolve_dir = round_dir / "evolve"
    evolve_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, evolve_dir / "config.yaml")
    _copy_sibling_system_prompt(config, evolve_dir)
    return promoted.resolve()


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="TB2 subset evolve with meta_harness (v1)",
    )
    parser.add_argument(
        "--r0-dir",
        default=None,
        help=(
            "Source TB2 run directory with prior trajectories. "
            "Required for --trajectory-mode reuse. "
            "Optional for rerun: if provided, warm-starts the R0 manifest with pass/fail info; "
            "if omitted, R0 runs fresh with the baseline config."
        ),
    )
    parser.add_argument(
        "--skip-final-score",
        action="store_true",
        help=(
            "Do not roll out the last round's promoted config. Off by default: "
            "without this scoring, the final meta-agent edit of every run ships "
            "unmeasured."
        ),
    )
    parser.add_argument(
        "--baseline-config",
        default=None,
        help=(
            "Seed R0's harness from this YAML instead of the stock config. Used to "
            "chain evolve iterations: pass iteration k's gated best_so_far config so "
            "iteration k+1 evolves on top of it rather than from scratch."
        ),
    )
    parser.add_argument(
        "--run-tag",
        default=None,
        help="Output tag under recipe/terminal_bench_2_with_metav2/runs/",
    )
    parser.add_argument(
        "--eval-backend",
        choices=("tb2", "tmax"),
        default=os.environ.get("EVOLVE_EVAL_BACKEND", "tb2"),
        help="Rollout backend: tb2 (Harbor) or tmax (Docker taxonomy tasks)",
    )
    parser.add_argument(
        "--tmax-envs-jsonl",
        default=None,
        help="Required when --eval-backend tmax: per-task env jsonl with container_def/tests",
    )
    parser.add_argument(
        "--tasks",
        default=str(_RECIPE_DIR / "tasks.json"),
        help="JSON file containing a list of task name strings (default: recipe dir tasks.json)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_META_MODEL,
        help=f"Meta-agent model (default: {DEFAULT_META_MODEL})",
    )
    parser.add_argument(
        "--provider-id",
        default=DEFAULT_PROVIDER_ID,
        help=f"Provider ID for non-Anthropic models (default: {DEFAULT_PROVIDER_ID})",
    )
    parser.add_argument(
        "--evolve-cost",
        type=lambda x: None if x.lower() in ("none", "null", "") else float(x),
        default=DEFAULT_EVOLVE_COST_CAP_USD,
        help=f"Meta-agent cost cap USD, or 'none' to disable (default: {DEFAULT_EVOLVE_COST_CAP_USD})",
    )
    parser.add_argument(
        "--evolve-steps",
        type=int,
        default=DEFAULT_EVOLVE_MAX_STEPS,
        help=f"Meta-agent step cap (default: {DEFAULT_EVOLVE_MAX_STEPS})",
    )
    parser.add_argument(
        "--evolve-early-reminder",
        type=int,
        default=DEFAULT_EVOLVE_EARLY_REMINDER_STEP,
        help=f"Step at which to inject soft convergence warning (default: {DEFAULT_EVOLVE_EARLY_REMINDER_STEP})",
    )
    parser.add_argument(
        "--evolve-reminder",
        type=int,
        default=DEFAULT_EVOLVE_REMINDER_STEP,
        help=f"Step at which to inject hard deadline reminder (default: {DEFAULT_EVOLVE_REMINDER_STEP})",
    )
    parser.add_argument(
        "--evolve-wall-clock",
        type=int,
        default=DEFAULT_EVOLVE_WALL_CLOCK_S,
        help=f"Meta-agent wall-clock cap in seconds (default: {DEFAULT_EVOLVE_WALL_CLOCK_S})",
    )
    parser.add_argument(
        "--noop-on-meta-fail",
        action=argparse.BooleanOptionalAction,
        default=_env_str("EVOLVE_NOOP_ON_META_FAIL", "0") not in {"0", "false", "False", ""},
        help=(
            "On meta-agent timeout / missing config.yaml, promote a byte-copy of the "
            "incumbent config and continue instead of failing the whole run "
            "(env EVOLVE_NOOP_ON_META_FAIL)."
        ),
    )
    parser.add_argument(
        "--no-require-evidence",
        action="store_true",
        default=False,
        help="Skip the evidence gate (candidates.md not required even when config changes)",
    )
    parser.add_argument(
        "--task-timeout",
        type=int,
        default=DEFAULT_TASK_TIMEOUT,
        help=f"TB2 task timeout used by dumped baseline config (default: {DEFAULT_TASK_TIMEOUT})",
    )
    parser.add_argument(
        "--num-rounds",
        type=int,
        default=1,
        help="How many evolve rounds to execute in this invocation (default: 1)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from previous run-tag evolve state, keeping the same session continuity.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional explicit meta-agent session_id. Default: tb2-metav2:{run_tag}",
    )
    parser.add_argument(
        "--start-round",
        type=int,
        default=None,
        help="Optional start input round index when not resuming (default: 0).",
    )
    parser.add_argument(
        "--allow-round-fallback",
        action="store_true",
        help=(
            "Allow fallback to older trajectories when R{n}/trajectories is missing. "
            "Default is strict (missing round trajectories raises an error)."
        ),
    )
    parser.add_argument(
        "--trajectory-mode",
        choices=("rerun", "reuse"),
        default="rerun",
        help=(
            "How to provide trajectories for R1+ rounds. "
            "`rerun` = run TB2 with each new config to generate fresh trajectories "
            "(true evolution). `reuse` = keep using existing provided trajectories."
        ),
    )
    parser.add_argument(
        "--tb2-eval-script",
        default=str((Path(_PROJECT_ROOT) / "benchmarks/terminal_bench_2/scripts/eval_opensandbox.sh").resolve()),
        help="Path to TB2 eval script used when --trajectory-mode=rerun.",
    )
    parser.add_argument(
        "--tb2-eval-concurrent",
        type=int,
        default=2,
        help="TB2 eval concurrency used when --trajectory-mode=rerun (default: 2).",
    )
    parser.add_argument(
        "--regression-tolerance",
        type=float,
        default=1.0 / 15.0,
        help=(
            "Pass-rate drop tolerated before an evolved config is rejected and the "
            "best-so-far config restored. Default 1/15 (one task on sample15). "
            "Negative disables gating (legacy always-promote behaviour)."
        ),
    )
    parser.add_argument(
        "--tb2-eval-resume",
        action="store_true",
        help="Pass --resume to TB2 eval script when --trajectory-mode=rerun.",
    )
    parser.add_argument(
        "--tb2-max-steps",
        type=int,
        default=None,
        help=(
            "Per-task RunLoop step budget for every round's rollout. Must match the "
            "budget used for the R0 baseline, otherwise the regression gate compares "
            "rounds run under different budgets (a 120-step R0 against a 500-step R1 "
            "credits the step budget to the evolved harness). Unset = the eval "
            "script's own default."
        ),
    )
    parser.add_argument(
        "--tb2-endpoints-file",
        default=os.environ.get("TB2_ENDPOINTS_FILE"),
        help=(
            "JSON file with a list of api_base URLs, one per model-serving GPU "
            "replica. When set, each round's TB2 rollout is dynamically sharded "
            "across all endpoints via a shared claim queue instead of a single "
            "server (default: env TB2_ENDPOINTS_FILE)."
        ),
    )
    parser.add_argument(
        "--adaptive-subset",
        action="store_true",
        default=False,
        help=(
            "From R1 onward, only re-run tasks that failed the previous round "
            "plus a small canary sample of previously-passed tasks, instead of "
            "the full task set every round. Requires --trajectory-mode rerun. "
            "The gating score still covers the full task universe: results for "
            "tasks not re-tested this round are carried forward from their last "
            "known outcome."
        ),
    )
    parser.add_argument(
        "--adaptive-canary-size",
        type=int,
        default=2,
        help="Previously-passed tasks to re-verify each adaptive-subset round, to catch "
        "regressions in carried-forward results (default: 2).",
    )
    # ── fan-out / screening (population evolve) ──────────────────────────
    # Default --fanout 1 reproduces the legacy (1+1) hill-climb byte-for-byte,
    # so an unchanged command line behaves exactly as it did before.
    parser.add_argument(
        "--fanout",
        type=int,
        default=1,
        help=(
            "Harness proposals to generate per round. 1 (default) is the legacy "
            "single-candidate hill-climb. >1 fans out that many meta-agent calls, "
            "each assigned a different focus, then screens them down to "
            "--fanout-keep before any full eval."
        ),
    )
    parser.add_argument(
        "--fanout-keep",
        type=int,
        default=2,
        help="Candidates surviving the screens and reaching full eval (default: 2). "
        "Survivors are full-evaluated concurrently, so k>1 costs ~1 eval of wall-clock.",
    )
    parser.add_argument(
        "--fanout-concurrent",
        type=int,
        default=4,
        help="Max concurrent meta-agent proposals (default: 4). Each holds a live "
        "harness with its own sandbox, so this is a local-resource cap.",
    )
    parser.add_argument(
        "--no-screen-llm",
        action="store_true",
        default=False,
        help="Skip the LLM predicted-impact ranking screen.",
    )
    parser.add_argument(
        "--no-screen-probe",
        action="store_true",
        default=False,
        help="Skip the mini-eval probe screen (the only screen that MEASURES "
        "regressions; skipping it makes selection purely predictive).",
    )
    parser.add_argument(
        "--probe-solved",
        type=int,
        default=2,
        help="Parent-solved tasks in the mini-eval probe set (default: 2).",
    )
    parser.add_argument(
        "--probe-unsolved",
        type=int,
        default=1,
        help="Parent-failed tasks in the mini-eval probe set, in addition to each "
        "candidate's assigned focus tasks (default: 1).",
    )
    parser.add_argument(
        "--explore-every",
        type=int,
        default=3,
        help="Every Nth round picks the most NOVEL archived parent instead of the "
        "best-scoring one (default: 3; 0 disables exploration).",
    )
    parser.add_argument(
        "--parent-tie-eps",
        type=float,
        default=0.0,
        help="Treat scored archive nodes within this pass-rate of the best mean "
        "as simultaneous fan-out parents (default: 0 = exact ties only).",
    )
    parser.add_argument(
        "--fanout-mode",
        choices=["v1", "v2", "tournament"],
        default="v1",
        help=(
            "Fan-out screening version. v1 (default) is fail-fast: LLM cuts the "
            "batch, then mini-eval hard-drops candidates that break more than one "
            "parent-solved probe task. v2 screens structurally, probes every remaining "
            "valid harness, ranks on net gain (new solves can outweigh regressions), "
            "and MERGE complementary candidates into one extra harness (complementary "
            "changesets are enough; the union does not have to beat either parent). "
            "Tied and unique-gain harnesses stay as simultaneous parents. "
            "tournament: propose N distinct harnesses in round 1 and a cheaper "
            "continuation + complementary-synthesis pair in later rounds; drop only system errors / "
            "no-ops / dupes, full-eval all of them in parallel, keep the highest "
            "pass-rate as the next parent. No probe, no LLM rank. "
            "Only applies when --fanout > 1."
        ),
    )
    parser.add_argument(
        "--fanout-eval-concurrent",
        type=int,
        default=0,
        help=(
            "Max concurrent full-evals of fan-out survivors (default: 0 = all "
            "at once). Tournament typically sets this to --fanout so the N "
            "harnesses share one 50-task wave."
        ),
    )
    parser.add_argument(
        "--tournament-later-fanout",
        type=int,
        default=2,
        help="Tournament proposal count after the first evolve round (default: 2). "
        "One slot continues a pivot and one can synthesize complementary pivots.",
    )
    parser.add_argument(
        "--pivot-min-new-solves",
        type=int,
        default=3,
        help="Keep a non-winning candidate as a next-round pivot only when it "
        "solves at least this many tasks its parent missed (default: 3).",
    )
    parser.add_argument(
        "--no-fanout-merge",
        action="store_true",
        default=False,
        help="v2 only: skip synthesizing a merged harness from complementary candidates.",
    )
    args = parser.parse_args()

    if args.fanout < 1:
        raise ValueError("--fanout must be >= 1")
    if args.tournament_later_fanout < 2:
        raise ValueError("--tournament-later-fanout must be >= 2")
    if args.pivot_min_new_solves < 1:
        raise ValueError("--pivot-min-new-solves must be >= 1")
    if args.fanout_mode == "tournament":
        # Eval every valid proposal; --fanout-keep is ignored.
        args.fanout_keep = int(args.fanout)
        args.skip_final_score = True
    if args.fanout_keep < 1:
        raise ValueError("--fanout-keep must be >= 1")
    if args.fanout_keep > args.fanout:
        raise ValueError(
            f"--fanout-keep ({args.fanout_keep}) cannot exceed --fanout ({args.fanout})"
        )
    if args.resume and not args.run_tag:
        raise ValueError("--resume requires --run-tag so it can locate previous state.")
    if args.adaptive_subset and args.trajectory_mode != "rerun":
        raise ValueError("--adaptive-subset requires --trajectory-mode rerun.")

    r0_dir = Path(args.r0_dir).resolve() if args.r0_dir else None
    tasks_json = Path(args.tasks).resolve()
    if not args.resume and args.trajectory_mode == "reuse":
        if r0_dir is None or not r0_dir.is_dir():
            raise FileNotFoundError(f"--trajectory-mode reuse requires an existing --r0-dir: {r0_dir or '(not set)'}")
    if not args.resume and r0_dir is not None and not r0_dir.is_dir():
        raise FileNotFoundError(f"--r0-dir not found: {r0_dir}")
    if not args.resume and not tasks_json.is_file():
        raise FileNotFoundError(f"--tasks file not found: {tasks_json}")
    try:
        _auth_preflight(args.model, args.provider_id)
    except RuntimeError as exc:
        raise SystemExit(f"\n[auth-preflight] {exc}\n") from exc

    run_tag = args.run_tag or time.strftime("run_%Y%m%d-%H%M%S")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_root = RUNS_DIR / run_tag
    run_root.mkdir(parents=True, exist_ok=True)

    subset_dir = run_root / "subset_r0_failed"  # only materialized for reuse mode
    r0_round_dir = run_root / "R0"
    baseline_config = r0_round_dir / "config.yaml"
    legacy_traj_dir = r0_round_dir / "trajectories"

    if legacy_traj_dir.exists():
        shutil.rmtree(legacy_traj_dir)
        logger.info("Removed legacy ingested markdown directory: %s", legacy_traj_dir)

    task_names: list[str] | None = None
    trajectories_root: Path | None = None  # only needed for reuse mode

    if args.resume:
        if args.trajectory_mode == "reuse":
            if not subset_dir.is_dir():
                raise FileNotFoundError(f"Resume requested but subset trajectory dir not found: {subset_dir}")
            trajectories_root = subset_dir
        if not baseline_config.is_file():
            raise FileNotFoundError(f"Resume requested but R0 baseline config not found: {baseline_config}")
        if tasks_json.is_file():
            task_names = json.loads(tasks_json.read_text(encoding="utf-8"))
            if isinstance(task_names, dict) and "task_ids" in task_names:
                task_names = list(task_names["task_ids"])
        if args.trajectory_mode == "reuse" and (subset_dir / "_manifest.json").is_file():
            manifest = json.loads((subset_dir / "_manifest.json").read_text(encoding="utf-8"))
            trial_count = manifest.get("num_trials")
            n_failed = manifest.get("num_failed")
            n_passed_m = manifest.get("num_passed")
            if isinstance(n_failed, int) and isinstance(n_passed_m, int):
                print(
                    f"\nResuming run `{run_tag}` with subset ({trial_count} trials: "
                    f"{n_failed} failed + {n_passed_m} passed)."
                )
            else:
                print(f"\nResuming run `{run_tag}` with subset ({trial_count} trials).")
        elif task_names:
            print(f"\nResuming run `{run_tag}` with {len(task_names)} tasks ({args.trajectory_mode} mode).")
        else:
            print(f"\nResuming run `{run_tag}`.")
    else:
        r0_round_dir.mkdir(parents=True, exist_ok=True)
        task_names = json.loads(tasks_json.read_text(encoding="utf-8"))
        if isinstance(task_names, dict) and "task_ids" in task_names:
            task_names = list(task_names["task_ids"])
        if not isinstance(task_names, list) or not task_names:
            raise ValueError(
                f"--tasks must be a non-empty JSON list (or object with task_ids): {tasks_json}"
            )

        picked: list[Path] = []
        if r0_dir is not None and r0_dir.is_dir():
            picked = _load_trials_from_tasks_json(r0_dir, tasks_json)

        if args.trajectory_mode == "reuse":
            if not picked:
                raise RuntimeError("--trajectory-mode reuse requires --r0-dir with prior trial directories.")
            if subset_dir.exists():
                shutil.rmtree(subset_dir)
            subset_dir.mkdir(parents=True, exist_ok=True)
            for src in picked:
                (subset_dir / src.name).symlink_to(src.resolve(), target_is_directory=True)
            trajectories_root = subset_dir
            logger.info("Materialized subset dir with %d trial symlinks → %s", len(picked), subset_dir)

        if picked:
            eval_records = _collect_eval_records_from_trials(picked)
            n_passed = sum(1 for r in eval_records if r.get("reward") and r["reward"] > 0)
            print(
                f"\nLoaded {len(picked)} tasks from {tasks_json.name} "
                f"({n_passed} passed, {len(picked) - n_passed} failed) — "
                f"using prior trajectories as R0 evidence:"
            )
            for p in picked:
                rw = _reward_of_trial_dir(p)
                status = "PASSED" if rw and rw > 0 else "FAILED"
                print(f"  - [{status}] {p.name.split('__')[0]}")
            _print_eval_summary(eval_records, "Subset R0 (prior)")
            (r0_round_dir / "eval_summary.json").write_text(
                json.dumps(
                    {
                        "round": 0,
                        "r0_eval_mode": "reuse_existing_results_only"
                        if args.trajectory_mode == "reuse"
                        else "warm_start_provenance",
                        "source_run_dir": str(r0_dir),
                        "tasks_json": str(tasks_json),
                        "records": eval_records,
                        "passed": n_passed,
                        "total": len(eval_records),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (r0_round_dir / "provenance.json").write_text(
                json.dumps(
                    {
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "policy": "warm_start_r0",
                        "detail": "R0 evidence loaded from prior run for provenance display.",
                        "source_run_dir": str(r0_dir),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            print(f"\nFresh start: {len(task_names)} tasks from {tasks_json.name} (R0 will run with baseline config):")
            for name in task_names:
                print(f"  - {name}")
            (r0_round_dir / "eval_summary.json").write_text(
                json.dumps(
                    {
                        "round": 0,
                        "r0_eval_mode": "fresh_rerun",
                        "source_run_dir": None,
                        "tasks_json": str(tasks_json),
                        "records": [],
                        "passed": 0,
                        "total": 0,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (r0_round_dir / "provenance.json").write_text(
                json.dumps(
                    {
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "policy": "fresh_r0_rerun",
                        "detail": "No prior trajectories; R0 will be generated by running TB2 eval with baseline config.",
                        "tasks": task_names,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        # baseline config
        if args.baseline_config:
            # Seed this run's R0 from an already-evolved harness instead of the
            # stock one. This is what chains iterations of the harness->SFT loop:
            # without it, iteration k+1 restarts from the stock config and throws
            # away every edit iteration k's gate accepted, so the loop measures
            # k independent single-shot evolutions rather than an accumulation.
            src = Path(args.baseline_config).resolve()
            if not src.is_file():
                raise FileNotFoundError(f"--baseline-config not found: {src}")
            baseline_config = r0_round_dir / "config.yaml"
            copied = _materialize_config_bundle(
                src, baseline_config, r0_round_dir / "assets"
            )
            (r0_round_dir / "baseline_provenance.json").write_text(
                json.dumps({"seeded_from": str(src),
                            "assets_copied": copied,
                            "note": ("R0 harness inherited from a previous evolve iteration, "
                                     "materialized as a self-contained bundle so this run does "
                                     "not depend on the previous iteration's directory")},
                           indent=2),
                encoding="utf-8",
            )
            logger.info("Seeded baseline config from %s → %s (%d asset(s) copied: %s)",
                        src, baseline_config, len(copied), ", ".join(copied) or "none")
        elif args.eval_backend == "tmax":
            src = Path(_PROJECT_ROOT) / "configs" / "baseline_tmax_harness.yaml"
            if not src.is_file():
                src = Path(_PROJECT_ROOT) / "configs" / "baseline_harness.yaml"
            baseline_config = r0_round_dir / "config.yaml"
            copied = _materialize_config_bundle(
                src, baseline_config, r0_round_dir / "assets"
            )
            seed_prompt = Path(_PROJECT_ROOT) / "configs" / "tmax_system_prompt.txt"
            if seed_prompt.is_file():
                shutil.copy2(seed_prompt, r0_round_dir / "system_prompt.txt")
            (r0_round_dir / "baseline_provenance.json").write_text(
                json.dumps(
                    {
                        "seeded_from": str(src),
                        "assets_copied": copied,
                        "eval_backend": "tmax",
                        "system_prompt": str(seed_prompt) if seed_prompt.is_file() else None,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info("Seeded Tmax baseline from %s → %s", src, baseline_config)
        else:
            baseline_config = _dump_baseline_config(
                r0_round_dir,
                timeout_seconds=args.task_timeout,
            )

    # 4) run meta_harness evolve loop (recipe-level iteration)
    provider = _make_provider(args.model, args.provider_id)
    meta_model = ModelConfig(main=provider)
    evolve_dir = run_root / "_meta_v2"
    evolve_dir.mkdir(parents=True, exist_ok=True)
    memo_path = run_root / "learnings.md"
    session_id = args.session_id or f"tb2-metav2:{run_tag}"
    state_path = evolve_dir / "_meta_scratch" / "harness_evolve_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    _TB2_SKILLS_DIR = _RECIPE_DIR / "skills"
    meta_agent = MetaAgent(
        inner_model=meta_model,
        memo_path=memo_path,
        extra_skills_dirs=([_TB2_SKILLS_DIR] if _TB2_SKILLS_DIR.is_dir() else None),
        max_cost_usd=args.evolve_cost,
        wall_clock_s=float(args.evolve_wall_clock),
        max_steps=args.evolve_steps,
        step_deadline_early_reminder_step=args.evolve_early_reminder,
        step_deadline_reminder_step=args.evolve_reminder,
        extra_harness_kws={"loop_detection": False},
        require_evidence=not args.no_require_evidence,
    )

    adapter: TB2RoundAdapter | TmaxRoundAdapter
    if args.eval_backend == "tmax":
        if not args.tmax_envs_jsonl:
            raise SystemExit("--eval-backend tmax requires --tmax-envs-jsonl")
        if not task_names:
            raise SystemExit("--eval-backend tmax requires a non-empty --tasks JSON list")
        adapter = TmaxRoundAdapter(
            baseline_config=baseline_config,
            task_names=task_names,
            envs_jsonl=Path(args.tmax_envs_jsonl),
            tasks_json=tasks_json,
            r0_trajectories=r0_dir if (r0_dir is not None and args.trajectory_mode == "rerun") else None,
            repo_root=Path(_PROJECT_ROOT),
            eval_concurrent=args.tb2_eval_concurrent,
            eval_resume=args.tb2_eval_resume,
            endpoints_file=Path(args.tb2_endpoints_file) if args.tb2_endpoints_file else None,
            eval_max_steps=args.tb2_max_steps,
            seed_system_prompt=Path(_PROJECT_ROOT) / "configs" / "tmax_system_prompt.txt",
        )
    else:
        adapter = TB2RoundAdapter(
            baseline_config=baseline_config,
            trajectories_root=trajectories_root,
            task_names=task_names,
            r0_trajectories=r0_dir if (r0_dir is not None and args.trajectory_mode == "rerun") else None,
            run_mode=args.trajectory_mode,
            strict_round_trajectories=not args.allow_round_fallback,
            repo_root=Path(_PROJECT_ROOT),
            eval_script=Path(args.tb2_eval_script),
            eval_concurrent=args.tb2_eval_concurrent,
            eval_resume=args.tb2_eval_resume,
            endpoints_file=Path(args.tb2_endpoints_file) if args.tb2_endpoints_file else None,
            eval_max_steps=args.tb2_max_steps,
        )

    # Load or initialise evolve state.
    if args.resume:
        if not state_path.is_file():
            raise FileNotFoundError(f"Resume requested but state not found: {state_path}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("session_id") != session_id:
            raise ValueError(
                "resume requested but session_id mismatched with previous state: "
                f"state={state.get('session_id')!r}, requested={session_id!r}"
            )
        next_input_round = int(state.get("next_input_round", 0))
        current_config = Path(str(state.get("last_output_config", ""))).resolve()
        if not current_config.is_file():
            raise FileNotFoundError(f"resume state points to missing last_output_config: {current_config}")
    else:
        next_input_round = int(args.start_round) if args.start_round is not None else 0
        current_config = adapter.initial_config()
        now = int(time.time())
        state = {
            "version": 1,
            "session_id": session_id,
            "status": "running",
            "created_at_epoch_s": now,
            "updated_at_epoch_s": now,
            "next_input_round": next_input_round,
            "last_output_round": next_input_round - 1,
            "last_output_config": str(current_config),
            "history": [],
        }
        _save_state(state_path, state)

    # Per-round evolve loop.
    last_rx_output_dir: Path | None = None
    last_promoted: Path = current_config
    # Restore best across --resume. Previously this was always None, so every
    # resume re-baselined and accepted regressions as "first scored round".
    best_round: tuple[float, Path, int] | None = _best_from_state(state)
    if best_round is not None:
        logger.info(
            "Restored best_so_far from state: R%d score=%.4f config=%s",
            best_round[2],
            best_round[0],
            best_round[1],
        )
    # {task_name: passed} across the full task universe, updated with whatever
    # was actually re-tested each round. Only meaningful when --adaptive-subset
    # is set, but harmless (and free) to track unconditionally so resume never
    # loses it. R0 always tests every task, so it seeds full coverage.
    full_task_results: dict[str, bool] = {
        str(k): bool(v) for k, v in (state.get("full_task_results") or {}).items()
    }
    # ── population archive + screen wiring ───────────────────────────────
    # The archive lives inside `state`, so it is saved and resumed by the same
    # _save_state calls the loop already makes.
    archive = Archive(state, task_universe=task_names or [])
    fanout_on = int(args.fanout) > 1
    tournament = args.fanout_mode == "tournament"
    if fanout_on and tournament:
        logger.info(
            "Fan-out ON (tournament): %d proposals first round, %d later, drop only system "
            "errors / no-ops / dupes, then parallel full eval of every survivor "
            "(eval-concurrent=%s)",
            args.fanout,
            args.tournament_later_fanout,
            args.fanout_eval_concurrent or "all",
        )
    elif fanout_on:
        logger.info(
            "Fan-out ON (%s): %d proposals/round → screens → %d full eval%s; "
            "explore every %s rounds",
            args.fanout_mode,
            args.fanout,
            args.fanout_keep,
            "" if args.fanout_mode != "v2" or args.no_fanout_merge else " (+ complementary merge)",
            args.explore_every or "never",
        )

    async def _screen_llm(system: str, user: str) -> str:
        """Single-shot completion for the LLM ranking screen (no tools)."""
        from harnessx.core.events import Message

        resp = await provider.complete(
            [Message(role="system", content=system), Message(role="user", content=user)],
            tools=[],
        )
        return resp.content or ""

    def _make_probe_runner(round_no: int):
        """Bind a probe runner to one round.

        Bound per round rather than closing over the loop variable: a closure
        over `input_round` would silently probe under the wrong round's job
        name once the loop advanced, colliding with the real eval's output dir.
        """

        async def _run_probe(*, config: Path, tasks, label: str) -> dict[str, bool]:
            traj = await adapter.resolve_trajectories_for_round(
                run_root=run_root,
                input_round=round_no,
                current_config=Path(config),
                task_names_override=list(tasks),
                job_suffix=label,
            )
            return read_per_task_results(traj)

        return _run_probe

    for i in range(args.num_rounds):
        input_round = next_input_round + i
        output_round = input_round + 1
        try:
            evaluated_config = Path(current_config).resolve()

            round_task_override: list[str] | None = None
            canary: list[str] = []
            if args.adaptive_subset and input_round > 0 and task_names:
                failed = [t for t in task_names if not full_task_results.get(t, False)]
                passed = [t for t in task_names if full_task_results.get(t, False)]
                # Rotate the canary window by round instead of always taking
                # passed[:N]. A fixed window re-verifies the same N tasks every
                # round and lets every other passing task carry its True forward
                # untouched for the whole run — so the score can only go down via
                # those N, and a config that breaks 4 non-canary passing tasks is
                # gated as "no regression" and promoted to best_so_far. Rotating
                # gives every passing task a turn, so a regression surfaces within
                # ceil(len(passed)/N) rounds instead of never.
                k = max(0, args.adaptive_canary_size)
                if passed and k:
                    start = (input_round * k) % len(passed)
                    rotated = passed[start:] + passed[:start]
                    canary = rotated[:k]
                round_task_override = failed + [t for t in canary if t not in failed]
                if not round_task_override:
                    # Everything currently known-passing and no canary requested —
                    # still re-verify one task so the round isn't a total no-op.
                    round_task_override = passed[:1]
                    canary = round_task_override
                logger.info(
                    "[R%d] adaptive subset: %d failed + %d canary (%s) = %d/%d task(s)",
                    input_round,
                    len(failed),
                    len(canary),
                    ", ".join(canary) or "-",
                    len(round_task_override),
                    len(task_names),
                )

            cache = state.get("tournament_eval_cache") or {}
            cached_cfg = Path(cache["config"]).resolve() if cache.get("config") else None
            cached_traj = Path(cache["trajectories"]) if cache.get("trajectories") else None
            if (
                tournament
                and cached_cfg is not None
                and cached_cfg == evaluated_config
                and cached_traj is not None
                and cached_traj.is_dir()
            ):
                trajectories_dir = cached_traj
                logger.info(
                    "[R%d] tournament: reusing generation-winner eval %s",
                    input_round,
                    trajectories_dir,
                )
            else:
                trajectories_dir = await adapter.resolve_trajectories_for_round(
                    run_root=run_root,
                    input_round=input_round,
                    current_config=evaluated_config,
                    task_names_override=round_task_override,
                )
            if not trajectories_dir.is_dir():
                raise FileNotFoundError(f"resolve_trajectories_for_round returned non-directory: {trajectories_dir}")

            # Merge this round's actual results into the full-universe tracker.
            # Tasks not re-tested this round keep their last known outcome.
            round_results = read_per_task_results(trajectories_dir)

            # A task that WAS in this round's subset but produced no result.json
            # (crashed harness, dead endpoint, container OOM) yields no key here.
            # Letting it fall through to the carry-forward path would re-assert
            # its previous outcome as if freshly measured — so a canary that
            # passed at R0 and crashed at R1 still reads True, and a round where
            # every single task crashed scores bit-identical to the previous
            # round and gets gated "accept". Count them as failures, matching
            # what the non-adaptive path does (sharded_tb2_eval puts missing
            # tasks in the denominator for exactly this reason).
            attempted = round_task_override if round_task_override is not None else (task_names or [])
            no_result = [t for t in attempted if t not in round_results]
            if no_result:
                logger.warning(
                    "[R%d] %d/%d attempted task(s) produced no result and are scored as "
                    "failures (not carried forward): %s",
                    input_round,
                    len(no_result),
                    len(attempted),
                    ", ".join(sorted(no_result)),
                )
                for t in no_result:
                    round_results[t] = False

            if canary:
                regressed = sorted(t for t in canary if round_results.get(t) is False)
                if regressed:
                    logger.warning(
                        "[R%d] canary regression on previously-passed task(s): %s — "
                        "carried-forward tasks not in this round's subset were NOT "
                        "re-verified and may also have regressed silently.",
                        input_round,
                        regressed,
                    )
            full_task_results.update(round_results)

            # Gate the evaluated config BEFORE evolving. Old code evolved first,
            # then attached this round's score to the *unevaluated* successor
            # (off-by-one), so regressions were never attributed to the config
            # that caused them.
            if args.adaptive_subset and task_names:
                # Score over the full task universe (carried-forward + freshly
                # re-tested), NOT just this round's smaller subset — otherwise a
                # round that only reran 3 previously-failing tasks would be scored
                # on a denominator of 3 and be incomparable to R0's score of 15.
                total = len(task_names)
                passed_n = sum(1 for t in task_names if full_task_results.get(t, False))
                score = (passed_n / total) if total else None
            else:
                score = adapter.read_round_score(trajectories_dir)
            # Record this measurement before gating so the incumbent's mean
            # includes every repeat, including the one just taken.
            _record_config_score(state, evaluated_config, score)
            state.setdefault("per_round_task_results", {})[str(input_round)] = dict(full_task_results)
            # Mirror the measurement into the archive so parent selection and
            # the novelty vectors see it. Repeats append to the same node.
            _arch_node = archive.node_for_config(str(evaluated_config), round=input_round)
            if score is not None:
                archive.record_score(
                    _arch_node.id,
                    float(score),
                    [t for t in task_names if full_task_results.get(t)],
                    trajectories=str(trajectories_dir),
                )

            unique_gained: list[str] = []
            inc_node = archive.incumbent()
            if inc_node is not None and Path(inc_node.config).resolve() != Path(evaluated_config).resolve():
                unique_gained = sorted(
                    t for t in task_names
                    if full_task_results.get(t) and t not in set(inc_node.solved or [])
                )
            incumbent_mean = _mean_score_for(state, best_round[1]) if best_round else None
            decision, gate_reason, best_round, reverted_cfg = _score_and_gate_tb2(
                round_idx=input_round,
                round_score=score,
                round_config=evaluated_config,
                best=best_round,
                tolerance=args.regression_tolerance,
                incumbent_mean=incumbent_mean,
                unique_gained=unique_gained,
            )

            if tournament and reverted_cfg is not None:
                logger.info(
                    "[R%d] tournament: ignoring revert (%s) — next parent is "
                    "this generation's highest scorer, not the incumbent",
                    input_round,
                    gate_reason,
                )
                reverted_cfg = None
                decision = "accept"

            # Close the loop: the config scored here is the one the meta-agent
            # produced in journal round `input_round`. Without this write-back the
            # journal's gating_outcome stayed "pending" forever and the lever
            # scoreboard the agent is shown was permanently empty — it never
            # learned whether any edit it had ever made worked.
            if input_round >= 1:
                flipped = ""
                prev = (state.get("per_round_task_results") or {}).get(str(input_round - 1))
                if prev:
                    gained = sorted(t for t in task_names
                                    if full_task_results.get(t) and not prev.get(t))
                    lost = sorted(t for t in task_names
                                  if prev.get(t) and not full_task_results.get(t))
                    flipped = (f"+{len(gained)}/-{len(lost)}"
                               + (f" gained={','.join(gained[:4])}" if gained else "")
                               + (f" lost={','.join(lost[:4])}" if lost else ""))
                n_pass = round(score * len(task_names)) if isinstance(score, (int, float)) else "?"
                attribution = (f"score={n_pass}/{len(task_names)}"
                               + (f"; {flipped}" if flipped else "")
                               + f"; {gate_reason}")
                if _update_journal_outcome(memo_path, input_round,
                                           "accepted" if reverted_cfg is None else "reverted",
                                           attribution):
                    logger.info("[R%d] journal outcome written back: %s",
                                input_round, attribution)
                else:
                    logger.warning("[R%d] could not write gate outcome into %s",
                                   input_round, memo_path)

            round_evolve_dir: Path
            output_config_path: Path
            promoted_config: Path
            changed: bool
            elapsed: float
            tournament_eval = None
            this_round_tournament = None

            if reverted_cfg is not None:
                logger.warning(
                    "[R%d] REGRESSION (%s) — reverting to %s; skipping evolve",
                    input_round,
                    gate_reason,
                    reverted_cfg,
                )
                current_config = Path(str(reverted_cfg)).resolve()
                # Carry the best config forward as a no-op R{n} so resume/layout
                # stay consistent without burning a meta-agent call on a reject.
                promoted_config = _copy_config_as_round(
                    run_root=run_root,
                    output_round=output_round,
                    config=current_config,
                )
                round_evolve_dir = (run_root / f"R{output_round}" / "evolve").resolve()
                output_config_path = promoted_config
                changed = False
                elapsed = 0.0
            else:
                logger.info("[R%d] gate %s (%s)", input_round, decision, gate_reason)
                started_at = time.time()
                round_evolve_dir = evolve_dir / f"R{output_round}"
                round_evolve_dir.mkdir(parents=True, exist_ok=True)
                # Directory the promoted round copies its sidecars from
                # (system_prompt.txt, processors/, tools/, templates/). Under
                # fan-out the winner's sidecars live in its own cN/ subdir, NOT
                # in the round root — promoting from the root would silently
                # drop a system-prompt or processor edit, which are exactly the
                # levers the meta-agent uses most.
                promote_src_dir = round_evolve_dir
                # TASK.md instructs the agent to read these; until now none of
                # them were ever written, so it planned each round with no idea
                # what the previous rounds had scored or changed.
                try:
                    _write_history_context(
                        round_evolve_dir / "_meta_scratch",
                        state=state, run_root=run_root, task_names=task_names,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("could not write history context: %s", exc)
                if not fanout_on:
                    output_config_path = await meta_agent.evolve(
                        current_config=evaluated_config,
                        trajectories_dir=trajectories_dir,
                        output_dir=round_evolve_dir,
                    )
                else:
                    # Branch from the archive's picks. Tied / unique-gain
                    # lineages stay as simultaneous pivots so the meta-agent
                    # can read each of their trajectories, not just the incumbent.
                    if tournament:
                        parent_nodes = (
                            archive.select_parents(
                                round=input_round,
                                explore_every=0,
                                tie_eps=float(getattr(args, "parent_tie_eps", 0.0)),
                                min_unique_solves=int(args.pivot_min_new_solves),
                            )
                            or [_arch_node]
                        )
                    else:
                        parent_nodes = (
                            archive.select_parents(
                                round=input_round,
                                explore_every=int(args.explore_every),
                                tie_eps=float(getattr(args, "parent_tie_eps", 0.0)),
                            )
                            or [_arch_node]
                        )
                    parent_node = parent_nodes[0]
                    parent_cfg = Path(parent_node.config).resolve()
                    if not parent_cfg.is_file():
                        logger.warning(
                            "[R%d] archived parent %s missing on disk (%s) — "
                            "falling back to the in-hand config",
                            input_round, parent_node.id, parent_cfg,
                        )
                        parent_node, parent_cfg = _arch_node, evaluated_config
                        parent_nodes = [parent_node]

                    parent_results = {t: bool(full_task_results.get(t)) for t in task_names}
                    if parent_node.solved:
                        parent_results = {
                            t: t in set(parent_node.solved) for t in task_names
                        }
                    pivot_dicts = _nodes_to_pivot_dicts(
                        parent_nodes, task_names, archive.incumbent()
                    )
                    try:
                        scratch = round_evolve_dir / "_meta_scratch"
                        scratch.mkdir(parents=True, exist_ok=True)
                        (scratch / "PIVOTS.md").write_text(
                            format_pivot_brief(pivot_dicts) or "(single parent)\n",
                            encoding="utf-8",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("could not write PIVOTS.md: %s", exc)
                    if tournament:
                        propose = propose_tournament
                    elif args.fanout_mode == "v2":
                        propose = propose_and_screen_v2
                    else:
                        propose = propose_and_screen
                    propose_kw = dict(
                        meta_agent=meta_agent,
                        archive=archive,
                        parent=parent_node,
                        parent_config=parent_cfg,
                        parent_results=parent_results,
                        trajectories_dir=trajectories_dir,
                        round_dir=round_evolve_dir,
                        task_universe=task_names,
                        llm=None if args.no_screen_llm else _screen_llm,
                        run_probe=(
                            None if args.no_screen_probe
                            else _make_probe_runner(input_round)
                        ),
                        n=(
                            int(args.fanout)
                            if not tournament or input_round == 0
                            else max(2, int(args.tournament_later_fanout))
                        ),
                        keep=int(args.fanout_keep),
                        max_concurrent=int(args.fanout_concurrent),
                        n_probe_solved=int(args.probe_solved),
                        n_probe_unsolved=int(args.probe_unsolved),
                    )
                    if args.fanout_mode == "v2":
                        propose_kw["merge"] = not args.no_fanout_merge
                        propose_kw["pivots"] = pivot_dicts
                    elif tournament:
                        propose_kw["pivots"] = pivot_dicts
                    survivors = await propose(**propose_kw)
                    if not survivors:
                        # Every proposal died. Carry the parent forward rather
                        # than failing the run: a barren round is a bad round,
                        # not a broken campaign.
                        logger.warning(
                            "[R%d] fan-out produced no survivor — carrying %s forward",
                            input_round, parent_cfg,
                        )
                        output_config_path = _copy_config_as_round(
                            run_root=run_root,
                            output_round=output_round,
                            config=parent_cfg,
                        )
                        promote_src_dir = Path(output_config_path).parent
                    else:
                        winner, kept_pivots = await _full_eval_survivors(
                            survivors=survivors,
                            archive=archive,
                            adapter=adapter,
                            run_root=run_root,
                            input_round=input_round,
                            task_names=task_names,
                            parent_solved=list(parent_node.solved or []),
                            tie_eps=float(getattr(args, "parent_tie_eps", 0.0)),
                            force_eval=tournament,
                            eval_concurrent=int(getattr(args, "fanout_eval_concurrent", 0) or 0),
                            job_suffix_traj=tournament,
                            min_unique_pivot=int(args.pivot_min_new_solves),
                        )
                        tournament_eval = winner
                        output_config_path = Path(winner.config).resolve()
                        promote_src_dir = output_config_path.parent
                        if tournament:
                            this_round_tournament = [
                                {
                                    "idx": c.idx,
                                    "score": c.full_score,
                                    "config": str(c.config),
                                    "trajectories": (
                                        str(c.trajectories_dir)
                                        if c.trajectories_dir
                                        else None
                                    ),
                                    "new_solves": sorted(set(c.full_solved or []) - {
                                        t for t, ok in (c.parent_results or {}).items() if ok
                                    }),
                                    "regressions": sorted({
                                        t for t, ok in (c.parent_results or {}).items() if ok
                                    } - set(c.full_solved or [])),
                                }
                                for c in survivors
                                if c.full_score is not None
                            ]
                            state["tournament_last_round"] = this_round_tournament
                        state["pivots"] = _nodes_to_pivot_dicts(
                            [
                                archive.get(c.node.id)
                                for c in kept_pivots
                                if getattr(c, "node", None) is not None
                                and archive.get(c.node.id) is not None
                            ],
                            task_names,
                            archive.incumbent(),
                        )
                        _save_state(state_path, state)
                elapsed = time.time() - started_at
                output_config_path = Path(output_config_path).resolve()

                promoted_config = adapter.promote_round_output(
                    run_root=run_root,
                    output_round=output_round,
                    rx_output_dir=promote_src_dir,
                    output_config=output_config_path,
                ).resolve()

                changed = evaluated_config.read_bytes() != output_config_path.read_bytes()
                current_config = promoted_config

                if tournament and tournament_eval is not None:
                    traj = getattr(tournament_eval, "trajectories_dir", None)
                    if traj is not None:
                        state["tournament_eval_cache"] = {
                            "config": str(promoted_config),
                            "trajectories": str(traj),
                            "score": tournament_eval.full_score,
                            "idx": tournament_eval.idx,
                        }
                    wscore = getattr(tournament_eval, "full_score", None)
                    if wscore is not None and (
                        best_round is None or float(wscore) > best_round[0]
                    ):
                        best_round = (float(wscore), promoted_config, output_round)
                        logger.info(
                            "[R%d] tournament best_so_far ← c%d @ %.4f",
                            output_round,
                            tournament_eval.idx,
                            wscore,
                        )

            rr = {
                "input_round": input_round,
                "output_round": output_round,
                "input_config": str(evaluated_config),
                "gated_config": str(evaluated_config),
                "trajectories_dir": str(trajectories_dir),
                "rx_output_dir": str(round_evolve_dir),
                "output_config": str(output_config_path),
                "promoted_config": str(promoted_config),
                "changed": bool(changed),
                "elapsed_s": round(float(elapsed), 3),
                "score": score,
                "gate_decision": decision,
                "gate_reason": gate_reason,
            }
            if this_round_tournament:
                rr["tournament_last_round"] = this_round_tournament
            bsf = _best_to_state_dict(best_round)
            if bsf is not None:
                rr["best_so_far"] = bsf
                state["best_so_far"] = bsf

            history = state.setdefault("history", [])
            history.append(rr)
            finished_at = int(time.time())
            state["status"] = "running"
            state["next_input_round"] = output_round
            state["last_output_round"] = output_round
            state["last_output_config"] = str(current_config)
            state["updated_at_epoch_s"] = finished_at
            state["full_task_results"] = full_task_results
            state.pop("last_error", None)
            _save_state(state_path, state)

            last_rx_output_dir = round_evolve_dir
            last_promoted = Path(current_config).resolve()
        except Exception as exc:
            msg = str(exc)
            soft = bool(getattr(args, "noop_on_meta_fail", False)) and (
                "timed out" in msg
                or "did not produce" in msg
                or "no config.yaml" in msg
            )
            failed_at = int(time.time())
            if soft:
                logger.error(
                    "[R%d→R%d] meta-agent failed (%s: %s) — no-op promoting incumbent and continuing",
                    input_round,
                    output_round,
                    type(exc).__name__,
                    msg,
                )
                try:
                    round_evolve_dir = evolve_dir / f"R{output_round}"
                    round_evolve_dir.mkdir(parents=True, exist_ok=True)
                    # Leave a breadcrumb next to the missing config.
                    (round_evolve_dir / "_meta_scratch").mkdir(parents=True, exist_ok=True)
                    (round_evolve_dir / "_meta_scratch" / "META_FAIL_NOOP.md").write_text(
                        f"# Meta fail → no-op\n\n`{type(exc).__name__}`: {msg}\n",
                        encoding="utf-8",
                    )
                    # Ensure validate-less promotion path has a config.yaml.
                    noop_yaml = round_evolve_dir / "config.yaml"
                    if not noop_yaml.is_file():
                        shutil.copy2(evaluated_config, noop_yaml)
                    promoted_config = adapter.promote_round_output(
                        run_root=run_root,
                        output_round=output_round,
                        rx_output_dir=round_evolve_dir,
                        output_config=noop_yaml,
                    ).resolve()
                    current_config = promoted_config
                    rr = {
                        "input_round": input_round,
                        "output_round": output_round,
                        "input_config": str(evaluated_config),
                        "gated_config": str(evaluated_config),
                        "trajectories_dir": str(trajectories_dir),
                        "rx_output_dir": str(round_evolve_dir),
                        "output_config": str(noop_yaml.resolve()),
                        "promoted_config": str(promoted_config),
                        "changed": False,
                        "elapsed_s": 0.0,
                        "score": score,
                        "gate_decision": "noop_on_meta_fail",
                        "gate_reason": f"{type(exc).__name__}: {msg}",
                    }
                    bsf = _best_to_state_dict(best_round)
                    if bsf is not None:
                        rr["best_so_far"] = bsf
                        state["best_so_far"] = bsf
                    history = state.setdefault("history", [])
                    history.append(rr)
                    state["status"] = "running"
                    state["next_input_round"] = output_round
                    state["last_output_round"] = output_round
                    state["last_output_config"] = str(current_config)
                    state["updated_at_epoch_s"] = failed_at
                    state["full_task_results"] = full_task_results
                    state["last_error"] = {
                        "input_round": input_round,
                        "output_round": output_round,
                        "error_type": type(exc).__name__,
                        "error_message": msg,
                        "failed_at_epoch_s": failed_at,
                        "recovered_as": "noop",
                    }
                    _save_state(state_path, state)
                    last_rx_output_dir = round_evolve_dir
                    last_promoted = Path(current_config).resolve()
                    continue
                except Exception as recover_exc:  # noqa: BLE001
                    logger.exception(
                        "noop-on-meta-fail recovery also failed: %s", recover_exc
                    )
            state["status"] = "failed"
            state["last_error"] = {
                "input_round": input_round,
                "output_round": output_round,
                "error_type": type(exc).__name__,
                "error_message": msg,
                "failed_at_epoch_s": failed_at,
            }
            state["updated_at_epoch_s"] = failed_at
            _save_state(state_path, state)
            raise

    # ── score the final round's edit ────────────────────────────────────────
    # `--num-rounds N` performs N meta-agent steps, producing configs R1..RN, but
    # only R0..R{N-1} were ever rolled out: the LAST edit was written, promoted,
    # and never measured. That is how a `token_threshold: 140000 -> 9500` change
    # (a 15x cut to the compaction trigger) shipped with its effect unknown, and
    # it means one whole meta-agent call per iteration was wasted every time.
    if not args.skip_final_score and last_promoted is not None:
        final_round = int(state.get("last_output_round", 0))
        try:
            logger.info("[R%d] scoring the final promoted config", final_round)
            final_traj = await adapter.resolve_trajectories_for_round(
                run_root=run_root,
                input_round=final_round,
                current_config=Path(last_promoted).resolve(),
            )
            final_results = read_per_task_results(final_traj)
            final_pass = sum(1 for t in task_names if final_results.get(t, False))
            final_score = (final_pass / len(task_names)) if task_names else None

            _record_config_score(state, last_promoted, final_score)
            state.setdefault("per_round_task_results", {})[str(final_round)] = dict(final_results)

            incumbent_mean = _mean_score_for(state, best_round[1]) if best_round else None
            decision, gate_reason, best_round, _ = _score_and_gate_tb2(
                round_idx=final_round,
                round_score=final_score,
                round_config=Path(last_promoted).resolve(),
                best=best_round,
                tolerance=args.regression_tolerance,
                incumbent_mean=incumbent_mean,
            )
            if final_round >= 1:
                _update_journal_outcome(
                    memo_path, final_round,
                    "accepted" if decision == "accept" else "reverted",
                    f"score={final_pass}/{len(task_names)}; {gate_reason} (final-round scoring)",
                )
            bsf = _best_to_state_dict(best_round)
            if bsf is not None:
                state["best_so_far"] = bsf
            (state.setdefault("history", [])).append({
                "input_round": final_round,
                "output_round": final_round,
                "input_config": str(last_promoted),
                "gated_config": str(last_promoted),
                "trajectories_dir": str(final_traj),
                "score": final_score,
                "gate_decision": decision,
                "gate_reason": gate_reason,
                "final_round_scoring": True,
                "changed": False,
            })
            logger.info("[R%d] final config scored %d/%d — gate %s",
                        final_round, final_pass, len(task_names), decision)
        except Exception as exc:  # noqa: BLE001
            # A failure here must not discard N completed rounds: the edits are
            # already promoted and every earlier score is already in the state.
            logger.warning("final-round scoring failed (rounds are unaffected): %s", exc)

    state["status"] = "completed"
    state["updated_at_epoch_s"] = int(time.time())
    _save_state(state_path, state)

    if last_rx_output_dir is None:
        raise RuntimeError("No evolve rounds executed.")
    new_yaml = last_rx_output_dir / "config.yaml"

    print("\n" + "=" * 72)
    print("meta_harness evolve done")
    print(f"  run_tag:   {run_tag}")
    print(f"  session:   {session_id}")
    print(f"  rounds:    +{args.num_rounds} ({'resume' if args.resume else 'fresh'})")
    print(f"  trajectory_mode: {args.trajectory_mode}")
    print(f"  trajectories_root: {trajectories_root or '(generated per-round)'}")
    print(f"  baseline: {baseline_config}")
    print(f"  evolved_latest: {new_yaml} (bundle: {last_rx_output_dir})")
    print(f"  promoted_latest: {last_promoted}")
    print(f"  state:    {state_path}")
    print(f"  memo:     {memo_path}")
    print(
        "  verify:   "
        f"TB2_HARNESS_CONFIG={last_promoted} "
        "bash benchmarks/terminal_bench_2/scripts/eval_opensandbox.sh "
        "--job-name VERIFY_JOB -n 2 --tasks TASKS.json"
    )
    print("=" * 72 + "\n")


if __name__ == "__main__":
    # asyncio.run() owns loop shutdown. The previous new_event_loop() +
    # shutdown_asyncgens/close sequence raced httpx aclose against a closed
    # loop ("Event loop is closed") and could non-zero-exit after a successful
    # evolve — which, under pipefail in evolve_tmax.sh, aborted the coevolve
    # loop (job 2824, iter2/A_evolve).
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None

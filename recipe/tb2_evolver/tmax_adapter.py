"""Tmax rollout adapter for harness evolve (duck-typed like TB2RoundAdapter).

Rollouts go through ``recipe.tmax_eval.run_eval`` (Docker + HarnessX agent +
pytest). The MetaAgent evolves a full HarnessConfig YAML — processors,
``tool_registry``, and sibling ``system_prompt.txt`` are all applied at
rollout time via ``recipe.tmax_eval.harness_runner``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _seed_system_prompt(config_path: Path, seed_prompt: Path | None) -> None:
    dest = config_path.parent / "system_prompt.txt"
    if dest.is_file():
        return
    if seed_prompt is not None and seed_prompt.is_file():
        shutil.copy2(seed_prompt, dest)
        return
    # Fall back to the repo default seed.
    root = Path(__file__).resolve().parents[2]
    default = root / "configs" / "tmax_system_prompt.txt"
    if default.is_file():
        shutil.copy2(default, dest)


class TmaxRoundAdapter:
    def __init__(
        self,
        *,
        baseline_config: Path,
        task_names: list[str],
        envs_jsonl: Path,
        tasks_json: Path,
        repo_root: Path | None = None,
        eval_concurrent: int = 4,
        eval_resume: bool = False,
        endpoints_file: Path | None = None,
        eval_max_steps: int | None = None,
        r0_trajectories: Path | None = None,
        seed_system_prompt: Path | None = None,
        jobs_dir: Path | None = None,
    ) -> None:
        self.baseline_config = Path(baseline_config).resolve()
        self._explicit_task_names = list(task_names)
        self.envs_jsonl = Path(envs_jsonl).resolve()
        self.tasks_json = Path(tasks_json).resolve()
        self.repo_root = (
            Path(repo_root).resolve()
            if repo_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self.eval_concurrent = max(1, int(eval_concurrent))
        self.eval_resume = bool(eval_resume)
        self.endpoints_file = Path(endpoints_file).resolve() if endpoints_file else None
        self.eval_max_steps = int(eval_max_steps) if eval_max_steps is not None else None
        self.r0_trajectories = Path(r0_trajectories).resolve() if r0_trajectories else None
        self.seed_system_prompt = (
            Path(seed_system_prompt).resolve() if seed_system_prompt else None
        )
        self.jobs_dir = (
            Path(jobs_dir).resolve()
            if jobs_dir is not None
            else (self.repo_root / ".benchmarks" / "tmax").resolve()
        )
        if not self.baseline_config.is_file():
            raise FileNotFoundError(f"baseline config not found: {self.baseline_config}")
        if not self.envs_jsonl.is_file():
            raise FileNotFoundError(f"envs jsonl not found: {self.envs_jsonl}")
        if self.endpoints_file is not None and not self.endpoints_file.is_file():
            raise FileNotFoundError(f"endpoints_file not found: {self.endpoints_file}")
        _seed_system_prompt(self.baseline_config, self.seed_system_prompt)

    def initial_config(self) -> Path:
        _seed_system_prompt(self.baseline_config, self.seed_system_prompt)
        return self.baseline_config

    async def resolve_trajectories_for_round(
        self,
        *,
        run_root: Path,
        input_round: int,
        current_config: Path,
        task_names_override: list[str] | None = None,
    ) -> Path:
        if input_round == 0 and self.r0_trajectories is not None:
            logger.info("R0: reusing pre-existing Tmax trajectories → %s", self.r0_trajectories)
            return self.r0_trajectories

        names = task_names_override if task_names_override is not None else self._explicit_task_names
        if not names:
            raise RuntimeError("TmaxRoundAdapter: empty task list")

        cfg = Path(current_config).resolve()
        _seed_system_prompt(cfg, self.seed_system_prompt)
        job_name = f"{run_root.name}-r{input_round}-traj"
        cmd = [
            sys.executable,
            "-m",
            "recipe.tmax_eval.run_eval",
            "--tasks-json",
            str(self.tasks_json),
            "--envs-jsonl",
            str(self.envs_jsonl),
            "--job-name",
            job_name,
            "--jobs-dir",
            str(self.jobs_dir),
            "--harness-config",
            str(cfg),
            "--concurrent",
            str(self.eval_concurrent),
        ]
        if self.eval_max_steps is not None:
            cmd += ["--max-steps", str(self.eval_max_steps)]
        if self.endpoints_file is not None:
            cmd += ["--endpoints-file", str(self.endpoints_file)]
        if self.eval_resume:
            cmd.append("--resume")
        for t in names:
            cmd += ["--task-id", t]

        env = os.environ.copy()
        env["TB2_HARNESS_CONFIG"] = str(cfg)
        prompt_file = cfg.parent / "system_prompt.txt"
        if prompt_file.is_file():
            env["TMAX_SYSTEM_PROMPT_FILE"] = str(prompt_file)

        logger.info("Tmax rollout: %s", " ".join(cmd))
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(self.repo_root),
            env=env,
            check=True,
        )
        run_dir = (self.jobs_dir / job_name).resolve()
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Expected Tmax traj dir missing: {run_dir}")
        return run_dir

    def promote_round_output(
        self,
        *,
        run_root: Path,
        output_round: int,
        rx_output_dir: Path,
        output_config: Path,
    ) -> Path:
        """Promote evolved config (+ system_prompt.txt) into R{n}/.

        Meta may edit processors / tool_registry in config.yaml and/or the
        sibling system_prompt.txt; both are copied into the promoted round dir.
        """
        from recipe.tb2_evolver.tb2_trajspec import _promote_rx_output

        promoted = _promote_rx_output(
            run_root=run_root,
            output_round=output_round,
            rx_output_dir=rx_output_dir,
            output_config=output_config,
        )
        # Copy MetaAgent-written Tmax prompt sidecar into the promoted round dir.
        src_prompt = Path(rx_output_dir) / "system_prompt.txt"
        dst_prompt = Path(promoted).parent / "system_prompt.txt"
        if src_prompt.is_file():
            shutil.copy2(src_prompt, dst_prompt)
        elif not dst_prompt.is_file():
            _seed_system_prompt(Path(promoted), self.seed_system_prompt)
        return promoted

    def read_round_score(self, trajectories_dir: Path) -> float | None:
        td = Path(trajectories_dir)
        summary = td / "summary.json"
        if summary.is_file():
            try:
                obj = json.loads(summary.read_text(encoding="utf-8"))
                pr = obj.get("pass_rate")
                if isinstance(pr, (int, float)):
                    return float(pr)
            except Exception:
                pass
        # Fall back to TB2-shaped aggregate if present.
        from recipe.tb2_evolver.tb2_trajspec import _read_tb2_pass_rate

        return _read_tb2_pass_rate(td)


__all__ = ["TmaxRoundAdapter"]

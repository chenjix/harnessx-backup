@MENTOR_GITHUB_HANDLE could you help run **three sequential iterations of Qwen3.5-4B harness + online RL coevolution on one 8×H200 node**?

### Experiment

- Start from `Qwen/Qwen3.5-4B` + the baseline Tmax harness; **no SFT**.
- Each iteration: harness evolve → harness evaluation/gate → online RL (DPPO) → model evaluation/gate. Continue from the accepted model/harness pair.
- Harness search: 2 tournament rounds per iteration, fanout 5, rotating 50-task evolve set.
- RL: 100 training tasks, **5120 episodes/iteration**, 32K response, 2 prompts × 16 samples, 6 learners + 2 vLLM engines, LR `1e-6`; active sampling capped at 8 prompt groups/step.
- Evaluate on the fixed **102-task** set, excluded from evolve/RL training. Three iterations run sequentially in one job.

### Launch

[The sbatch script](https://github.com/chenjix/harnessx-backup/blob/main/scripts/slurm/tmax/h200_4b_rl_3iters_handoff.sbatch) contains the complete configuration and requests up to 5 days on `ml.p5en.48xlarge` / `interactive-ai`.

Use the configured cluster checkout with working harness/vLLM and RL environments, meta-model credentials, taxonomy parquet, and generated evaluation JSONLs. Setup details: [environment](https://github.com/chenjix/harnessx-backup/blob/main/docs/ENVIRONMENT.md) and [data preparation](https://github.com/chenjix/harnessx-backup/blob/main/docs/TMAX_RUNBOOK.md#2-fresh-clone-setup).

```bash
cd /fsx/home/jixuan.chen/harnessx-backup  # adjust to the cluster checkout
git pull --ff-only
set -a
source configs/tmax.env
set +a
export REPO="$PWD" ENV_FILE=/dev/null
export REPLICATE=61 START_ITER=1  # choose an unused replicate on the cluster
unset ALLOW_RESUME DRY_RUN TMAX_CONFIG_ONLY

bash scripts/tmax/run.sh preflight
TMAX_CONFIG_ONLY=1 bash scripts/slurm/tmax/h200_4b_rl_3iters_handoff.sbatch
sbatch --export=ALL,REPLICATE="$REPLICATE",START_ITER=1 \
  scripts/slurm/tmax/h200_4b_rl_3iters_handoff.sbatch
```

Please resolve failed preflight checks before submission. If interrupted, inspect `outputs/tmax_coevolve/rep<REP>/STATUS` and resume the incomplete iteration with the same artifacts, e.g.:

```bash
# After the previous job has stopped; example: iteration 2 is incomplete.
sbatch --export=ALL,REPLICATE="$REPLICATE",ALLOW_RESUME=1,START_ITER=2 \
  scripts/slurm/tmax/h200_4b_rl_3iters_handoff.sbatch
```

### Deliverables

- Git SHA, replicate, Slurm job ID, and launch command.
- Initial evaluation plus all three RL evaluations; `scores.tsv`, `incumbent.tsv`, and `timings.tsv` from `outputs/tmax_coevolve/rep<REP>/`.
- Checkpoint paths for all three iterations under `outputs/rl/tmax_coev_rep<REP>_i{1,2,3}_a0/`, and training logs with achieved steps/episodes, rewards, gradient norms, and exit status.
- If incomplete, the failing stage and relevant log excerpt.

Local shell/config checks and 8 RL data/selection tests passed. H200 execution is pending.

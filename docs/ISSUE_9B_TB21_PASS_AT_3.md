@cherry979988 could you help run **two more full Terminal-Bench 2.1 evaluation trials for the 9B base / SFT / RL checkpoints on one 8×H200 node**, so the three trials combine into pass@3?

### Experiment

- Benchmark: **Terminal-Bench 2.1, full 89 tasks** (registry `terminal-bench@2.0`), native-harness transfer mode.
- Three checkpoints, evaluated with the same harness pairing used in the existing single-trial numbers:
  - **base** — `Qwen/Qwen3.5-9B`, no adapter, baseline harness (`configs/baseline_harness.yaml`).
  - **sft** — rep22 promoted LoRA `outputs/sft/tmax_coev_rep22_i1` + `configs/rep22_best_tb21_transfer.yaml`.
  - **rl** — rep46 RL2 full checkpoint `outputs/rl/tmax_coev_rep46_i2` + `configs/rep46_rl_best_tb21_transfer.yaml`.
- **Trial 1 already exists** (2026-09-08). This job runs **trials 2 and 3 only** — six full-89 evaluations, sequentially, in one allocation. Per-task pass@3 is then computed across the three trials.
- Sampling is the only thing that varies between trials: same model, same harness, same task list, same `TB2_MAX_STEPS=120` and `TB2_CONCURRENT=2`.

### Prerequisites

The three trial-1 run directories must still be present under `.benchmarks/tb2/` on the cluster, since the summarizer reads them as trial 1:

```
.benchmarks/tb2/base-qwen35-9b-tb21-20260908-071605
.benchmarks/tb2/rep22-best-tb21-native-20260908-034352
.benchmarks/tb2/rep46-rl2-rep19h1-tb21-native-20260908-063705
```

Each must contain 89 `*/result.json`. If any are missing or were pruned, please say so before submitting — we would then need a third trial instead of two.

### Launch

[The sbatch script](https://github.com/chenjix/harnessx-backup/blob/main/scripts/slurm/tb2/h200_tb21_pass_at_3.sbatch) holds the full configuration and requests up to 3 days on `ml.p5en.48xlarge` / `interactive-ai`. Use the configured cluster checkout with working harness/vLLM environments; setup details in [environment](https://github.com/chenjix/harnessx-backup/blob/main/docs/ENVIRONMENT.md).

```bash
cd /fsx/home/jixuan.chen/harnessx-backup  # adjust to the cluster checkout
git pull --ff-only

# Confirm the three trial-1 run directories above each hold 89 results:
for d in base-qwen35-9b-tb21-20260908-071605 \
         rep22-best-tb21-native-20260908-034352 \
         rep46-rl2-rep19h1-tb21-native-20260908-063705; do
  printf '%s\t%s\n' "$d" "$(find ".benchmarks/tb2/$d" -mindepth 2 -maxdepth 2 -name result.json | wc -l)"
done

bash scripts/tb2/submit_pass_at_3.sh
```

That submits one job and writes a manifest to `outputs/runs/tb21-pass-at-3-<STAMP>.tsv`. Once the job finishes:

```bash
python3 scripts/tb2/summarize_pass_at_3.py \
  --manifest outputs/runs/tb21-pass-at-3-<STAMP>.tsv
```

It prints per-model pass@1 for each of the three trials and pass@3 out of 89, and writes per-task detail to `outputs/runs/tb21-pass-at-3-<STAMP>-results.tsv`.

Evaluations that fail are logged and the job continues, so a single bad evaluation does not lose the other five. If the allocation times out, resubmitting with the same stamp resumes — evaluations whose 89 results are already on disk are skipped:

```bash
PASS_AT_3_STAMP=<STAMP> \
PASS_AT_3_MANIFEST=outputs/runs/tb21-pass-at-3-<STAMP>.tsv \
  bash scripts/tb2/submit_pass_at_3.sh
```

Walltime is the main uncertainty: a single full-89 evaluation previously got its own 1-day allocation, and this job chains six. 3 days should cover it, but please check `squeue` before the limit if it looks slow, rather than letting it hit the wall.

### Deliverables

- Git SHA, Slurm job ID, launch command, and the submission manifest path.
- Slurm stdout/stderr from `/fsx/home/jixuan.chen/logs/tb21_pass_at_3_<jobid>.{out,err}`, including the per-evaluation `PASS`/`FAIL`/`SKIP` lines.
- The six trial-2/3 run directory names under `.benchmarks/tb2/`, with the result count for each.
- The `summarize_pass_at_3.py` stdout table and the `-results.tsv` per-task file.
- If any evaluation failed, which model/trial and the relevant log excerpt.

Scripts are syntax-checked locally; H200 execution is pending.

# Qwen3.5 × Terminal-Bench 2 full-stack experiments

Self-contained, script-driven codebase for Qwen3.5-4B/9B:

1. serve the model with the correct Qwen tool parser;
2. run gated HarnessX harness evolution on Terminal-Bench 2;
3. build same-model, quality-gated SFT data from the resulting trajectories;
4. train/evaluate a LoRA SFT adapter;
5. optionally build and train the existing **offline TB2 replay-GRPO** path.

The implementation was distilled from `/fsx/home/jixuan.chen/HarnessX` and the
2026-07-26 runs. Runtime outputs, model weights, secrets, vendored SDKs, and historical
benchmark directories are intentionally not copied.

## Important result-derived safeguards

- vLLM always uses `--enable-auto-tool-choice --tool-call-parser qwen3_xml`.
  The old `hermes` setup silently returned `tool_calls: null` and invalidated scores.
- Every evaluation/evolve launch probes an actual structured Bash call before starting.
- A port that is already serving is rejected; a stale server must not be mistaken for
  the requested model.
- Harness evolution uses regression gating (`REGRESSION_TOLERANCE`, default `1/15`).
- SFT uses only successful trajectories from the exact selected model, excludes fixed
  holdout tasks, requires structured tool calls, and validates native Qwen XML targets.
- Slurm controls GPU visibility. Do not hardcode `CUDA_VISIBLE_DEVICES=0` in sbatch jobs.
- SFT serving uses base + LoRA; merged Qwen3.5 checkpoints previously triggered vLLM
  architecture/vision routing issues.

Reference report and scores:

- `reference/results/REPORT-2026-07-26.md`
- `reference/results/scores-2026-07-26.json`

The report's measured result is important: Qwen3.5-9B clearly outperformed 4B under
paired runs, while the small SFT and TB2 harness-evolution gains did not replicate.
This codebase supports those experiments; it does not claim that every stage improves
the model.

## Layout

```text
benchmarks/terminal_bench_2/  Harbor/Docker TB2 adapter
harnessx/                     HarnessX runtime + meta-harness
recipe/tb2_evolver/           gated harness-evolution loop
recipe/tb2_sft/               trajectory filtering + LoRA trainer
recipe/slime/                 Slime/HarnessX replay-GRPO integration
configs/                      model profiles and baseline harness
scripts/                      launchers and preflight checks
reference/results/            historical report/results only
```

## Environment

```bash
cd /fsx/home/jixuan.chen/qwen35-tb2-fullstack
cp .env.example .env
chmod 600 .env
# edit .env; never commit it
bash scripts/doctor.sh
```

The current host already has separate serving and SFT environments. To create a fresh
serving/evolve environment:

```bash
bash scripts/setup_env.sh
```

Required external pieces:

- Docker daemon and Harbor CLI with `terminal-bench@2.0`;
- CUDA-compatible vLLM containing the `qwen3_xml` parser;
- a meta-agent API key for harness evolution;
- `trl`, `peft`, `datasets`, and `transformers` in `SFT_CONDA_ENV`;
- Slime + Megatron source/checkpoints only for replay-GRPO.

## Run inside the existing Slurm allocation

The scripts do not create or kill Slurm jobs. Run them on the allocated GPU node, for
example inside the currently active `hx_hold` allocation. Pick an unused GPU and port in
`.env`.

From the login node, launch into an existing allocation (the current one was job 27090
when this codebase was created):

```bash
bash scripts/run_on_job.sh 27090 4b evolve,sft,eval-sft
```

### One-command research pipeline

4B:

```bash
bash scripts/run_pipeline.sh 4b evolve,sft,eval-sft
```

9B:

```bash
bash scripts/run_pipeline.sh 9b evolve,sft,eval-sft
```

The generated `RUN_TAG` is exported once and reused by every stage.

### Individual stages

```bash
MODEL_SIZE=4b bash scripts/evolve.sh

# Reuse the same tag from evolve:
MODEL_SIZE=4b RUN_TAG=<tag> bash scripts/build_sft_data.sh
MODEL_SIZE=4b RUN_TAG=<tag> bash scripts/train_sft.sh

# Base/evolved-harness evaluation:
MODEL_SIZE=4b RUN_TAG=<tag> EVAL_SFT=0 bash scripts/evaluate.sh

# LoRA evaluation:
MODEL_SIZE=4b RUN_TAG=<tag> EVAL_SFT=1 bash scripts/evaluate.sh
```

Use `RESUME=1` with the same `RUN_TAG` to resume an interrupted evolve run.

Common overrides:

```bash
GPU=1 PORT=8301 NUM_ROUNDS=6 TB2_CONCURRENT=3 \
RUN_TAG=my-qwen35-9b-run MODEL_SIZE=9b \
bash scripts/evolve.sh
```

## Data and checkpoint flow

```text
TB2 Harbor results (.benchmarks/tb2/<tag>-r*-traj)
  ├─> gated harness configs (recipe/tb2_evolver/runs/<tag>/R*/config.yaml)
  ├─> same-model SFT JSONL (recipe/tb2_sft/data/<dataset>/)
  │     └─> LoRA (outputs/sft/)
  └─> offline replay JSONL (data/grpo/)
        └─> Slime replay-GRPO checkpoints (checkpoints/grpo/)
```

## GRPO scope — read before running

The existing GRPO integration is **offline TB2 replay-GRPO**, not online
Harbor-verifier-backed GRPO:

- prompts are extracted from completed TB2 sessions;
- the training rollout's Bash tool executes in the HarnessX training process, not in a
  fresh task-specific Harbor container;
- reward is behavioral (used Bash + non-empty final output), not the TB2 task verifier.

That objective can be useful as a tool-use warmup, but calling it “TB2 outcome RL” would
be technically wrong. The launcher therefore refuses to run unless explicitly enabled:

```bash
MODEL_SIZE=4b RUN_TAG=<tag> bash scripts/build_grpo_replay.sh

ALLOW_OFFLINE_TB2_REPLAY_GRPO=1 \
MODEL_SIZE=4b RUN_TAG=<tag> \
bash scripts/train_grpo.sh
```

Before that command, fill these model-specific values in `.env`:

- `SLIME_ROOT`, `MEGATRON_ROOT`, `DATA_ROOT`
- `MODEL_ARGS_SCRIPT_4B` / `MODEL_ARGS_SCRIPT_9B`
- `HF_CHECKPOINT_4B` / `HF_CHECKPOINT_9B`
- `REF_LOAD_4B` / `REF_LOAD_9B`

The launcher no longer inherits the old bug where `PROMPT_DATA` was ignored and the
math dataset was trained instead. It also no longer runs broad `pkill -9 python`.

## Toward true online TB2 GRPO

A true implementation still needs a rollout environment manager that:

1. creates one isolated Harbor/Docker task environment per sampled rollout;
2. routes each Bash call to that rollout's container;
3. invokes the task's real TB2 verifier at episode end;
4. returns verifier reward to Slime/veRL;
5. guarantees cleanup and concurrency limits.

Do not remove the offline warning until those five properties are implemented and
integration-tested.

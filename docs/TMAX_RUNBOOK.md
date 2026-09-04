# Tmax experiment handoff runbook

This is the canonical entry-point guide for continuing the Tmax experiments.
Run every command from the repository root. The stable command router is
`scripts/tmax/run.sh`; implementation scripts remain separate so each stage can
also be debugged independently.

Environment installation and dependency ownership are documented in
`docs/ENVIRONMENT.md`.
The ready-to-submit 4B/9B SFT, RL, and rollout-ablation settings are listed in
`docs/TMAX_EXPERIMENT_MATRIX.md`.
For a function-by-function explanation of the core loop, see
`docs/TMAX_LOOP_GUIDE.md`.

## 1. Pipeline map

```text
taxonomy parquet + task lists
          |
          v
  harness evolve (A) ----> holdout harness gate (B)
          |                         |
          +---- successful traces --+
                                    v
                         SFT-gen + corpus (B2/C)
                                    |
                                    v
                              LoRA SFT (D)
                                    |
                         optional online RL (D2)
                                    |
                                    v
                           holdout model gate (E)
                                    |
                           next iteration / resume
```

The frozen holdout-102 is evaluation-only. Evolve, SFT-gen, SFT, and RL must
exclude it. The resumable full loop tracks the incumbent **model and harness as
a pair**, rather than assuming every candidate is accepted.

| Stage | Public entry | Core implementation | Main output |
|---|---|---|---|
| Preflight | `bash scripts/tmax/run.sh preflight` | `scripts/tmax/run.sh` | terminal report |
| Harness evolve | `RUN_TAG=x bash scripts/tmax/run.sh evolve` | `recipe/tb2_evolver/run.py` | `recipe/tb2_evolver/runs/<tag>/` |
| Evaluation | `JOB_NAME=x bash scripts/tmax/run.sh eval` | `recipe/tmax_eval/run_eval.py` | `.benchmarks/tmax/<job>/` |
| Corpus build | full loop stage C | `recipe/tb2_sft/src/build_tmax_evolve_sft.py` | `recipe/tb2_sft/data/<name>/` |
| SFT | `SFT_DATASET_NAME=x bash scripts/tmax/run.sh sft` | `recipe/tb2_sft/src/train_sft_lora.py` | `outputs/sft/<name>/` |
| RL | `RL_DATASET_NAME=x RL_OUTPUT_DIR=x bash scripts/tmax/run.sh rl` | open-instruct `grpo_fast.py` | `outputs/rl/<name>/` |
| Full loop | `REPLICATE=N bash scripts/tmax/run.sh coevolve` | `scripts/tmax/run_loop_tmax_coevolve.sh` | `outputs/tmax_coevolve/repN/` |

Detailed algorithm and data-plane semantics live in
`docs/COEVOLVE_PIPELINE.md`; RL debugging history lives in `docs/RL_STATUS.md`.

## 2. Fresh-clone setup

1. Clone the repository and enter its root.
2. Build the required profiles with `scripts/tmax/setup_envs.sh`; follow
   `docs/ENVIRONMENT.md` for CUDA/vLLM details.
3. Copy `configs/tmax.env.example` to the ignored `configs/tmax.env`, replace
   the four interpreter paths, and configure the meta-model credential.
4. Load it: `set -a; source configs/tmax.env; set +a`.
5. Place or symlink the taxonomy parquet at the `TAXONOMY_PARQUET` path.
6. Materialize the two generated env JSONLs (they are intentionally ignored):

```bash
IDS_JSON=recipe/tb2_evolver/tasks_tmax_only200.json \
OUT_DIR=recipe/tb2_sft/data/qwen35_9b_tmax_only200 \
  bash scripts/tmax/build_tmax_envs_from_taxonomy.sh

IDS_JSON=recipe/tb2_evolver/tasks_tmax_evolve50_list.json \
OUT_DIR=recipe/tb2_sft/data/tmax_evolve50 \
  bash scripts/tmax/build_tmax_envs_from_taxonomy.sh
```

7. Run `bash scripts/tmax/run.sh preflight`. Resolve every `MISS`; warnings are
   acceptable only when the corresponding feature (Slurm or a meta provider)
   is not being used.
8. On a new compute node, run Docker preflight/image preparation before
   reserving a long GPU job. The full H200 launcher performs this check too.

Never commit `configs/tmax.env`, credentials, model weights, generated JSONL,
Docker archives, `outputs/`, `.benchmarks/`, or logs.

## 3. Recommended full experiment

Choose a replicate number not already present under `outputs/tmax_coevolve/`.
From the repo root:

```bash
set -a; source configs/tmax.env; set +a
REPLICATE=30 N_ITERS=3 ENABLE_RL=0 \
  bash scripts/tmax/run.sh submit-coevolve
```

This submits the 8xH200, 9B tournament harness-evolve + winner-only SFT recipe.
Enable online RL only after the SFT-only chain and the RL smoke/prove job pass:

```bash
REPLICATE=31 N_ITERS=1 ENABLE_RL=1 RL_EPISODES=128 \
  bash scripts/tmax/run.sh submit-coevolve
```

For a non-Slurm allocation, use `REPLICATE=30 N_ITERS=1 bash
scripts/tmax/run.sh coevolve`. It is the same loop without scheduler setup.

## 4. Run one stage

Harness evolve smoke (two tasks; use a unique tag):

```bash
RUN_TAG=mentor-evolve-smoke LIMIT=2 EVOLVE_ROUNDS=1 \
  bash scripts/tmax/run.sh evolve
```

Base-model evaluation smoke:

```bash
JOB_NAME=mentor-base-smoke LIMIT=2 TMAX_CONCURRENT=1 \
  bash scripts/tmax/run.sh eval
```

Evaluate an SFT adapter:

```bash
EVAL_SFT=1 LORA_PATH=outputs/sft/<run> LORA_NAME=<served-name> \
JOB_NAME=mentor-sft-eval bash scripts/tmax/run.sh eval
```

Train an already-built corpus:

```bash
SFT_DATASET_NAME=tmax_coev_rep30_i1 SFT_NAME=rep30-i1 \
  bash scripts/tmax/run.sh sft
```

Run RL from a merged/full checkpoint:

```bash
RL_DATASET_NAME=tmax_rl_rep30 RL_OUTPUT_DIR=outputs/rl/rep30 \
RL_INIT_MODEL=/absolute/path/to/full-checkpoint RL_N_TASKS=100 \
  bash scripts/tmax/run.sh rl
```

When starting from a LoRA adapter, set `ADAPTER_DIR` instead of
`RL_INIT_MODEL`; the RL entry merges it before training. Keep
`RL_PROMPT_SCHEMA=vanillux_instance_v1`, otherwise agents do not receive the
required submit instruction.

## 5. Resume, monitor, and hand off state

The full loop is idempotent at completed-stage boundaries. Re-submit the same
command with the same `REPLICATE`; `.done-*` markers cause completed work to be
skipped. Do not delete markers unless deliberately rerunning that stage.

The first files to inspect are:

```text
outputs/tmax_coevolve/repN/STATUS          current stage or failure
outputs/tmax_coevolve/repN/incumbent.tsv   accepted score/model/harness tuple
outputs/tmax_coevolve/repN/scores.tsv      evaluation history
outputs/tmax_coevolve/repN/timings.tsv     stage durations
logs/ or Slurm output                      detailed process logs
```

Before another person takes over, record: git commit SHA, replicate, Slurm job
ID, exact submission command, external parquet location/checksum, model/cache
locations, accepted incumbent row, and whether Docker images exist on shared
storage or only on a particular node.

## 6. Common failure boundaries

- Missing generated env JSONL: rebuild it from the taxonomy parquet (section 2).
- Docker image missing/full: use `docker_preflight.sh` and
  `prebuild_tmax_images.sh`; images are node-local unless exported.
- SFT import failure: `SFT_PYTHON` must contain torch, transformers, datasets,
  TRL, and PEFT. The serving venv is not automatically a training venv.
- RL parser or zero reward: Qwen3.5 uses `vllm_qwen3_xml`; preserve
  `vanillux_instance_v1` and the final submit command. See `docs/RL_STATUS.md`.
- Resume appears to use different tasks: keep the generated
  `evolve_set_iN.env`; resampling a resumed iteration invalidates its evidence.
- A harness or model loses the holdout gate: this is expected. Continue from
  `incumbent.tsv`, not merely the newest checkpoint/config on disk.

## 7. What belongs on GitHub

Before pushing, run:

```bash
git status --short
bash -n scripts/tmax/run.sh scripts/tmax/*.sh scripts/slurm/tmax/*.sbatch
python3 -m pytest -q recipe/tb2_evolver recipe/tmax_eval \
  recipe/tb2_sft/src/test_build_tmax_evolve_sft.py \
  recipe/tb2_sft/src/test_build_tmax_rl_dataset.py
```

Review untracked task lists/provenance JSON deliberately: small reproducibility
manifests normally belong in git; generated corpora, trajectories, and weights
do not. Commit code and docs only after checking that no absolute personal
paths or secrets were introduced.

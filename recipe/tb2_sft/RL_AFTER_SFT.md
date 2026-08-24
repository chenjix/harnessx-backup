# Tmax online RL (DPPO / grpo_fast)

Reference implementation: `tmax/training/open-instruct/scripts/tmax/RL/qwen35_9b.sh`
(official) — ours is the same `open_instruct/grpo_fast.py` command with a local
dataset and a single node instead of Beaker + 8 nodes.

## Train vs holdout

| Split | Tasks | Role |
|-------|-------|------|
| Evolve | 50 (rotating) | harness evolve + SFT corpus |
| RL train | ≤`RL_N_TASKS` taxonomy (prefers evolve-50) | GRPO/DPPO rollouts |
| Holdout | 102 | ratchet only — never RL-train |

`build_tmax_rl_dataset.py` refuses the holdout envs jsonl and hard-fails on any
holdout id in the selected set.

## The four things that made this not run

1. **`--truncate_importance_sampling_ratio_cap`** — the flag is
   `--truncated_...`. HfArgumentParser rejects unknown args, so grpo_fast exited
   before the first rollout. `scripts/tmax/rl_preflight.sh` now diffs every flag
   we pass against open_instruct's arg dataclasses so this cannot recur silently.
2. **`env_config.env_name` was `swerl_sandbox`** while we run
   `--tools swerl_vanillux_sandbox`. `data_loader._merge_env_config` keys
   per-sample env configs *by name*, so the entry landed under a name nothing
   read, the env got no `image`, and `_do_reset` raised
   "requires an explicit image per task" on every rollout.
3. **`env_config.image` was the `From:` base (`ubuntu:22.04`)**.
   `swerl_vanillux_sandbox` boots that image, uploads `tests/` at submit time,
   and **never runs `setup.sh`** — so the task's own files and packages were
   simply absent and every reward was 0. The official pipeline bakes `%post`
   into a per-task image (14601 tmax tasks → 14490 distinct images in
   `allenai/tmax-15k-open-instruct`); we now point `env_config.image` at the
   local content-hash tag `recipe/tmax_eval` already builds, and also write
   `task_data/<task>/image.txt` (the env's fallback lookup).
4. **No env had the deps.** grpo_fast needs ray + deepspeed + openenv-core +
   vllm + liger-kernel + flash-attn + ai2-olmo-core together; the serving venv
   has ray+vllm only and the SFT conda env has none of them.
   `scripts/tmax/setup_rl_env.sh` builds the env from open-instruct's own
   `uv.lock` (python 3.12, torch 2.10+cu128, prebuilt flash-attn wheel,
   causal-conv1d compiled against nvcc 12.8).

`setup.sh` is still written into `task_data/` for provenance, but nothing runs
it — treat the image as the environment.

## Verify (single 8-GPU node)

```bash
bash scripts/tmax/setup_rl_env.sh                      # once, 15-40 min
export RL_PYTHON=$PWD/tmax/training/open-instruct/.venv/bin/python
RL_DATASET_NAME=tmax_rl_smoke_data bash scripts/tmax/rl_preflight.sh
SMOKE_NAME=tmax_rl_smoke bash scripts/tmax/smoke_rl_grpo.sh
```

The smoke builds a 4-task dataset, builds those 4 sandbox images, and runs a
tiny DPPO job. `ADAPTER_DIR=` (empty) runs straight off the base model; point it
at an SFT adapter to warm-start, which merges LoRA → full weights first.

## Sizing on 8× A100-40GB

ZeRO-3 shards params+grads+Adam across the learners, so a 9B needs
~(18+18+72)/N GB per learner: 6 learners ≈ 18GB + activations fits, 2 learners
does not. vLLM engines only hold weights + KV.

```bash
RL_N_LEARNERS=6 RL_N_VLLM=2 RL_DEEPSPEED_STAGE=3 ...
```

The official 9B run uses 8 nodes (8 learners + 48 engines, `response_length
65536`); on one node keep `RL_RESPONSE_LENGTH` at 8-16k and expect much lower
throughput.

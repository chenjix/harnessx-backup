# External data

## Tmax taxonomy (RL train pool)

Expected path (symlink or copy):

```text
data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
```

Typical source: Hugging Face `allenai/TMax-SFT-16.5K` (or the taxonomy parquet used by your cluster cache).

```bash
# example — adjust to your HF cache layout
mkdir -p data/external/tmax-taxonomy/data
# ln -s ~/.cache/huggingface/hub/datasets--allenai--TMax-SFT-16.5K/.../train-*.parquet \
#   data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
```

Build RL set (excludes holdout-102, prefers evolve-50):

```bash
python -m recipe.tb2_sft.src.build_tmax_rl_dataset \
  --from-taxonomy --n-tasks 100 --seed 42 --name tmax_rl_train100
```

## Env JSONLs (local builds)

Generated under `recipe/tb2_sft/data/` (gitignored), e.g.:

- `tmax_evolve50/eval_task_set_with_envs.jsonl`
- `qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl`

Rebuild helpers: `scripts/build_tmax_allowlist_sft_data.sh`, `scripts/rebuild_tmax_only200_sft_data.sh`.

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

This parquet is also the pool the **rotating evolve set** draws new tasks from:
its rows carry `container_def` (Singularity def -> Docker image), `description`,
`test_initial_state` and `test_final_state`, which is everything
`recipe/tmax_eval/run_eval.py` needs to stand a task up. 2200 tasks total, of
which 102 are the eval holdout.

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

Both files above are required before any Tmax loop can run — a fresh clone has
neither. Regenerate them from the taxonomy parquet (no HF download needed):

```bash
IDS_JSON=recipe/tb2_evolver/tasks_tmax_only200.json \
OUT_DIR=recipe/tb2_sft/data/qwen35_9b_tmax_only200 \
  bash scripts/tmax/build_tmax_envs_from_taxonomy.sh      # holdout-102

IDS_JSON=recipe/tb2_evolver/tasks_tmax_evolve50_list.json \
OUT_DIR=recipe/tb2_sft/data/tmax_evolve50 \
  bash scripts/tmax/build_tmax_envs_from_taxonomy.sh      # evolve-50 (iteration 1)
```

Later iterations generate their own
`recipe/tb2_sft/data/tmax_coev_rep<N>_i<k>_evolveset/eval_task_set_with_envs.jsonl`
as part of the rotation stage.

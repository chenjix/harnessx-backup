# External Tmax data

Large datasets are not stored as ordinary Git objects. The local file
`external/tmax-taxonomy/data/train-00000-of-00001.parquet` is currently a
machine-specific symlink into the Hugging Face cache and is intentionally not
committed.

Recreate it from the `allenai/TMax-SFT-16.5K` dataset, then place or symlink the
parquet at:

```text
data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
```

Expected snapshot used by the archived experiments:

```text
Hugging Face revision: 848e218270672080a92a94791c028ee43ae33387
Rows:                  2200
Bytes:                 9235491
SHA-256:               f915804e6e0154654f7894175d7032e261c84f4c693dba2163501a8a26aabf4d
```

After staging the parquet, generate the runnable evolve/holdout JSONL files by
following `docs/TMAX_RUNBOOK.md` or `docs/DATA.md`.

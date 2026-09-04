# Experiment artifacts retained in Git

This directory stores small, text-only evidence needed to interpret historical
runs. It is not a model/checkpoint store.

`logs/` currently contains snapshots of the RL rep19 holdout runs 3408/3416 and
the rep25 run 3847 as they existed on 2026-09-04. Slurm/Ray logs can be sparse
files: their apparent byte size may be much larger than their compressed Git
object size.

The ignored `outputs/` tree contains hundreds of GB of weights. For the GitHub
handoff, only the small coevolve state tables plus RL checkpoint pointer/config
metadata are force-added. Model weights (`*.safetensors`), optimizer state,
tokenizers, generated datasets, Docker images, and caches remain excluded.

For durable weight storage, publish checkpoints to an artifact/model registry
or Git LFS-backed release and record the URI plus checksum here. Ordinary GitHub
Git objects are not suitable for 0.5–18 GB checkpoint files.

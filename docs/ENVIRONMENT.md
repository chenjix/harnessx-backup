# Reproducing the Tmax runtime environments

Tmax uses three Python environments because vLLM serving, lightweight LoRA SFT,
and open-instruct distributed RL have different dependency and CUDA constraints.
Do not assume that an interpreter that can serve a model can also train it.

## Environment matrix

| Environment | Used by | Dependency source | Suggested path |
|---|---|---|---|
| Harness/eval | harness evolve, Docker evaluation, data builders, vLLM serving | root `pyproject.toml`, `uv.lock`, `requirements-evolve.txt` | `.venv-harness` |
| SFT | `scripts/train_sft.sh` | `requirements-sft.txt` | `.venv-sft` |
| RL | `scripts/tmax/train_rl_grpo.sh` | `tmax/training/open-instruct/pyproject.toml` and `uv.lock` | `tmax/training/open-instruct/.venv` |

System requirements are Linux, Git, Docker with daemon access, and NVIDIA
drivers visible through `nvidia-smi`. Slurm is needed only for the supplied
cluster launchers. RL additionally needs `uv`; CUDA extension compilation may
need `nvcc` matching the CUDA version reported by PyTorch.

## Install

From the repository root:

```bash
bash scripts/tmax/setup_envs.sh harness
bash scripts/tmax/setup_envs.sh sft
bash scripts/tmax/setup_envs.sh rl
```

`setup_envs.sh all` runs all three, but installing them separately makes GPU or
network failures easier to diagnose. Override destinations with
`HARNESS_VENV`, `SFT_VENV`, and `RL_VENV`.

The harness installer deliberately does not choose a vLLM wheel. vLLM builds
are coupled to the node's CUDA/PyTorch stack; follow the cluster's supported
installation method, install it into `.venv-harness`, and verify import/version.
Remote OpenAI-compatible serving does not require local vLLM.

The SFT requirements default to PyPI's PyTorch selection. If the cluster needs
a specific CUDA wheel, install that PyTorch build first, then install
`requirements-sft.txt`. Set `SFT_ATTN_IMPLEMENTATION=sdpa` unless a compatible
`flash-attn` is installed.

RL must use `scripts/tmax/setup_rl_env.sh`; do not translate its lockfile into a
hand-maintained requirements list. It performs a frozen `uv sync`, handles the
`causal-conv1d` build isolation issue, and verifies the distributed stack.

## Configure the pipeline

```bash
cp configs/tmax.env.example configs/tmax.env
```

Set at least:

```text
PYTHON_BIN=<repo>/.venv-harness/bin/python
VLLM_VENV=<repo>/.venv-harness
SFT_PYTHON=<repo>/.venv-sft/bin/python
RL_PYTHON=<repo>/tmax/training/open-instruct/.venv/bin/python
```

Then load and validate:

```bash
set -a; source configs/tmax.env; set +a

$PYTHON_BIN scripts/tmax/check_env.py harness
$SFT_PYTHON scripts/tmax/check_env.py sft
$RL_PYTHON scripts/tmax/check_env.py rl
bash scripts/tmax/run.sh preflight
```

`check_env.py` prints the interpreter, package versions, command availability,
and CUDA visibility. Preserve this output with the experiment metadata when
handing a run to another person.

## Reproducibility policy

- Root `uv.lock` is the resolved lock for HarnessX development dependencies.
- `requirements-evolve.txt` and `requirements-sft.txt` describe direct stage
  dependencies and supported version ranges; they are readable installation
  contracts, not machine-specific `pip freeze` dumps.
- The RL `uv.lock` is authoritative and installed with `--frozen`.
- Record `python --version`, `pip freeze`, `nvidia-smi`, Docker version, Git SHA,
  and external dataset checksum for each publication-quality run.
- Do not commit virtualenvs, Hugging Face caches, credentials, Docker images,
  generated datasets, model weights, or environment files containing secrets.

## Common setup failures

- `No module named trl/peft`: `SFT_PYTHON` points to the harness environment.
- `No module named deepspeed/openenv`: `RL_PYTHON` is not the locked
  open-instruct environment.
- PyTorch reports CPU-only: reinstall a CUDA-compatible build for the node.
- CUDA version mismatch while building `causal-conv1d`: make `CUDA_HOME` match
  `python -c 'import torch; print(torch.version.cuda)'` and rerun the RL setup.
- Docker SDK permission error: shell access to the `docker` command does not
  necessarily imply Python SDK access to `/var/run/docker.sock`.
- vLLM import/ABI failure: PyTorch, vLLM, CUDA runtime, and driver versions are
  incompatible; use one cluster-approved combination rather than upgrading one
  package in isolation.

#!/usr/bin/env python3
"""Validate one of the distinct Tmax Python environments."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version


PROFILES = {
    "harness": {
        "modules": ["harnessx", "boto3", "docker", "litellm", "pandas", "pyarrow", "yaml"],
        "commands": ["docker"],
    },
    "sft": {
        "modules": ["accelerate", "datasets", "peft", "safetensors", "torch", "transformers", "trl"],
        "commands": ["nvidia-smi"],
    },
    "rl": {
        "modules": [
            "accelerate", "datasets", "deepspeed", "docker", "liger_kernel",
            "openenv", "ray", "torch", "transformers", "vllm",
        ],
        "commands": ["docker", "nvidia-smi"],
    },
}

DIST_NAMES = {"yaml": "PyYAML", "openenv": "openenv-core", "liger_kernel": "liger-kernel"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES)
    args = parser.parse_args()

    spec = PROFILES[args.profile]
    missing: list[str] = []
    print(f"profile : {args.profile}")
    print(f"python  : {sys.executable}")
    print(f"version : {sys.version.split()[0]}")

    for module in spec["modules"]:
        found = importlib.util.find_spec(module) is not None
        if not found:
            print(f"MISS module  {module}")
            missing.append(module)
            continue
        try:
            package_version = version(DIST_NAMES.get(module, module))
        except PackageNotFoundError:
            package_version = "installed"
        print(f"OK   module  {module:<16} {package_version}")

    for command in spec["commands"]:
        path = shutil.which(command)
        if path:
            print(f"OK   command {command:<16} {path}")
        else:
            print(f"MISS command {command}")
            missing.append(command)

    if args.profile in {"sft", "rl"} and importlib.util.find_spec("torch"):
        import torch

        print(f"torch cuda build : {torch.version.cuda or 'CPU only'}")
        print(f"cuda available   : {torch.cuda.is_available()}")
        if not torch.cuda.is_available():
            missing.append("CUDA-enabled torch/runtime")

    if missing:
        print("\nEnvironment is NOT ready; missing: " + ", ".join(missing), file=sys.stderr)
        return 2
    print("\nEnvironment is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

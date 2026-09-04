#!/usr/bin/env python3
"""Translate an evolved Tmax harness policy into the RL bash-only protocol."""

from __future__ import annotations

import argparse
from pathlib import Path


RL_PROTOCOL = """

## Execution and submission protocol

- Work under `/home/user` unless the task explicitly says otherwise.
- Use the `bash` tool for inspection, editing, execution, and verification.
- Before submitting, re-read the task, check every requested output path, inspect
  the produced contents, and run a test that exercises the real behavior.
- Submit only when verification succeeds by running exactly
  `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` as its own command.
- A service needed by verification must still be alive and reachable when you
  submit. Start persistent services with `nohup` or an equivalent daemon mode;
  do not kill them after checking them.
- Do not claim success merely because a command exited zero. If verification
  exposes a problem, fix it and verify again before submitting.
""".strip()


def sibling_system_prompt(harness: Path) -> str:
    prompt = harness.parent / "system_prompt.txt"
    if prompt.is_file():
        return prompt.read_text(encoding="utf-8").strip()
    # The baseline prompt is the canonical fallback for configs whose bundle
    # has no prompt sidecar.
    fallback = Path(__file__).resolve().parents[2] / "configs" / "system_prompt.txt"
    if fallback.is_file():
        return fallback.read_text(encoding="utf-8").strip()
    return (
        "You are a terminal coding agent solving a single Linux task.\n"
        "Act autonomously and verify your work before finishing."
    )


def materialize(harness: Path, output: Path) -> None:
    if not harness.is_file():
        raise FileNotFoundError(harness)
    base = sibling_system_prompt(harness)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"{base}\n\n{RL_PROTOCOL}\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    materialize(args.harness.resolve(), args.output.resolve())
    print(f"RL harness policy: {args.harness} -> {args.output}")


if __name__ == "__main__":
    main()

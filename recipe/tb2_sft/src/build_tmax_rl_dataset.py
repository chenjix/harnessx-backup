#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build an open-instruct / SWERL RL dataset from Tmax taxonomy tasks.

Default policy for coevolve RL
------------------------------
* Train on up to ``--n-tasks`` (default **100**) tasks drawn from the 2.2k
  taxonomy parquet, **excluding** the 102-task holdout.
* Prefer keeping the evolve-50 set inside the RL pool (same seed tasks the
  harness already sees), then fill the remainder stratified by domain.
* Never accept the holdout env JSONL as ``--envs-jsonl``.

Writes under ``--out-root/--name``::

  train.jsonl          # open-instruct local mixer (preferred)
  hf_dataset/          # datasets.save_to_disk copy
  task_data/<task_id>/ # instruction.md, tests/test.sh, setup.sh
  task-data.tar.gz
  tasks_list.json      # task id list for provenance
  summary.json

Example::

  python -m recipe.tb2_sft.src.build_tmax_rl_dataset \\
    --from-taxonomy --n-tasks 100 --seed 42 \\
    --name tmax_rl_train100
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TAXONOMY = _ROOT / "data/external/tmax-taxonomy/data/train-00000-of-00001.parquet"
_DEFAULT_HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"
_DEFAULT_EVOLVE = _ROOT / "recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
_HOLDOUT_ENVS = _ROOT / "recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
_VANILLUX_YAML = (
    _ROOT / "tmax/training/open-instruct/open_instruct/environments/vanillux_prompts.yaml"
)

# Must match official allenai/tmax-15k-open-instruct user messages: the vanillux
# instance template (workflow + COMPLETE_TASK submit). grpo_fast discards the
# env reset observation, and --system_prompt_override_file replaces the system
# turn, so submit instructions have to live in the user message or the policy
# never issues the marker and every reward is 0.
PROMPT_SCHEMA = "vanillux_instance_v1"
SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

# Fallback if vanillux_prompts.yaml is missing. The live system turn is still
# overridden by swerl_vanillux_sandbox_system_prompt.txt at train time.
SYSTEM_PROMPT = (
    "You are a helpful assistant that can interact with a computer.\n\n"
    "Your response must include a THOUGHT section before your action where you\n"
    "explain your reasoning. After the THOUGHT, you must call the `bash` tool\n"
    "with EXACTLY ONE bash command (multiple commands chained with `&&` or `||`\n"
    "count as a single action).\n\n"
    "Failure to follow these rules — calling no tool, calling a tool other than\n"
    "`bash`, or omitting the THOUGHT — will cause your response to be rejected."
)

_INSTANCE_TEMPLATE_FALLBACK = f"""Please solve this task:

{{{{task}}}}

You can execute bash commands and edit files (with `sed`, `cat > file << 'EOF'`,
etc.) to implement the necessary changes.

## Recommended Workflow

6. Submit your changes and finish your work by issuing the following command:
   `echo {SUBMIT_MARKER}`
   Do not combine it with any other command. After this command, you cannot
   continue working on this task.
"""

_TEMPLATES: tuple[str, str] | None = None


def vanillux_templates() -> tuple[str, str]:
    """Return (system_template, instance_template) from the env yaml."""
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    system, instance = SYSTEM_PROMPT, _INSTANCE_TEMPLATE_FALLBACK
    if _VANILLUX_YAML.is_file():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(_VANILLUX_YAML.read_text(encoding="utf-8")) or {}
            system = str(data.get("system_template") or system).strip()
            instance = str(data.get("instance_template") or instance)
        except Exception:  # noqa: BLE001 — builder must still run without PyYAML
            pass
    _TEMPLATES = (system, instance)
    return _TEMPLATES


def wrap_user_message(description: str, instance_template: str | None = None) -> str:
    """Embed the taxonomy instruction in the official vanillux user prompt.

    Official mixer rows look like::

        Please solve this task:\\n\\n<instruction>\\n\\nYou can execute bash...
        echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT

    Reset() also renders this template, but pool.acquire_reset discards that
    observation, so the dataset user turn is the only copy the policy sees.
    """
    desc = (description or "").strip()
    if not desc:
        return desc
    if SUBMIT_MARKER in desc and desc.startswith("Please solve this task:"):
        return description
    tmpl = instance_template if instance_template is not None else vanillux_templates()[1]
    return tmpl.replace("{{task}}", desc)


def _load_task_ids(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("tasks") or raw.get("task_ids") or raw.get("task_names") or []
        if not items and all(isinstance(k, str) and k.startswith("task_") for k in raw):
            items = list(raw.keys())
    else:
        items = []
    out: list[str] = []
    for x in items:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id") or x.get("task")
            if tid:
                out.append(str(tid))
    return out


def parse_container_def(container_def: str) -> tuple[str, str]:
    image = "python:3.12-slim"
    for line in container_def.splitlines():
        stripped = line.strip()
        if stripped.startswith("From:"):
            image = stripped.split(":", 1)[1].strip()
            break
    in_post = False
    post_lines: list[str] = []
    for line in container_def.splitlines():
        if line.strip() == "%post":
            in_post = True
            continue
        if line.strip().startswith("%") and in_post:
            break
        if in_post:
            post_lines.append(line)
    return image, "\n".join(post_lines).strip()


def make_test_sh(test_final_state: str) -> str:
    return f"""#!/bin/bash
set -e
mkdir -p /logs/verifier

cat << 'TEST_EOF' > /tmp/test_final_state.py
{test_final_state}
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
"""


def make_setup_sh(post_commands: str) -> str:
    return f"""#!/bin/bash
set -e
{post_commands}
"""


def _write_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def _add_tar_file(tar: tarfile.TarFile, path: str, content: str, mode: int = 0o755) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=path)
    info.size = len(data)
    info.mode = mode
    tar.addfile(info, io.BytesIO(data))


def task_image(task_id: str, container_def: str, *, mode: str, registry: str) -> str:
    """The image the RL sandbox must boot for this task.

    ``open_instruct.environments.swerl_vanillux_sandbox`` never runs setup.sh: it
    boots ``env_config.image`` (or ``task_data/<task>/image.txt``), uploads
    ``tests/`` at submit time, and nothing else. The task's *environment* — the
    files and packages its %post creates — therefore has to be baked into the
    image, which is exactly what the official pipeline does (14601 tmax tasks map
    to 14490 distinct per-task images in allenai/tmax-15k-open-instruct).

    Handing it the ``From:`` base instead (``ubuntu:22.04``) boots a container
    with none of the task's state, so every rollout scores 0.

    * ``local``    — the content-hash tag ``recipe/tmax_eval`` builds and the
                     coevolve prebuild already populates. Right for a single node
                     where rollouts share the docker daemon.
    * ``registry`` — ``<registry>:<hash12>``, for a pushed multi-node setup.
    * ``base``     — the ``From:`` line. Only correct if the task needs no setup.
    """
    if mode == "base":
        image, _ = parse_container_def(container_def)
        return image
    digest = hashlib.sha1(container_def.encode()).hexdigest()[:12]
    if mode == "registry":
        if not registry:
            raise SystemExit("ERROR: --image-mode registry requires --image-registry")
        return f"{registry}:{digest}"
    safe = task_id.replace("/", "_")
    return f"tmax-eval:{safe}-{digest}"


def _row_to_record(
    row: dict[str, Any], *, env_name: str, image_mode: str, image_registry: str
) -> dict[str, Any] | None:
    task_id = str(row.get("task_id") or "")
    description = str(row.get("description") or "")
    test_final = str(row.get("test_final_state") or "")
    container_def = str(row.get("container_def") or "")
    if not task_id or not description or not test_final:
        return None
    image = task_image(task_id, container_def, mode=image_mode, registry=image_registry)
    system, instance = vanillux_templates()
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": wrap_user_message(description, instance)},
        ],
        "ground_truth": task_id,
        "dataset": "passthrough",
        # env_configs are keyed by env_name (data_loader._merge_env_config), so a
        # name that does not match --tools silently drops the per-task image and
        # the env dies with "requires an explicit image per task".
        "env_config": {"env_name": env_name, "task_id": task_id, "image": image},
        "source": "tmax_taxonomy_rl_train",
        "domain": row.get("domain"),
        # keep raw fields for env jsonl export / debugging
        "_raw": {
            "task_id": task_id,
            "description": description,
            "domain": row.get("domain"),
            "task_complexity": row.get("task_complexity"),
            "container_def": container_def,
            "test_final_state": test_final,
            "test_initial_state": row.get("test_initial_state"),
        },
    }


def select_from_taxonomy(
    *,
    taxonomy_parquet: Path,
    exclude: set[str],
    prefer_ids: list[str],
    n_tasks: int,
    seed: int,
    env_name: str,
    image_mode: str,
    image_registry: str,
) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as e:
        raise SystemExit("ERROR: pandas required to read taxonomy parquet") from e

    df = pd.read_parquet(taxonomy_parquet)
    by_id = {str(r["task_id"]): r for r in df.to_dict(orient="records")}

    chosen: list[str] = []
    for tid in prefer_ids:
        if tid in exclude:
            continue
        if tid in by_id and tid not in chosen:
            chosen.append(tid)
        if len(chosen) >= n_tasks:
            break

    remaining_need = n_tasks - len(chosen)
    if remaining_need > 0:
        pool = [tid for tid in by_id if tid not in exclude and tid not in chosen]
        # Stratify by domain when possible.
        buckets: dict[str, list[str]] = defaultdict(list)
        for tid in pool:
            dom = str(by_id[tid].get("domain") or "unknown")
            buckets[dom].append(tid)
        rng = random.Random(seed)
        for b in buckets.values():
            rng.shuffle(b)
        domains = sorted(buckets.keys())
        picked: list[str] = []
        while len(picked) < remaining_need and any(buckets[d] for d in domains):
            for d in domains:
                if len(picked) >= remaining_need:
                    break
                if buckets[d]:
                    picked.append(buckets[d].pop())
        chosen.extend(picked)

    rows: list[dict[str, Any]] = []
    for tid in chosen[:n_tasks]:
        rec = _row_to_record(
            by_id[tid], env_name=env_name, image_mode=image_mode, image_registry=image_registry
        )
        if rec:
            rows.append(rec)
    return rows


def load_from_envs_jsonl(
    path: Path, exclude: set[str], *, env_name: str, image_mode: str, image_registry: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            tid = str(raw.get("task_id") or "")
            if tid in exclude:
                continue
            rec = _row_to_record(
                raw, env_name=env_name, image_mode=image_mode, image_registry=image_registry
            )
            if rec:
                rows.append(rec)
    return rows


def write_dataset(records: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    if not records:
        raise SystemExit("ERROR: no RL tasks to write")
    missing_submit = [
        rec.get("ground_truth")
        for rec in records
        if SUBMIT_MARKER
        not in next(
            (m.get("content") or "" for m in rec.get("messages") or [] if m.get("role") == "user"),
            "",
        )
    ]
    if missing_submit:
        raise SystemExit(
            f"ERROR: {len(missing_submit)} RL row(s) missing {SUBMIT_MARKER} in the "
            f"user message, e.g. {missing_submit[:3]}. wrap_user_message must embed "
            "the vanillux instance template."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    task_data_dir = out_dir / "task_data"
    if task_data_dir.exists():
        import shutil

        shutil.rmtree(task_data_dir)
    task_data_dir.mkdir(parents=True, exist_ok=True)
    tar_path = out_dir / "task-data.tar.gz"

    clean_records: list[dict[str, Any]] = []
    env_rows: list[dict[str, Any]] = []
    with tarfile.open(tar_path, mode="w:gz") as tar:
        for rec in records:
            raw = rec.pop("_raw")
            task_id = raw["task_id"]
            description = raw["description"]
            test_final = raw["test_final_state"]
            container_def = raw["container_def"]
            _, post_commands = parse_container_def(container_def)

            image = rec["env_config"]["image"]
            task_dir = task_data_dir / task_id
            _write_text(task_dir / "instruction.md", description)
            _write_text(task_dir / "tests" / "test.sh", make_test_sh(test_final), mode=0o755)
            # image.txt is the env's fallback when env_config carries no image
            # (swerl_vanillux_sandbox._do_reset reads it before giving up).
            _write_text(task_dir / "image.txt", image + "\n")
            if post_commands:
                # Kept for provenance only: the vanillux env does NOT run setup.sh.
                # Whatever this installs has to already be inside `image`.
                _write_text(task_dir / "setup.sh", make_setup_sh(post_commands), mode=0o755)

            _add_tar_file(tar, f"{task_id}/instruction.md", description, mode=0o644)
            _add_tar_file(tar, f"{task_id}/tests/test.sh", make_test_sh(test_final))
            _add_tar_file(tar, f"{task_id}/image.txt", image + "\n", mode=0o644)
            if post_commands:
                _add_tar_file(tar, f"{task_id}/setup.sh", make_setup_sh(post_commands))

            clean_records.append(rec)
            env_rows.append(
                {
                    "task_id": task_id,
                    "rl_image": image,
                    "domain": raw.get("domain"),
                    "task_complexity": raw.get("task_complexity"),
                    "description": description,
                    "container_def": container_def,
                    "test_final_state": test_final,
                    "test_initial_state": raw.get("test_initial_state"),
                }
            )

    # open-instruct prefers local .jsonl mixer paths
    train_jsonl = out_dir / "train.jsonl"
    with train_jsonl.open("w", encoding="utf-8") as fh:
        for rec in clean_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    envs_jsonl = out_dir / "eval_task_set_with_envs.jsonl"
    with envs_jsonl.open("w", encoding="utf-8") as fh:
        for row in env_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    tasks_list = {
        "name": out_dir.name,
        "n_tasks": len(clean_records),
        "task_ids": [r["ground_truth"] for r in clean_records],
        "by_domain": {},
    }
    dom_counts: dict[str, int] = defaultdict(int)
    for r in clean_records:
        dom_counts[str(r.get("domain") or "unknown")] += 1
    tasks_list["by_domain"] = dict(sorted(dom_counts.items()))
    (out_dir / "tasks_list.json").write_text(json.dumps(tasks_list, indent=2) + "\n", encoding="utf-8")

    try:
        from datasets import Dataset

        ds = Dataset.from_list(clean_records)
        ds_dir = out_dir / "hf_dataset"
        if ds_dir.exists():
            import shutil

            shutil.rmtree(ds_dir)
        ds.save_to_disk(str(ds_dir))
        hf_path = str(ds_dir)
    except Exception as e:  # noqa: BLE001 — optional
        hf_path = None
        print(f"WARN: could not save hf_dataset ({e}); train.jsonl is enough for open-instruct")

    images = sorted({r["env_config"]["image"] for r in clean_records})
    missing_images: list[str] = []
    try:
        import subprocess

        have = set(
            subprocess.run(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                text=True, capture_output=True, timeout=120,
            ).stdout.split()
        )
        missing_images = [i for i in images if i not in have]
    except Exception:  # noqa: BLE001 — docker may not be reachable from here
        missing_images = []

    summary = {
        "n_tasks": len(clean_records),
        "prompt_schema": PROMPT_SCHEMA,
        "env_name": clean_records[0]["env_config"]["env_name"],
        "n_images": len(images),
        "n_images_missing_locally": len(missing_images),
        "images_missing_locally": missing_images[:20],
        "train_jsonl": str(train_jsonl),
        "envs_jsonl": str(envs_jsonl),
        "hf_dataset": hf_path,
        "task_data_dir": str(task_data_dir),
        "task_data_tarball": str(tar_path),
        "tasks_list": str(out_dir / "tasks_list.json"),
        "task_ids": [r["ground_truth"] for r in clean_records],
        "by_domain": tasks_list["by_domain"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out_dir}: n_tasks={len(clean_records)} "
        f"train.jsonl + task_data/ domains={dict(tasks_list['by_domain'])}"
    )
    print(f"  env_name={clean_records[0]['env_config']['env_name']}  images={len(images)}")
    if missing_images:
        print(
            f"  WARNING: {len(missing_images)} of {len(images)} task image(s) are NOT on this "
            f"node, e.g. {missing_images[:3]}\n"
            f"           Every rollout on those tasks fails to reset. Build them first:\n"
            f"           SHARED_BASE=1 ENVS_JSONL={out_dir}/eval_task_set_with_envs.jsonl \\\n"
            f"             bash scripts/tmax/prebuild_tmax_images.sh"
        )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-root", type=Path, default=_ROOT / "recipe/tb2_sft/data")
    ap.add_argument("--exclude-tasks", type=Path, default=_DEFAULT_HOLDOUT)
    ap.add_argument(
        "--exclude-extra",
        type=Path,
        default=None,
        help="Additional task ids to drop (unanimous/mastered from evolve select).",
    )
    ap.add_argument("--n-tasks", type=int, default=100, help="Target RL train size (default 100)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--from-taxonomy",
        action="store_true",
        help="Sample from taxonomy parquet (recommended for RL-100).",
    )
    ap.add_argument("--taxonomy-parquet", type=Path, default=_DEFAULT_TAXONOMY)
    ap.add_argument(
        "--prefer-tasks",
        type=Path,
        default=_DEFAULT_EVOLVE,
        help="Task ids to keep first (default: evolve-50 list).",
    )
    ap.add_argument(
        "--envs-jsonl",
        type=Path,
        default=None,
        help="Optional explicit envs jsonl (must not be holdout-102).",
    )
    ap.add_argument(
        "--env-name",
        default="swerl_vanillux_sandbox",
        help="Must match --tools passed to grpo_fast; env_configs are keyed by it.",
    )
    ap.add_argument(
        "--image-mode",
        choices=("local", "registry", "base"),
        default="local",
        help="Where the per-task image lives (default: the local content-hash tag).",
    )
    ap.add_argument("--image-registry", default="", help="registry mode: <registry>:<hash12>")
    args = ap.parse_args()

    exclude = set(_load_task_ids(args.exclude_tasks))
    exclude |= set(_load_task_ids(args.exclude_extra))
    prefer = _load_task_ids(args.prefer_tasks)

    if args.envs_jsonl is not None:
        envs = args.envs_jsonl
        if envs.resolve() == _HOLDOUT_ENVS.resolve():
            raise SystemExit(
                "REFUSING: envs-jsonl is the 102-task holdout file. "
                "RL must train on a non-holdout pool (use --from-taxonomy)."
            )
        if not envs.is_file():
            raise SystemExit(f"ERROR: missing envs jsonl: {envs}")
        records = load_from_envs_jsonl(
            envs, exclude, env_name=args.env_name,
            image_mode=args.image_mode, image_registry=args.image_registry,
        )
        if args.n_tasks and len(records) > args.n_tasks:
            rng = random.Random(args.seed)
            # keep prefer ids, then fill
            by_id = {r["ground_truth"]: r for r in records}
            chosen = [by_id[t] for t in prefer if t in by_id][: args.n_tasks]
            rest = [r for r in records if r["ground_truth"] not in {c["ground_truth"] for c in chosen}]
            rng.shuffle(rest)
            records = chosen + rest[: max(0, args.n_tasks - len(chosen))]
    else:
        # default path: taxonomy → 100
        if not args.taxonomy_parquet.is_file():
            raise SystemExit(f"ERROR: taxonomy parquet missing: {args.taxonomy_parquet}")
        records = select_from_taxonomy(
            taxonomy_parquet=args.taxonomy_parquet,
            exclude=exclude,
            prefer_ids=prefer,
            n_tasks=args.n_tasks,
            seed=args.seed,
            env_name=args.env_name,
            image_mode=args.image_mode,
            image_registry=args.image_registry,
        )

    # Safety: no holdout leakage
    leaked = [r["ground_truth"] for r in records if r["ground_truth"] in exclude]
    if leaked:
        raise SystemExit(f"ERROR: holdout leakage in RL set: {leaked[:5]}")

    out_dir = args.out_root / args.name
    write_dataset(records, out_dir)


if __name__ == "__main__":
    main()

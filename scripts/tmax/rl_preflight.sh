#!/usr/bin/env bash
# Check everything the Tmax RL run needs, cheaply, before requesting GPUs.
#
#   RL_DATASET_NAME=tmax_rl_smoke_data bash scripts/tmax/rl_preflight.sh
#   RL_PYTHON=/path/to/open-instruct/.venv/bin/python bash scripts/tmax/rl_preflight.sh
#
# Six checks, in the order they bite:
#   1. every --flag we pass exists in open_instruct's arg dataclasses. A single
#      wrong name makes grpo_fast exit before the first rollout — that is exactly
#      how `--truncate_importance_sampling_ratio_cap` (missing "d") sat in this
#      script silently. Static (ast), so it needs no RL deps installed.
#   2. RL deps importable in RL_PYTHON.
#   3. dataset rows match what the official pipeline produces: dataset=passthrough,
#      env_config.env_name == the --tools value, one image per task.
#   4. every task image present locally (the env raises if one is missing).
#   5. task_data layout the env actually reads: instruction.md, tests/test.sh.
#   6. docker + GPUs.

set -uo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

OI="${OPEN_INSTRUCT_ROOT:-$ROOT/tmax/training/open-instruct}"
RL_DATASET_NAME="${RL_DATASET_NAME:-}"
DATA_ROOT="${RL_DATA_ROOT:-${RL_DATASET_NAME:+$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME}}"
TOOLS_NAME="${RL_ENV_NAME:-swerl_vanillux_sandbox}"
PY="${PYTHON_BIN:-${VLLM_VENV:-$HOME/.venv}/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"
RL_PY="${RL_PYTHON:-$OI/.venv/bin/python}"

rc=0
note() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; rc=1; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  \033[1;33mwarn\033[0m %s\n' "$*"; }

note "1. grpo_fast flags used by ${TRAIN_SCRIPT:-scripts/tmax/train_rl_grpo.sh}"
OI="$OI" TRAIN_SCRIPT="${TRAIN_SCRIPT:-}" "$PY" - <<'PY' || rc=1
import ast, os, re, sys
from pathlib import Path

OI = Path(os.environ["OI"])
known: set[str] = set()
for f in OI.glob("open_instruct/**/*.py"):
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
            or (isinstance(d, ast.Call) and (
                (isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                or (isinstance(d.func, ast.Attribute) and d.func.attr == "dataclass")))
            for d in node.decorator_list
        ):
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    known.add(st.target.id)
    known.update(
        m.group(1).replace("-", "_")
        for m in re.finditer(r'add_argument\(\s*"--([A-Za-z0-9_\-]+)"', f.read_text(encoding="utf-8", errors="replace"))
    )

script = Path(os.environ.get("TRAIN_SCRIPT") or "scripts/tmax/train_rl_grpo.sh").read_text()
# Extract exactly the CMD=( ... ) array, not "everything after grpo_fast.py".
# The loose version swept up flags belonging to neighbouring shell commands the
# moment anything was appended below the launch — `timeout --signal/--kill-after`,
# `ray stop --force`, `docker ps --filter` all got reported as bogus grpo_fast
# flags and hard-failed a job that was otherwise fine.
m = re.search(r"^CMD=\(\n(.*?)^\)$", script, re.S | re.M)
if m is None:
    print("  FAIL could not locate the CMD=( ... ) block in train_rl_grpo.sh")
    print("       (this is a checker bug, not a flag problem)")
    sys.exit(1)
cmd_block = m.group(1)
# Hyphens matter: `--envs-jsonl` truncated to `--envs` looked like an unknown
# flag. Capture the whole name and normalise it the way argparse does.
FLAG_RE = re.compile(r"--([A-Za-z0-9][A-Za-z0-9_-]*)")
def _flags(text):
    return {f.replace("-", "_") for f in FLAG_RE.findall(text)}

flags = _flags(cmd_block)
# Conditional arrays are spliced in as "${NAME[@]}". Resolve exactly those — a
# blanket scan for `NAME=(--...)` also picked up build_args for the dataset
# builder, whose flags belong to a different program entirely.
spliced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}", cmd_block))
for line in script.splitlines():
    mm = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\+?=\(", line)
    if mm and mm.group(1) in spliced:
        flags |= _flags(line)
flags = sorted(flags)
if len(flags) < 30:
    print(f"  FAIL only extracted {len(flags)} flag(s) from the CMD block — "
          "extraction is broken (checker bug, not a flag problem)")
    sys.exit(1)
bad = sorted({f for f in flags if f not in known})
import difflib
for f in bad:
    near = difflib.get_close_matches(f, known, n=2, cutoff=0.7)
    print(f"  FAIL --{f}" + (f"   did you mean {['--'+n for n in near]}" if near else ""))
print(f"  ok   {len(set(flags)) - len(bad)}/{len(set(flags))} flags exist in open_instruct")
sys.exit(1 if bad else 0)
PY

note "2. RL python deps ($RL_PY)"
if [[ ! -x "$RL_PY" ]]; then
  bad "no interpreter at $RL_PY — run: bash scripts/tmax/setup_rl_env.sh"
else
  miss="$("$RL_PY" - <<'PY'
import importlib.util as u
need = ["ray","deepspeed","openenv","vllm","torch","transformers","liger_kernel","datasets","docker"]
print(" ".join(m for m in need if u.find_spec(m) is None))
PY
)"
  if [[ -n "$miss" ]]; then
    bad "missing in RL python: $miss  → bash scripts/tmax/setup_rl_env.sh"
  else
    ok "ray/deepspeed/openenv/vllm/liger/docker all importable"
  fi
fi

if [[ -z "$DATA_ROOT" ]]; then
  note "3-5. dataset checks skipped (set RL_DATASET_NAME=...)"
else
  note "3-5. dataset $DATA_ROOT"
  DATA_ROOT="$DATA_ROOT" TOOLS_NAME="$TOOLS_NAME" \
  IMAGES_OPTIONAL="${IMAGES_OPTIONAL:-0}" "$PY" - <<'PY' || rc=1
import json, os, subprocess, sys
from pathlib import Path

d = Path(os.environ["DATA_ROOT"])
tools_name = os.environ["TOOLS_NAME"]
fail = []
train = d / "train.jsonl"
if not train.is_file():
    print(f"  FAIL no {train}")
    sys.exit(1)
rows = [json.loads(l) for l in train.read_text().splitlines() if l.strip()]
print(f"  ok   {len(rows)} row(s) in train.jsonl")

names = {r.get("env_config", {}).get("env_name") for r in rows}
if names != {tools_name}:
    fail.append(f"env_config.env_name={names} but --tools is {tools_name}; "
                "env_configs are keyed by name, so the per-task image is dropped "
                "and the env raises 'requires an explicit image per task'")
else:
    print(f"  ok   env_name == {tools_name} on every row")

ds = {r.get("dataset") for r in rows}
if ds != {"passthrough"}:
    fail.append(f"dataset column = {ds}, official RL data uses 'passthrough'")
else:
    print("  ok   dataset == passthrough (matches official)")

marker = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
missing_submit = []
for r in rows:
    user = ""
    for m in r.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "user":
            user = str(m.get("content") or "")
            break
    if marker not in user or not user.startswith("Please solve this task:"):
        missing_submit.append(r.get("ground_truth"))
if missing_submit:
    fail.append(
        f"{len(missing_submit)} row(s) missing vanillux instance template "
        f"(Please solve this task: + {marker}) in the user message, e.g. "
        f"{missing_submit[:3]}. Rebuild with build_tmax_rl_dataset.py "
        f"(prompt_schema=vanillux_instance_v1)."
    )
else:
    print(f"  ok   user messages embed {marker} (vanillux instance template)")

summary_path = d / "summary.json"
schema = ""
if summary_path.is_file():
    schema = str((json.loads(summary_path.read_text()) or {}).get("prompt_schema") or "")
if schema != "vanillux_instance_v1":
    fail.append(
        f"prompt_schema={schema!r}, expected vanillux_instance_v1 — "
        "stale mixer; train_rl_grpo.sh should rebuild"
    )
else:
    print("  ok   prompt_schema == vanillux_instance_v1")

imgs = {r["env_config"].get("image") for r in rows}
if None in imgs or "" in imgs:
    fail.append("some rows have no env_config.image")
elif imgs & {"ubuntu:22.04", "python:3.12-slim"}:
    fail.append(
        f"env_config.image is a BASE image ({sorted(imgs & {'ubuntu:22.04','python:3.12-slim'})}). "
        "The vanillux env never runs setup.sh, so the task environment must be baked "
        "into a per-task image — rebuild the dataset with --image-mode local")
else:
    print(f"  ok   {len(imgs)} per-task image(s), none of them a bare base image")

have = set(subprocess.run(["docker","images","--format","{{.Repository}}:{{.Tag}}"],
                          text=True, capture_output=True, timeout=120).stdout.split())
missing = sorted(i for i in imgs if i and i not in have)
if missing and os.environ.get("IMAGES_OPTIONAL") == "1":
    # Docker images are node-local. When the caller knows the trainer will build
    # them on its own node, their absence here says nothing about the run.
    print(f"  warn {len(missing)}/{len(imgs)} task image(s) not on THIS node — the "
          f"trainer builds them on the compute node and exports images.tar")
elif missing:
    fail.append(f"{len(missing)}/{len(imgs)} task image(s) not on this node, e.g. {missing[:3]}\n"
                f"         SHARED_BASE=1 ENVS_JSONL={d}/eval_task_set_with_envs.jsonl "
                f"bash scripts/tmax/prebuild_tmax_images.sh")
else:
    print(f"  ok   all {len(imgs)} task image(s) present locally")

td = d / "task_data"
bad_layout = []
for r in rows:
    tid = r["ground_truth"]
    if not (td / tid / "instruction.md").is_file() or not (td / tid / "tests" / "test.sh").is_file():
        bad_layout.append(tid)
if bad_layout:
    fail.append(f"{len(bad_layout)} task(s) missing instruction.md or tests/test.sh, e.g. {bad_layout[:3]}")
else:
    print("  ok   task_data/<task>/{instruction.md,tests/test.sh} present (what the env reads)")

for f in fail:
    print(f"  FAIL {f}")
sys.exit(1 if fail else 0)
PY
fi

note "6. docker + GPUs"
if [[ "${SKIP_GPU_CHECK:-0}" == "1" ]]; then
  echo "  skip GPU check (SKIP_GPU_CHECK=1 — running on a node without GPUs)"
fi
if docker info >/dev/null 2>&1; then ok "docker daemon reachable (CLI)"; else bad "docker unreachable — the sandbox needs it"; fi
# The sandbox actors use the python SDK, not the CLI. Those differ here: the CLI
# binary is setgid-docker, a plain process is not, so the CLI can succeed while
# every task reset fails with PermissionError(13) on the socket.
if [[ -x "$RL_PY" ]]; then
  if "$RL_PY" -c "import docker; docker.from_env(timeout=10).version()" >/dev/null 2>&1; then
    ok "docker reachable from the python SDK (what the sandbox uses)"
  else
    warn "python SDK cannot reach docker directly — the launcher publishes the socket"
    warn "on loopback for the sandbox actors (RL_DOCKER_PROXY=1, the default)"
  fi
fi
n_gpu="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | grep -c '^[0-9]' || true)"
if [[ "${SKIP_GPU_CHECK:-0}" == "1" ]]; then
  :
elif (( n_gpu > 0 )); then
  ok "$n_gpu GPU(s): $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
  (( n_gpu >= 2 )) || warn "1 GPU → train_rl_grpo.sh falls back to --single_gpu_mode"
else
  bad "no GPUs visible"
fi

echo
if (( rc == 0 )); then
  printf '\033[1;32mRL preflight PASSED\033[0m\n'
else
  printf '\033[1;31mRL preflight FAILED — fix the FAIL lines above\033[0m\n'
fi
exit $rc

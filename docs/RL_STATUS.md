# Tmax RL (DPPO) — where it stands

Written 2026-08-23, after a night of debugging. Read `bash ~/rl_status.sh` for the
live picture; this file is the interpretation.

## Verdict

The RL module **runs end to end**. Every stage has been executed and observed on
8x A100-40GB: container sandboxes, tool calls inside them, DPPO optimizer steps,
intermediate and final checkpoints, clean trainer exit. The reward path was
verified separately to return 1 for a correct solution.

What has **not** been achieved is a non-zero reward *during training*. That is a
policy-capability matter, not a pipeline defect — see "The remaining gap".

## The green run

Job **32121** (2026-08-23 12:45 UTC, ip-10-3-107-176): `COMPLETED 0:0` in 15:33.

```
sandbox pool: 10 concurrent container(s)
image store: 21G -> 21G free after reclaiming unused task images
Pool ready: 10 SWERLVanilluxSandboxEnv actors
reset attempt failures: 0
bash/avg_calls_per_rollout: 5.62 / 1.62 / 4.12 / 4.50   (steps 1-4, failure_rate 0.00)
training_step 1..4, epoch 1.00, 32 episodes
trainer exit status: 0
final checkpoint: outputs/rl/tmax_rl_smoke_data_32121/tmax_dppo_clean__42__1787489347/
                  (config.json + model.safetensors, 23G with the step checkpoints)
```

That is the reference for "the module runs": every stage executed, slurm reports
COMPLETED, and the exit status is the trainer's own.

## Evidence

| Stage | Evidence |
|---|---|
| Dependencies | 12 packages import cleanly in the uv-built env (`scripts/tmax/setup_rl_env.sh`) |
| CLI contract | 51/51 flags accepted by grpo_fast's real parser |
| Dataset | 8 tasks; `env_name` / `image` / `dataset` match `allenai/tmax-15k-open-instruct` field for field; 0/102 holdout ids leaked |
| Task images | 780MB `images.tar` on shared storage; a node without them loads in <1 min (verified job 32060) |
| Docker access | socket published on loopback for the SDK; `reset attempt` failures went 8/8 → 0 |
| Learners + engines | 6 + 2 on one node, ZeRO-3 |
| Weight sync | passes with `gather_whole_model=false` + `deepspeed_zpg=1` (previously OOM at 33GB or a 88-minute hang) |
| Agent in container | `bash/avg_calls_per_rollout` 3.88 (Qwen3.5-4B) / 4.00 (Qwen3-4B-Instruct), `failure_rate` 0.00 |
| Training | `training_step` 1→4, `epoch 1.00`, 32 episodes |
| Checkpoints | `step_2`, `step_4`, and a final HF checkpoint (`config.json` + `model.safetensors`) |
| Trainer status | `trainer exit status: 0` |
| Reward path | task `test_final_state` + our generated `test.sh` in a container with a correct solution → `/logs/verifier/reward.txt` = **1** |

## The remaining gap

`non_submitting_completion_fraction` stayed at 1.00 in every run: the policy never
issues `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`, so every episode scores 0 and
every advantage is 0. Token budget is not the cause — raising `response_length`
2048 → 8192 pushed truncation from 0.88 down to 0.25 and then 0.00 without
producing a single submission.

Both models tried are weak for this task family: Qwen3.5-4B is a base model, and
Qwen3-4B-Instruct-2507 is not trained for this harness. The official recipe RLs
from an **SFT'd** checkpoint with `response_length 65536`
(`tmax/training/open-instruct/scripts/tmax/RL/qwen35_9b.sh`), and the upstream
notes say ~65k output length matters specifically to avoid overlong negatives.

A second observation from the instruct-model probe: `vllm_hermes` parses tool-call
arguments with a plain `json.loads`, and shell commands routinely contain `\d`,
`\.` and similar, which are invalid JSON escapes unless doubled. 18
`JSONDecodeError: Invalid \escape` events appeared in one run
(`vllm/tool_parsers/hermes_tool_parser.py:111`), each silently dropping that tool
call. The XML parser used for Qwen3.5/3.6 does not go through JSON escaping and
produced zero parse errors across two runs, so the target family is also the more
robust one. Do not read the instruct-model probe as evidence about the harness.

Recommended next step, in order:
1. Produce an SFT adapter (the coevolve loop's stage D) and RL from the merged
   checkpoint — `ADAPTER_DIR=... bash scripts/tmax/train_rl_grpo.sh` merges it.
2. `MODEL_SIZE=9b RL_RESPONSE_LENGTH=32768` (pack 34816) with 6 learners; watch
   memory, drop to `RL_N_LEARNERS=7 RL_N_VLLM=1` if it OOMs.
3. Only then judge whether reward stays flat.

## Fixed along the way

Project/environment defects:

1. `--truncate_importance_sampling_ratio_cap` → `--truncated_...` (unknown flag, instant exit)
2. `env_config.env_name` must equal the `--tools` value, else the per-task image is dropped
3. `env_config.image` must be the per-task built image, not the `From:` base — the env never runs `setup.sh`, so a base image means an empty task and a silent 0 reward
4. No env had ray+deepspeed+openenv+vllm together → build from open-instruct's own `uv.lock`
5. `causal-conv1d` built against a cu130 torch pulled into uv's isolated build env → two-phase sync with `--no-build-isolation-package`
6. Rollout group must satisfy `samples × prompts ≥ learners / sequence_parallel_size`
7. Dataset must hold `max(async_steps,1) × unique_prompts` prompts
8. `_discover_tools_from_datasets` did not accept a local `.jsonl` mixer (upstream inconsistency; patched)
9. vLLM's sagemaker router probes `/opt/ml/model/model.py` with `pathlib.is_file()`, which does not swallow EACCES → kills the engine after weights load; fixed with `SAGEMAKER_MODEL_PATH`
10. `gather_whole_model=True` (the default) materialises the whole model per rank → OOM on 40GB
11. Docker images are node-local; prep on a login node left the compute node empty → export/load `images.tar`
12. `/usr/bin/docker` is setgid-docker but the python SDK is not, so sandbox resets got EACCES → publish the socket on loopback
13. Tool parser must match the model family: `vllm_qwen3_xml` for Qwen3.5/3.6, `vllm_hermes` for Qwen3 *-Instruct. A mismatch is silent: 0 tool calls, 0 reward
14. Rollout containers install packages, so the node's image store fills up → reclaim unused task images before training
15. `pool_size` defaulted to 64 while the rollout group needed 8: 56 idle containers, each running apt/pip, exhausted a node's 34GB image store and the node was drained. Pool size is now derived from the rollout shape

Defects introduced while fixing the above (all resolved):

16. The flag checker read every `--x` after `grpo_fast.py`, so neighbouring shell commands' flags were reported as unknown grpo_fast flags
16. A comment explaining the removal of a GPU-occupying loop contained the phrase the cluster's detector greps for
20. Minimising the sbatch dropped `RL_UNIQUE_PROMPTS` / `RL_ASYNC_STEPS`, breaking the dataset-size constraint
18. The post-run checkpoint check looked at the wrong directory level
19. `set -e` inside the EXIT trap replaced a successful run's exit status with 1

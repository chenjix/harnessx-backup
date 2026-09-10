# `run_loop_tmax_coevolve.sh` 中文导读

## 它是不是核心代码？

是，但更准确地说，它是 Tmax 实验的**核心 orchestration（编排）脚本**：

- 决定每个 iteration 依次执行哪些阶段；
- 在 subprocess 之间传递当前 model、harness、task set 和输出路径；
- 在 holdout-102 上决定是否接受新的 harness/model；
- 用 `.done-*` marker 支持中断后续跑；
- 记录 `STATUS`、score、timing 和 incumbent。

它不直接实现模型推理、harness 搜索算法、SFT optimizer 或 RL optimizer。
这些工作分别委托给 Python 模块和下游 shell 脚本。

仓库根目录的 `scripts/run_loop_tmax_coevolve.sh` 是 compatibility symlink，
真实文件是 `scripts/tmax/run_loop_tmax_coevolve.sh`。

## `main()` 在哪里？

脚本现在有显式 `main()`。文件的执行顺序是：

```text
读取 env/defaults
  -> 初始化路径、日志、lock、trap、进程预检
  -> 定义 helper functions
  -> main "$@"
       -> rotation 数据池预检
       -> 恢复/建立 incumbent
       -> anchor holdout
       -> for iteration k
       -> 输出总表
```

配置和函数仍需要先在顶层初始化，因此 `main()` 负责的是实际实验流程，
不是整个文件的每一行。

## 一次 iteration 的状态机

```text
输入 incumbent = (M*, H*)
          |
          v
 A0 rotate tasks（可选）
          |
          v
 A evolve harness: (M*, H*) -> H_candidate
          |
          v
 B holdout harness gate: H_candidate vs H*
          |
          +------ accepted/rejected ------> H_used
                                             |
                 +---------------------------+
                 |
        ENABLE_SFT=1?                         ENABLE_SFT=0
                 |                                  |
       B2 single-H rollout（可选）                   |
       C build successful corpus                     |
       D LoRA SFT -> M_candidate                     |
                 +------------------+----------------+
                                    |
                           ENABLE_RL=1?
                                    |
                         D2 GRPO/DPPO -> M_candidate
                                    |
                                    v
                         E holdout model gate
                                    |
                   accepted: update M* / rejected: keep M*
                                    |
                              next iteration
```

Holdout-102 只用于 B/E promotion，不进入 evolve、SFT 或 RL train。

## 阶段、代码位置和参数

### A0：旋转 evolve task set

入口函数：`rotate_task_set()`、`use_task_set_for_iter()`。

下游模块：

```text
recipe.tb2_sft.src.tmax_mastery
recipe.tb2_sft.src.build_tmax_evolve_task_set
```

主要参数：

| 参数 | 作用 |
|---|---|
| `ROTATE_EVOLVE_TASKS` | `1` 开启轮换，`0` 固定初始 evolve set |
| `ROTATE_FROM_ITER` | 从第几个 iteration 开始轮换 |
| `EVOLVE_SET_SIZE` | 每轮 evolve 任务数 |
| `MASTERY_MIN_SUCCESSES` | 判定 mastered 所需成功次数 |
| `MASTERY_REQUIRE_CORPUS` | mastered 是否还必须已进入 SFT corpus |
| `TAXONOMY_PARQUET` | 新任务池 |
| `EVOLVE_EXTRA_EXCLUDE` | 额外禁止抽取的 task ids |

每轮 task set 被写入 `outputs/tmax_coevolve/repN/evolve_set_iK.env`；resume
必须复用它，不能重新随机抽题。

### A：Harness evolve

入口函数：`run_evolve()`。

调用链：

```text
scripts/tmax/evolve_tmax.sh
  -> python -m recipe.tb2_evolver.run --eval-backend tmax
  -> fanout/tournament + tmax_adapter
```

主要参数：

| 参数 | 作用 |
|---|---|
| `EVOLVE_ROUNDS` | 每个 outer iteration 的 harness evolve 轮数 |
| `EVOLVE_EXTRA_ARGS` | fanout/tournament 的完整附加参数 |
| `META_MODEL` | 提议/修改 harness 的 meta model |
| `TMAX_CONCURRENT` | Tmax rollout 并发量 |
| `REGRESSION_TOLERANCE` | evolve 内部 gate 允许的退化幅度 |
| `EVOLVE_WALL_CLOCK_S` | meta evolve wall-clock 上限 |

输入 model/harness 是当前 incumbent；输出由 `resolve_evolved()` 从
`harness_evolve_state.json` 解析为 `cand_harness`。

### B：Harness holdout gate

入口函数：`run_holdout_eval()`、`paired_accept()`、`score_holdout()`。

它固定 incumbent model，只比较 candidate harness 和 incumbent harness。
实际评估通过：

```text
scripts/tmax/evaluate_tmax.sh
  -> python -m recipe.tmax_eval.run_eval
```

主要参数：

| 参数 | 作用 |
|---|---|
| `HARNESS_RATCHET` | `1` 时只有通过 gate 才采用新 harness |
| `PAIRED_EVAL_ALPHA` | paired sign-test 显著性阈值 |
| `ACCEPT_AGGREGATE_GAINS` | 默认 `1`；holdout 总通过数严格上涨时直接接受，无需 paired gate 显著 |
| `HOLDOUT_CONCURRENT` | holdout evaluation 并发量 |
| `HOLDOUT_TASKS_JSON` | 冻结的 holdout-102 |

当前实际 acceptance 是两条路径的 OR：总通过数严格上涨，或
`paired_accept()` 通过。`ACCEPT_TIES` 属于旧/兼容语义；默认不会让总分平局
绕过 paired gate。

### B2：Single-harness SFT rollout

入口函数：`run_sftgen_rollout()` 和 `run_sftgen_eval()`。

主要参数：

| 参数 | 作用 |
|---|---|
| `SFT_GEN_ROLLOUT` | 是否额外采集 SFT 数据 |
| `SFT_GEN_TASKS` | evolve 轨迹复用加新 rollout 后的 unique task 目标 |
| `SFT_GEN_CONCURRENT` | 单 harness rollout 并发量 |

这里使用 stage B 后的 `harness_used`。如果 candidate H 被拒绝，就使用旧
incumbent H，不会用 rejected harness 采训练数据。

### C：构建 SFT corpus

入口函数：`build_corpus()`。

下游模块：`recipe.tb2_sft.src.build_tmax_evolve_sft`。

| 参数 | 作用 |
|---|---|
| `MIN_TRAJS` / `MAX_TRAJS` | corpus 最少/最多轨迹数 |
| `PER_TASK` | 每个 task 最多保留的成功轨迹数 |
| `CUMULATIVE_CORPUS` | 是否加入之前 iterations 的数据 |
| `CORPUS_PREFER_CURRENT` | budget 优先留当前 iteration |
| `CORPUS_MAX_PREV_FRAC` | 历史轨迹最大比例 |
| `CORPUS_EVAL_HARNESS` | 是否注入 eval harness prompt |
| `CORPUS_WINNER_ONLY` | 是否只保留 fingerprint 匹配的 harness 轨迹 |

Single-harness setting 使用后两项均为 `1`；cross-harness ablation 使用均为
`0`。

### D：LoRA SFT

只有 `ENABLE_SFT=1` 时执行。

```text
scripts/train_sft.sh
  -> recipe.tb2_sft.src.train_sft_lora
```

关键参数包括 `SFT_EPOCHS`、`SFT_PYTHON`、`SFT_GPUS`，以及由 launcher
设置的 batch/sequence-length 参数。若 incumbent 是 LoRA，使用
`INIT_ADAPTER` 继续训练；若它是完整 RL checkpoint，则通过
`MODEL_OVERRIDE` 在该权重上新建 LoRA。

### D2：Online RL

只有 `ENABLE_RL=1` 时执行。`ENABLE_SFT=0, ENABLE_RL=1` 就是纯
harness-evolve + RL 路径，会跳过 B2/C/D。

```text
scripts/tmax/train_rl_grpo.sh
  -> recipe.tb2_sft.src.build_tmax_rl_dataset
  -> open_instruct/grpo_fast.py
  -> resolve_rl_ckpt()
```

主要参数是 `RL_N_TASKS`、`RL_EPISODES`、`RL_N_LEARNERS`、`RL_N_VLLM`、
`RL_RESPONSE_LENGTH`、`RL_SAMPLES_PER_PROMPT`、`RL_UNIQUE_PROMPTS` 和
`RL_TOOL_PARSER`。RL task builder 会排除 holdout。

### E：Model holdout gate

再次调用 `run_holdout_eval()`，这次固定 `harness_used`，比较训练前后的
model。通过 `paired_accept()` 后才更新 `prev_adapter`/incumbent。

如果失败且 `attempt < MAX_SFT_RETRIES`，脚本会追加
`EXTRA_EVOLVE_ROUNDS`，重新构建 corpus/训练/评估。纯 RL setting 通常把
`MAX_SFT_RETRIES=0`，不会进入 SFT retry 逻辑。

## 最重要的全局变量

| 参数 | 建议理解 |
|---|---|
| `REPLICATE` | 实验状态命名空间；并行 setting 必须不同 |
| `START_ITER` / `N_ITERS` | outer loop 起止 iteration |
| `MODEL_SIZE` | `4b`、`9b` 等模型规格，由 `_common.sh` 映射模型 ID |
| `ENABLE_SFT` | 是否执行 B2/C/D |
| `ENABLE_RL` | 是否执行 D2 |
| `SEED_HARNESS` | 初始 H；默认 `baseline_tmax_harness.yaml` |
| `INIT_LORA_PATH` | 初始 M 的 LoRA；空值表示 base model |
| `GPU_POOL` | serving/training 可见 GPU 列表 |

推荐不要直接手工组合所有变量，而是使用
`scripts/tmax/settings/run_*.sh`。这些 setting 把 4B/9B、SFT/RL、
single/cross-harness 的关键变量固定下来。

## Resume 如何工作

`mark()` 创建 `$STATE/.done-<stage>`，`done_p()` 在重启时检查它。
`time_stage()` 负责：

1. 更新 `STATUS`；
2. 执行阶段函数并保留真实退出码；
3. 记录 `timings.tsv`。

`STATE` 是 `outputs/tmax_coevolve/repN/`。同一 replicate 重提时，已完成
阶段被跳过，未完成阶段继续执行。除了明确要重跑某阶段，不要手工删除
`.done-*`。

## 关键状态变量

在阅读 iteration loop 时只需持续跟踪四个变量：

| 变量 | 含义 |
|---|---|
| `prev_harness` | 当前接受的 incumbent harness H* |
| `prev_adapter` | 当前接受的 incumbent model M*；可能是 LoRA 或完整 checkpoint |
| `INC_SCORE` | incumbent pair 的 holdout 分数 |
| `prev_eval_job` | incumbent 对应的 eval，用于 paired comparison |

临时变量 `cand_harness`、`harness_used`、`EVAL_MODEL` 分别表示本轮候选 H、
stage B 后实际使用的 H、本轮待 gate 的 M。只要区分 incumbent 和 candidate，
主循环就会清晰很多。

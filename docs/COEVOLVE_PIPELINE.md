# Tmax H↔M 协同进化：当前 pipeline 实现说明

本文描述 **2026-09 数据配方落地之后** 的 Tmax coevolve 全流程。入口脚本是
`scripts/tmax/run_loop_tmax_coevolve.sh`（`scripts/run_loop_tmax_coevolve.sh`
是指向它的 symlink）。H200 tournament 启动器：

```text
scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch   # 9B winner-only + SFT-gen（推荐）
scripts/slurm/tmax/h200_tmax_coevolve_tournament.sbatch       # 9B 同方法
scripts/slurm/tmax/h200_tmax_coevolve_tournament_4b.sbatch
scripts/slurm/tmax/h200_tmax_coevolve_tournament_2b.sbatch
```

正在跑的 job **不会**吃到这些改动：loop 脚本在 `sbatch` 时已经 sourced。
下一发 `sbatch` / 下一轮 `exec` 才会用新默认值。不要去热补正在跑的 3151。

---

## 1. 设计原则：四条平面，不要塌成一套 50 题

协同进化的对象是 **(harness H, model M)** 这对 incumbent，度量只在冻结的
**holdout-102** 上。训练数据另有自己的任务平面。混用会把「H 搜索信号」喂进
SFT 正例（rep19 mixed-fanout），或让 SFT 去模仿被 holdout 拒绝的 YAML
（rep19 / job 3150）。

| 平面 | 任务从哪来 | 并发 | 角色 |
|---|---|---|---|
| **H-search（evolve）** | 20–50 道非 holdout，mastered 轮换 | tournament 5 路 × `TMAX_CONCURRENT=4`（9B） | 给 meta-agent 搜 H；sibling `fe-c*` 是 **H 信号**，不是 SFT 正例 |
| **SFT-gen** | **100 道 unique 非 holdout**；复用本轮 evolve 评估，再补新题 | 单 harness，`SFT_GEN_CONCURRENT=8`，每题 1 条 | 给 SFT 采 **eval harness（H\*）** 下的成功轨迹 |
| **History** | 历轮 corpus，当前 iter 优先；旧数据 `prev_frac≤0.4` | — | 只保留 fingerprint 对得上 H\* 的 dir（现有 `winner_only`） |
| **Promotion** | 冻结 holdout-102，**永不训练** | `HOLDOUT_CONCURRENT=8`（单 harness） | 只按 **pass count** 决定接受/拒绝；平局可接受 |

文献上我们主张的是 **data recipe 如何调解 H↔M**，不是「H↔M 这件事本身」。
HELIX 的 sibling 轨迹进 SFT、把 SFT 采数塌到 evolve-50、平局再卡一层
sys_err，都已经在链上输过。

---

## 2. 一次外循环在磁盘上长什么样

以 `REPLICATE=22`、`MODEL_SIZE=9b` 为例。

```text
outputs/tmax_coevolve/rep22/
  STATUS, timings.tsv, scores.tsv
  incumbent.tsv          # 当前 (score, model, harness)
  best_model.tsv / best_harness.tsv
  evolve_set_i{k}.env    # EVOLVE_TASKS_JSON / EVOLVE_ENVS_JSONL
  mastered_i{k}.json
  sftgen_plan_i{k}_a{n}.json
  sftgen_pair.tsv        # 上次 B2 用过的 (H*, M)，用于跳过重复 rollout
  .done-*                # 阶段幂等标记

recipe/tb2_evolver/runs/tmax-coev-rep22-i{k}/
  _meta_v2/R0/config.yaml + system_prompt.txt     # 本轮 seed（上一轮 winner 物化过来）
  _meta_v2/R{n}/c{j}/                             # tournament 候选
  _meta_v2/R{n}/config.yaml + system_prompt.txt   # 本轮 gated winner

.benchmarks/tmax/
  tmax-coev-rep22-anchor0/          # 起始 holdout
  tmax-coev-rep22-i{k}-r0-traj/     # evolve R0（seed H）
  tmax-coev-rep22-i{k}-r0-fe-c{j}-traj/   # tournament siblings
  tmax-coev-rep22-i{k}-B-harness/   # stage B holdout
  tmax-coev-rep22-i{k}-sftgen-r0-traj/    # stage B2 补采
  tmax-coev-rep22-i{k}-E-sft-a0/    # stage E holdout

recipe/tb2_sft/data/
  tmax_coev_rep22_i{k}_evolveset/   # A0 写出的 50 题 + envs
  tmax_coev_rep22_i{k}_sftgen/      # B2 补采的新题 + envs
  tmax_coev_rep22_i{k}/             # SFT corpus（jsonl + summary.json）

outputs/sft/tmax_coev_rep22_i{k}/    # LoRA adapter
```

Holdout 题单：`recipe/tb2_evolver/tasks_tmax_only200.json`（102 ids）。
Iter 1 evolve 默认：`recipe/tb2_evolver/tasks_tmax_evolve50_list.json`。

---

## 3. 启动与 incumbent

`sbatch` 设好 env 后 `exec` loop。Loop 对每个 `REPLICATE` 持有
`outputs/tmax_coevolve/repN/.lock`。

1. **任务池预检**（`ROTATE_EVOLVE_TASKS=1`）：
   `build_tmax_evolve_task_set --check-only`，确认 taxonomy parquet
   在扣掉 holdout 和当前 evolve-50 之后还能抽出新题。
2. **bootstrap incumbent**：若没有 `incumbent.tsv`，对
   `(SEED_HARNESS 或 baseline_tmax_harness.yaml, INIT_LORA 或 base)`
   跑一次 `holdout-anchor0`，写入 `INC_SCORE`。
3. `for k in START_ITER..N_ITERS`。

每轮开始时的输入是 **上一轮结束时的 incumbent 对** `(prev_harness, prev_adapter)`，
而不是「这一轮刚搜出来的 H」——B 拒绝之后尤其如此。

---

## 4. Stage A0 — 旋转 evolve set

开关：`ROTATE_EVOLVE_TASKS=1`，从 `ROTATE_FROM_ITER`（默认 2）开始。

**Mastered**（`recipe/tb2_sft/src/tmax_mastery.py`）必须同时满足：

1. 该 task 出现在至少一份 SFT corpus `summary.json` 的 `selected_tasks` 里；
2. 在已扫描的 evolve traj 里成功次数 ≥ `MASTERY_MIN_SUCCESSES`（默认 1）。

然后 `build_tmax_evolve_task_set.py`：

```text
keep  = 上轮 set − mastered − holdout
new   = 从 2.2k taxonomy 按 domain 分层抽到 EVOLVE_SET_SIZE（默认 50）
        排除 holdout + mastered + keep + EVOLVE_EXTRA_EXCLUDE
```

写出 `recipe/tb2_sft/data/tmax_coev_repN_i{k}_evolveset/{task_ids.json,eval_task_set_with_envs.jsonl}`，
路径记入 `evolve_set_i{k}.env`。Resume 必须复用这份 set，禁止重抽。

Holdout-102 **永远**不进 evolve / SFT-gen / RL train。

---

## 5. Stage A — harness evolve

`scripts/tmax/evolve_tmax.sh` → `python -m recipe.tb2_evolver.run --eval-backend tmax`。

- `--baseline-config "$prev_harness"`：把 incumbent YAML **物化**进本轮 `R0/`。
- `_materialize_config_bundle` 复制 YAML、所有 `file://` processor/template 资产，
  **以及 YAML 旁边的 `system_prompt.txt`**。Tmax eval 读的是 sibling 文件，
  不是 YAML 里的 URI；以前只拷 YAML 时，下一轮 R0 会静默掉回
  `configs/tmax_system_prompt.txt` 那 5 行默认 prompt。
- Inner gate：`--regression-tolerance 0.04`（tournament sbatch 不再传 `-1`）。
  候选在 evolve-set 上相对 parent 掉超过 4% 则 revert。
  Revert 走 `_copy_config_as_round`，现在同样拷 sibling prompt。
- Tournament 默认（sbatch `EVOLVE_EXTRA_ARGS`）：

  ```text
  --fanout 5 --fanout-keep 5 --fanout-concurrent 5
  --fanout-mode tournament --fanout-eval-concurrent 5
  --regression-tolerance 0.04 --explore-every 0 --skip-final-score
  --num-rounds 2
  ```

  R0 评估 seed；R1 提 5 个候选全量评估；R2 再从 winner 提 5 个。
  9B 上每个候选 `TMAX_CONCURRENT=4`（5×4=20 worker），避免 5×8 把节点打满。

- 每个 evolve 评估 dir 由 `tmax_adapter` 写入 sidecar：
  `harness_config.yaml` + `system_prompt.txt`，供后面 `winner_only` 指纹匹配。

`resolve_evolved` 取出本轮 gated 的 `Rn/config.yaml` 作为 `cand_harness`。
没有 gated 输出则沿用 `prev_harness`。

---

## 6. Stage B — holdout 上的 H ratchet

用 **incumbent 模型** + **cand_harness** 跑 holdout-102
（job `tmax-coev-repN-i{k}-B-harness`），并发 `HOLDOUT_CONCURRENT=8`。

当前 promotion 规则：

- `after > before` 且 `ACCEPT_AGGREGATE_GAINS=1`（默认）→ 接受，即使 paired sign-test 不显著
- 或 paired sign-test 通过 → 接受
- aggregate 平局默认不接受；harness 可显式设置 `HARNESS_ACCEPT_SCORE_TIES=1`
- 否则拒绝

**不再**看 `eval_system_errors`。`agent_error` 已经让该题 `reward=0`，
pass count 里已经计过；再卡一层 sys_err=0 会把「真平局」卡死（4B 在 64 上就是这样）。

结果：

- **接受**：`harness_used = cand_harness`，写入 `best_harness.tsv` / `incumbent.tsv`
- **拒绝**：`harness_used = prev_harness`（incumbent）。后面的 SFT-gen 和
  corpus **都在 incumbent H 下采数**，不会去训练被 holdout 否决的 YAML。

若 YAML 路径与 incumbent 相同，跳过 B，复用 `INC_SCORE`。

---

## 7. Stage B2 — SFT-gen 补采（100 unique）

实现：`run_sftgen_rollout` + `recipe/tb2_sft/src/plan_tmax_sftgen.py`。
默认 `SFT_GEN_ROLLOUT=1`。`SFT_GEN_ROLLOUT=0` 回到只吃 evolve-50。

### 7.1 何时跳过

记录 `outputs/tmax_coevolve/repN/sftgen_pair.tsv` = `H*\tM`。
若本轮 `(harness_used, prev_adapter)` 与上次完全一样，**不再**补采。
典型情况：B 拒绝了新 H、E 也拒绝了新 M，下一轮还是同一对。

### 7.2 复用 + 补齐

```text
reuse = 本轮 evolve traj（{TAG}-r*-traj，含 fe-c*）里出现过的 task id
        ∪ 本轮 EVOLVE_TASKS_JSON
n_new = max(0, SFT_GEN_TASKS - |reuse|)
```

`{TAG}-sftgen-r0-traj` **不会**被算进 reuse（glob 是 `{tag}-r*`，
`i1-sftgen-r0` 对不上 `i1-r*`）。

`n_new>0` 时调用 `build_tmax_evolve_task_set`：

- `--size n_new --keep-tasks` 为空
- `--avoid-tasks` = 本轮 evolve set（不要把 50 道再跑一遍）
- `--exclude-tasks` = holdout-102
- 分层抽样，seed = `ROTATE_SEED + 1000*k + attempt`

写出 `recipe/tb2_sft/data/tmax_coev_repN_i{k}_sftgen/`。

### 7.3 评估

`JOB_NAME={TAG}-sftgen-r0-traj`，走 `scripts/evaluate_tmax.sh`：

- `HARNESS_CONFIG=$harness_used`（B 拒绝则是 incumbent）
- 模型 = incumbent `prev_adapter`（或 base）
- `TMAX_CONCURRENT=$SFT_GEN_CONCURRENT`（默认 8）
- 每题 1 条（eval 本身就是 1 rollout / task）

`recipe.tmax_eval.run_eval` 现在也会把 `harness_config.yaml` 和 sibling
`system_prompt.txt` 写进该 dir，否则 `winner_only` 指纹对不上，整份
sftgen 会被丢掉。

---

## 8. Stage C — winner-only corpus

`recipe/tb2_sft/src/build_tmax_evolve_sft.py`。

**Run tags（顺序）**：

```text
{TAG}                         # 本轮 evolve
{TAG}-sftgen                  # 本轮 B2（dir 存在才加）
{BASE}-i1 … {BASE}-i{k-1}     # 历史 evolve（CUMULATIVE_CORPUS=1）
{BASE}-i{p}-sftgen            # 历史 B2
```

`--prefer-run-tag {TAG}`：`meta.run.startswith(TAG)` 为真的都算「本轮」，
因此 `{TAG}-sftgen-r0-traj` 也被算进 current（不会被 `prev_frac` 裁掉）。
旧轮上限 `CORPUS_MAX_PREV_FRAC=0.4`。

**过滤**：

- `--eval-harness $harness_used` + `--winner-only`：traj dir 的 sidecar
  YAML+prompt 必须与 eval harness 字节级一致。Tournament 落败的 `fe-c*`
  全部丢弃。B 拒绝时，连本轮 winner 的 evolve dir 也会被丢掉（指纹对不上
  incumbent）——这是对的：rep22 i1 就是靠「B 拒绝 → 只留 r0-traj 股票 YAML」
  才把 holdout 从 78 拉到 82。
- 成功轨迹、tool-count 门、排除 holdout ids。
- **Quality**（`_evolve_sft_quality_score`）：偏向 8–20 tool turns 的干净成功；
  很长的 tool 循环和很慢的 wall-clock **减分**。旧分会给更长更慢加分，
  4B 刷分循环会被排到前面。
- `PER_TASK=1`：每题最多留 1 条。
- `MAX_PAIRS_PER_TRAJ=32`：单条轨迹最多 32 个 (prompt, completion) 对；
  9B 很少顶到，**没有**做 last-K 截断。
- 每条 SFT 样本的 system 位注入 **eval harness 的 sibling `system_prompt.txt`**，
  与 holdout 时 `SiblingSystemPromptBuilder` 读到的是同一份。

空 corpus（`n_selected_trajs < 1`）则本轮无法 SFT，跳出 retry 环。

---

## 9. Stage D / D2 / E — SFT、可选 RL、M ratchet

- **D**：`scripts/train_sft.sh`，从 `prev_adapter` continue LoRA（若有），
  `SFT_EPOCHS=2`，写出 `outputs/sft/tmax_coev_repN_i{k}/`。
  `ENABLE_SFT=0` 跳过 B2/C/D，adapter 目录改写到 `outputs/rl/`。
- **D2**（`ENABLE_RL=1`）：taxonomy 上 ≤`RL_N_TASKS` 的 DPPO/GRPO，
  **不含 holdout**。失败且没有 ckpt 会硬失败，避免把 SFT 分数标成 `_rl`。
  `ENABLE_SFT=0` 时从 base / 上一轮 RL ckpt 起步（不 merge LoRA）。
  Launcher：`scripts/slurm/tmax/h200_tmax_coevolve_evolve_rl.sbatch`。
- **E**：holdout-102，`harness_used` + 新模型，job `{TAG}-E-sft-a{attempt}`。
  同样 `HOLDOUT_CONCURRENT=8`；总 pass count 严格上涨即可接受，paired
  sign-test 仍会生成报告并作为另一条接受路径。

接受 → `prev_adapter` 换成新 adapter，更新 `INC_SCORE`（仅当分数上升）。
拒绝 → 若 `attempt < MAX_SFT_RETRIES`，同一 `RUN_TAG` 再跑
`EXTRA_EVOLVE_ROUNDS` 轮 evolve（seed 仍是 `harness_used`），再走一遍
B2（H/M 没变会 skip）+ C + D + E。Tournament sbatch 默认
`MAX_SFT_RETRIES=0`、`EXTRA_EVOLVE_ROUNDS=0`。

`EVAL_HARNESS_AFTER_EXTRA` 默认 0：extra evolve 产出的更新 H **不**在
retry 中途换掉，避免 E 的 before/after 用了两套 harness。

一轮结束 `save_incumbent k end`。全部 iter 完成后 `STATUS=DONE`。

---

## 10. Prompt 如何跟着 H 走

Tmax 运行时：

```text
recipe.tmax_eval.prompt_builder.SiblingSystemPromptBuilder
  → <active_config.yaml 同目录>/system_prompt.txt
```

因此 **YAML 挪到新目录时必须把 prompt 一起挪**。现在三处都会拷：

| 调用点 | 作用 |
|---|---|
| `_materialize_config_bundle` | iter k+1 的 R0 从 incumbent 物化 |
| `_copy_config_as_round` | inner gate revert / 空 round 占位 |
| `TmaxRoundAdapter.promote_round_output` | 本来就会从 winner 的 `cN/` 拷 prompt |
| `run_eval` 写 sidecar | 独立 holdout / SFT-gen dir 能被 `winner_only` 认出来 |

Meta-agent 编辑 prompt 仍然写在候选自己的 `cN/system_prompt.txt`；promote
从 **winner 目录**拷出，不要从 round root 拷（`test_promote_fanout.py`
锁死了这个回归）。

---

## 11. 关键环境变量（新默认）

| 变量 | 新默认 | 含义 |
|---|---|---|
| `SFT_GEN_ROLLOUT` | `1` | 打开 B2 |
| `SFT_GEN_TASKS` | `100` | unique 非 holdout 目标 |
| `SFT_GEN_CONCURRENT` | `8` | B2 单 harness 并发 |
| `PER_TASK` | `1` | corpus 每题最多 1 条 |
| `HOLDOUT_CONCURRENT` | `8` | 单 harness holdout |
| `TMAX_CONCURRENT` | tournament 9B 仍为 `4` | 5 路 evolve 共享节点 |
| `REGRESSION_TOLERANCE` / `EVOLVE_EXTRA_ARGS` | `0.04` | inner gate 打开 |
| `ACCEPT_TIES` | `1` | 平局只看 pass count |
| `CORPUS_WINNER_ONLY` | `1` | 丢掉落败 `fe-c*` |
| `CORPUS_EVAL_HARNESS` | `1` | 注入 eval prompt |
| `CORPUS_MAX_PREV_FRAC` | `0.4` | 历史轨迹占比上限 |
| `MAX_PAIRS_PER_TRAJ` | `32` | 单 traj pair 上限 |
| `EVOLVE_SET_SIZE` | `50` | H-search 平面大小 |

2B launcher 的 `HOLDOUT_CONCURRENT` 仍是 32（decode 便宜，刻意超订）。

---

## 12. 代码地图

```text
scripts/tmax/run_loop_tmax_coevolve.sh     外循环 A0→A→B→B2→C→D→E
scripts/tmax/evolve_tmax.sh                调 tb2_evolver.run
scripts/tmax/evaluate_tmax.sh              调 tmax_eval.run_eval
scripts/train_sft.sh                       LoRA SFT

recipe/tb2_evolver/run.py                  evolve + 物化 + prompt 链式拷贝
recipe/tb2_evolver/tmax_adapter.py         Tmax rollout + promote sidecar
recipe/tb2_evolver/fanout_tournament.py    5-way tournament
recipe/tmax_eval/run_eval.py               单次评估 + sidecar
recipe/tmax_eval/prompt_builder.py         sibling system prompt

recipe/tb2_sft/src/plan_tmax_sftgen.py     统计 reuse / n_new
recipe/tb2_sft/src/build_tmax_evolve_task_set.py
recipe/tb2_sft/src/tmax_mastery.py
recipe/tb2_sft/src/build_tmax_evolve_sft.py  winner_only corpus + quality
```

测试：

```bash
python recipe/tb2_sft/src/test_build_tmax_evolve_sft.py
python recipe/tb2_evolver/test_promote_fanout.py
```

---

## 13. 明确没有做的事

- **Sibling DPO / 用 `fe-c*` 当 SFT 正例**：phase 2，本配方禁止。
- **Last-K pair 截断**：9B 很少顶到 32，不是 blocker。
- **热补 job 3151**：它已经 sourced 了旧 loop；新默认只作用于下一发。
- **新的 harness fingerprint 体系**：继续用 YAML+prompt 字节对。
- **把 SFT-gen 塌回 evolve-50 或扩到 300 题全并行**：100 unique + 复用 evolve 评估。

---

## 14. 一次 iter 的时序（tournament 9B）

```text
A0  旋转 50 题（iter≥2）
A   R0: 1×50  (conc 4)
    R1: 5×50  (5 路 × conc 4)
    R2: 5×50
    inner gate 0.04；落败 fe-c* 留在磁盘但不进 SFT
B   holdout-102 × 1 harness × conc 8
    接受 → H* = 新 H；拒绝 → H* = 旧 H
B2  plan: reuse ~50，再抽 ~50 新题
    若 (H*,M) 变了：1×~50 conc 8（H* + incumbent M）
    若没变：skip
C   扫 {TAG} + {TAG}-sftgen + 历史；fingerprint==H* 才留；
    compact quality；PER_TASK=1；注入 H* prompt
D   LoRA 2 epoch
E   holdout-102 × H* × 新 M × conc 8
    接受/平局 → 新 incumbent M；否则保持旧 M
```

这就是当前实现的完整 H↔M 数据配方。

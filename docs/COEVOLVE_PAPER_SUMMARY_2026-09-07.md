# Co-evolution 实验总结（论文整理版）

数据截止：2026-09-07。主任务为 Tmax，统一评测集为冻结的 holdout-102；表中分数均为通过题数（满分 102），可直接按百分比近似解读。主要证据来自 `outputs/tmax_coevolve/rep*/scores.tsv`、`incumbent.tsv`、各轮 SFT corpus 的 `summary.json` 和实验脚本。旧的 `STATUS=RUNNING` 但进程/任务已经不存在的链视为 stalled，不视为仍在产生结果。2026-09-08 至 2026-09-10 的后续结果见 `COEVOLVE_RUNS_2026-09-10.md`；其中 rep50 已完成，不能再按本文件中的“正在运行”理解。

## 1. 论文希望验证的命题与当前证据强度

### 命题 A：Harness evolving + model update 优于只做 harness evolving

当前证据是“支持，但还缺严格 matched control”。

- 9B harness-only 的最好已确认结果是 base + evolved harness：77/78 左右起步，最好到 86（rep19：77→83→86；rep22 的 harness stage：78→76[拒绝]→86）。绝对增益约 +9。
- 9B Harness+SFT 的最好结果为 88（rep13：74→82，经 harness +8；再经 SFT +6 到 88），高于已确认的 harness-only 最好 86；但 rep13 只完成一轮，而且其数据 recipe 与 rep19 不同，不能作为完全受控的最终结论。
- 9B Harness+RL 链 rep46 在固定 rep19-H1 起点上得到 79→79→84→86，说明 model RL update 可以在 harness 已改进后继续提供 +7；同一 RL2 checkpoint 在 rep50 anchor 重评为 88，随后 harness evolve 到 90。这个链在单次评测上达到当前最高 90，但同一 checkpoint 的 84/88 波动说明必须多 seed/重复评测后再写显著性结论。
- 严格的 baseline-harness-only 四轮 tournament 控制（rep48）只完成 anchor 77 和 evolve，逐轮 R1–R4 的 holdout-102 复评尚未进入主 `scores.tsv`；因此“co-evolution 严格超过同预算 harness-only”的最终 matched claim 目前仍未闭环。

论文当前最稳妥表述：**模型更新能在已进化 harness 的基础上带来额外收益，并且最佳联合链超过目前观测到的 harness-only 峰值；严格同预算、多次重复的对照仍需补齐。**

### 命题 B：Co-evolution 效果受到 data recipe 影响

该命题已有较强的多链证据。

- 旧 mixed-sibling recipe（rep19）中，harness 从 77→83→86，但三次 SFT 分别为 −2、−2、−4，全部被拒绝：模型更新没有贡献。
- winner-only + single-incumbent SFT-gen recipe（rep22）第一轮在 harness 候选被拒绝后，仍把 stock-harness 的干净成功轨迹用于 SFT，使 78→82（+4）；第二、三、四轮 SFT 分别 −2、−1、−3，说明 recipe 改善了首轮，但后期仍饱和。
- 旧 sequential/all-evolve recipe 的结果高度不稳定：既有 +7（rep10 i1）、+6（rep12 i2 retry）、+6（rep13 i1），也有大量 0 或负增长。两个最大的后期 SFT 增长只在“额外 evolve + 重建 corpus”的 retry 中出现；当前 `MAX_SFT_RETRIES=0` 会失去这类收益。
- 4B mixed-sibling recipe（rep21）为 64→64/62，下一轮 60/62，完全没有提升；4B winner-only + SFT-gen（rep25）为 67→68→66，同样未提升模型。这说明 recipe 的作用还与模型容量/成功轨迹质量交互，9B recipe 不能直接假设迁移到 4B。

## 2. 统一实验设置

### 2.1 评测与任务平面

| 平面 | 数据 | 用途 | 是否进入训练 |
|---|---|---|---|
| H-search / evolve | 每轮 20–50 个非 holdout 任务；第 2 轮起按 mastered 轮换 | meta-agent 搜索 harness | 候选 sibling 轨迹原则上不直接作为正例 |
| SFT-gen | 最多 100 个 unique 非 holdout；复用本轮任务后补采 | 在最终采用的 H* 下收集成功轨迹 | 是 |
| History | 历轮 corpus；旧数据比例 `<=0.4` | 防遗忘并保持当前轮优先 | 是，需匹配 H* fingerprint |
| Promotion | 固定 holdout-102 | 接受/拒绝 H 或 M | 永不训练 |

Promotion 只看 pass count；提升则接受，平局在新 pipeline 中可接受。候选 H 被拒后，后续 model update 必须使用 incumbent H，而不是被拒绝的 H。

### 2.2 Harness evolving 默认设置

- Meta-agent：Claude Opus 4.8。
- Seed：`configs/baseline_tmax_harness.yaml`，或明确指定的已接受 harness。
- Tournament：R0 seed eval；R1 从 parent 提 5 个候选并全评；R2 从 winner 再提 5 个候选。
- 关键参数：`fanout=5`、`fanout_keep=5`、候选并行 5、inner regression tolerance 4%、`explore_every=0`、`skip_final_score`。
- 9B 通常每候选 `TMAX_CONCURRENT=4`；holdout 单 harness 并发 8。
- Harness 可进化四类杠杆：control processors、system instruction、processor configuration、tools/actions。历史上没有候选真正增加新 tool。

已接受 harness 的主要变化：loop/repeated-command breaker 占额外 processor 槽位的 53%，step-budget/lifecycle/self-verify 占 26%，HTTP verifier dependency reminder 占 21%；10 个已接受 H 中只有 3 个改写 prompt。Tournament 的两个 86 分 harness 基本来自 control processor，而不是 prompt rewrite。

### 2.3 SFT 默认设置

- 模型：主实验 Qwen3.5-9B；迁移验证 Qwen3.5-4B。
- LoRA：rank 32，alpha 64，dropout 0.05。
- 训练：2 epochs，lr `2e-5`，linear schedule，sequence length 16384，`completion_only_loss`。
- 每任务最多 1 条轨迹（新 recipe）；每轨迹最多 32 个 prompt/completion pair。
- 优先保留 8–20 tool turns 的干净成功轨迹，惩罚超长循环和慢轨迹。
- 继续训练时从上一轮 adapter 出发；当前默认不做 SFT retry。

### 2.4 RL 设置（rep46 主链）

- 起点：Qwen3.5-9B base + rep19-i1 已接受 harness H1。
- 每轮 2304 episodes，100 个训练任务，response length 16384，per-turn 4096，最多 40 steps。
- 每次 4 unique prompts × 8 samples；2 async steps；6 learner + 2 vLLM。
- active sampling、zero-std filtering、最多 8 sampled prompt groups、20% frontier exploration、domain-balanced frontier。
- RL 阶段约 6 小时，wall timeout 7 小时；rep46 设置 `MODEL_RATCHET=0`，因此每轮模型继续更新而非按 holdout 拒绝回滚。

## 3. 9B：逐轮 harness 与 model update 结果

每轮记作 `Before → H → Model → After incumbent`。H Δ 相对该轮开始；Model Δ 相对 H 决策后的 incumbent harness 分数。

| Chain / recipe | Iter | Before | Harness stage | H Δ / decision | Model stage | M Δ / decision | After |
|---|---:|---:|---:|---|---:|---|---:|
| rep1 sequential, frozen-50, all traj, PT=3 | 1 | 77 | 75 | −2 reject | 81 | +4 accept | 81 |
| | 2 | 81 | 82 | +1 accept | 82/81/82 | 0/−1/0 reject | 82 |
| | 3 | 82 | 84 | +2 accept | 84 | 0 tie | 84 |
| rep9 sequential smoke, MIN=80 | 1 | 76 | 77 | +1 accept | 77 | 0 tie | 77 |
| rep10 sequential, rotating-50, all traj | 1 | 74 | 75 | +1 accept | 82 | +7 accept | 82 |
| | 2 | 82 | 81 | −1 reject | 81/84 | −1/+2; retry accept | 84 |
| | 3 | 84 | 83 | −1 reject | 80/83/80 | −4/−1/−4 reject | 84 |
| rep12 sequential, alt rotating-50, retry×2 | 1 | 77 | 80 | +3 accept | 81 | +1 accept | 81 |
| | 2 | 81 | 80 | −1 reject | 81/87 | 0/+6; retry accept | 87 |
| | 3 | 87 | 80 | −7 reject | 86/83/84 | −1/−4/−3 reject | 87 |
| | 4 | 87 | 83 | −4 reject | 87/83/82 | 0/−4/−5 | 87 |
| rep13 sequential + SFT（随后计划 RL） | 1 | 74 | 82 | +8 accept | 88 | +6 accept | 88 |
| rep14 9B SFT-500 warm start, fanout v1 | 1 | 76 | 78 | +2 accept* | 80 | +2 accept | 80 |
| rep19 tournament, mixed sibling SFT | 1 | 77 | 83 | +6 accept | 81 | −2 reject | 83 |
| | 2 | 83 | 86 | +3 accept | 84 | −2 reject | 86 |
| | 3 | 86 | 80 | −6 reject | 82 | −4 reject | 86 |
| rep22 tournament, winner-only + SFT-gen | 1 | 78 | 76 | −2 reject | 82 | +4 accept | 82 |
| | 2 | 82 | 86 | +4 accept | 84 | −2 reject | 86 |
| | 3 | 86 | 82 | −4 reject | 85 | −1 reject | 86 |
| | 4 | 86 | 80 | −6 reject | 83 | −3 reject | 86 |
| | 5 | 86 | 82 | −4 reject | 尚未写入完成分数 | — | 86 |
| rep46 fixed rep19-H1 + iterative RL | 1 | 79 | skip/reuse H1 | 0 | 79 | 0 continue | 79 |
| | 2 | 79 | 79 | 0/tie | 84 | +5 continue | 84 |
| | 3 | 84 | skip/no new accepted H | 0 | 86 | +2 continue | 86 |
| rep50 RL2 checkpoint + H3 + RL3 | 3 | 88† | 90 | +2 accept | 正在运行 | — | 90 before RL3 |

\* rep14 i1 的“新” harness 实际无额外 processor、只是 materialized seed，+2 很可能是评测噪声。
† rep50 的 anchor 使用 rep46 RL2 checkpoint；同一 checkpoint 在 rep46 i2 评为 84，在 rep50 重评为 88。

### 3.1 总体增量统计（9B SFT 链）

基于 19 个有完整 H/SFT 的 outer iterations、只取第一次 SFT attempt：

| Stage | n | 平均 Δ | accept / tie / reject |
|---|---:|---:|---:|
| Harness | 19 | +0.1 | 10 / 0 / 9 |
| SFT a0 | 19 | +0.6 | 6 / 5 / 8 |

但均值掩盖了强烈的轮次效应：几乎所有 first-try SFT 正增益发生在第 1 轮；第 2 轮以后接受的 SFT 增益只有 rep10 i2 的 +2 retry 和 rep12 i2 的 +6 retry。Harness 在后期仍能偶尔增加（+1、+2、+3、+4）。

## 4. Data recipe 的区别与结果

| Recipe | 数据来源 | Harness 对齐 | 每任务轨迹 | 历史/重试 | 代表链 | 观测结果 |
|---|---|---|---:|---|---|---|
| 旧 all-evolve | evolve 所有轨迹 | 不严格区分最终 H* | 3 | MIN=80；部分 retry×2 | rep1/10/12/13 | 高方差；首轮可 +1 至 +7；retry 可再出现 +2/+6；后期多数 0/负 |
| mixed siblings | tournament 所有候选成功轨迹 | 不要求与 promoted H 一致 | 8 | 无额外 SFT-gen | rep19；4B rep21 | 9B 三轮 SFT 全负；4B 也负。搜索 sibling 的成功不等于适合训练最终 policy |
| winner-only + SFT-gen（single） | 仅 H* fingerprint 匹配轨迹；补到 100 unique | 严格匹配 incumbent/eval harness，注入其 prompt | 1 | 历史≤40%；当前无 retry | rep22；4B rep25 | 9B 首轮 +4、后续 −2/−1/−3；4B 首轮 −2。干净 on-policy 数据改善首轮，但仍有容量和后期饱和问题 |
| RL frontier | taxonomy 100 tasks；active/frontier sampling | 在当前 H 下在线 rollout | 每 prompt 8 samples | 每轮 2304 episodes，连续更新 | rep46/50 | 固定 H 上 RL 79→84→86；之后 H 再到 90（单次评测），显示较强互补，但方差待控制 |

Single 与 cross 的精确定义：

- `single`：`SFT_GEN_ROLLOUT=1`、补采 100 题、`CORPUS_EVAL_HARNESS=1`、`CORPUS_WINNER_ONLY=1`；只保留 incumbent harness 指纹匹配的数据，并注入其 system prompt。
- `cross`：`SFT_GEN_ROLLOUT=0`、不补采；`CORPUS_EVAL_HARNESS=0`、`CORPUS_WINNER_ONLY=0`；保留 tournament candidate harness 的成功轨迹及原始 prompt 分布。
- 这不是纯粹“同样本量只改混合方式”的 ablation：数据量与多样性也同时改变。论文若要因果归因，需要再做 task-count-matched control。

## 5. 4B 与其他模型验证

| Chain | Model | Recipe | 逐轮结果 | 当前结论 |
|---|---|---|---|---|
| rep21 | 4B | tournament + mixed siblings, PT=8 | 64→H64→SFT62；下一轮 H60→SFT62；incumbent 64 | 无 co-evolution 增益 |
| rep23 | 4B | winner-only，尚未 SFT | anchor 59；停在 i1 evolve | 未完成，不能下结论 |
| rep25 | 4B | winner-only + SFT-gen | 67→H68→SFT66；incumbent 68 | harness +1，SFT −2；未验证正迁移 |
| rep45 | 4B | evolve + RL | anchor 62；停在 i1 RL | 未完成 |
| rep20 | 2B | tournament + mixed / 成功轨迹极少 | 10→H8→SFT9；incumbent 10 | 容量明显不足 |
| rep27 | 27B | sequential no-RL | anchor 97，未完成 evolve | 天花板过高，不适合主要消融 |

所以目前不能写“4B 已复现 9B 的 co-evolution gain”。准确写法是：**4B 验证正在进行，现有 mixed 和 winner-only SFT 均未产生 model gain，提示 data recipe 与模型容量存在交互。**

## 6. 当前可以用于论文的核心图表

1. **主曲线：9B 逐轮分解图。** 每轮用两段增量表示 H update 和 M update；建议展示 rep19（mixed-SFT）、rep22（winner-only SFT-gen）、rep46/50（RL frontier），并画 incumbent ratchet 曲线。
2. **Recipe 对照表。** 用 rep19 vs rep22 表示 mixed sibling 与 single-incumbent 数据的差异；注明搜索方式相同（tournament 5+5），但数据量并未匹配。
3. **最好结果图。** baseline 77–79，harness-only peak 86，Harness+SFT peak 88，Harness+RL/H 再进化单次 peak 90；后两项标注“single-run / repeated evaluation pending”。
4. **Harness 机制图。** 展示被接受修改的组成：loop breaker 53%、lifecycle/budget 26%、dependency reminder 21%；说明改进主要来自 control 而不是新增工具。
5. **4B transfer 表。** 将 4B 当前负结果作为重要边界条件，而不是隐藏：这恰好支持 recipe/capacity interaction 的研究问题。

## 7. 论文结论的建议措辞

> Across Qwen3.5-9B runs, harness optimization and model optimization provide complementary gains. Harness evolution alone improves the frozen model from roughly 77–78 to a peak of 86/102. Model updates can add further gains on top of an evolved harness: SFT reaches 88/102 in the best observed chain, while an RL-based chain improves from 79 to 86 under a fixed evolved harness and reaches 90 after an additional harness update. However, the benefit is strongly mediated by the training-data recipe. Mixing trajectories from competing harness candidates consistently hurts SFT, whereas filtering trajectories to the promoted harness and collecting clean on-policy successes yields a positive first-round update. These gains diminish in later rounds and have not yet transferred to 4B, motivating matched-data and repeated-evaluation controls.

## 8. 发表前必须补齐的控制实验

1. 完成 rep48 或等价的同预算 9B harness-only R1–R4 holdout，作为严格 control。
2. 对 base、rep19-H1、RL2、RL3、H3+RL3 至少做 3 次 holdout-102 重评；当前同一 RL2 checkpoint 的 84 vs 88 已证明单次分数不足。
3. 做 task-count-matched `single` vs `cross`，否则 recipe 差异混入了数据量/多样性差异。
4. 做 frozen-H 的 model-only 对照，以及 frozen-M 的 H-only 对照，形成 2×2：none / H-only / M-only / H+M。
5. 在 4B 上复刻最有效的 9B RL frontier recipe；若仍失败，报告容量×recipe interaction，而不是笼统声称规模迁移。
6. 报告至少 3 seeds 的 mean±std，并对 pass/fail 用 paired bootstrap 或 McNemar test（同一 102 题）比较。

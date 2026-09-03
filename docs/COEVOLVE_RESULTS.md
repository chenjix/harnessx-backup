# Tmax coevolution results

Snapshot: 2026-09-01.

Eval is frozen **holdout-102** (`recipe/tb2_evolver/tasks_tmax_only200.json`) on 8×H200. Sources: `outputs/tmax_coevolve/rep*/scores.tsv`, `incumbent.tsv`, SFT corpus `summary.json`. Older `STATUS=RUNNING` files whose jobs are off the queue are treated as stalled.

Related: [COEVOLVE_PIPELINE.md](./COEVOLVE_PIPELINE.md) (current loop implementation).

Interactive copy: canvas `coevolve-all-results.canvas.tsx`.

---

## Current harness and SFT defaults

Launchers:

- 9B: `scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch` (`REPLICATE=22`)
- 4B: `scripts/slurm/tmax/h200_tmax_coevolve_tournament_4b_sft.sbatch` (`REPLICATE=23`)

Job **3194** resumes rep22 from iter 4 through 5.

### Harness evolve (stage A / B)

| Item | Setting |
|---|---|
| Method | `fanout-mode tournament` |
| Rounds | 2 (R0 seed eval; R1 propose 5, full-eval all; R2 propose 5 from winner) |
| Flags | `--fanout 5 --fanout-keep 5 --fanout-concurrent 5 --fanout-eval-concurrent 5 --regression-tolerance 0.04 --explore-every 0 --skip-final-score` |
| Inner gate | 4% on the evolve set |
| Holdout ratchet | pass count only; ties accepted (`ACCEPT_TIES=1`); no `sys_err` second gate |
| On reject | later stages use incumbent H, not the rejected YAML |
| Evolve plane | 50 tasks; rotate from iter 2 by mastery |
| Holdout | never trained on |
| Seed | `configs/baseline_tmax_harness.yaml` |
| Meta-agent | Claude Opus 4.8 |
| 9B evolve concurrency | `TMAX_CONCURRENT=4` (5×4 = 20 workers) |

### SFT corpus and training (stage B2 / C / D / E)

| Item | Setting |
|---|---|
| Method | `winner_only` + separate SFT-gen plane |
| Filter | sidecar YAML+prompt must match eval harness H*; drop losing `fe-c*` siblings |
| SFT-gen (B2) | reuse this iter’s evolve evals, then sample new tasks up to 100 unique non-holdout |
| `PER_TASK` | 1 |
| `MAX_PAIRS_PER_TRAJ` | 32 |
| History | `CORPUS_MAX_PREV_FRAC=0.4` |
| Quality | prefer clean 8–20 tool-turn successes; penalize long loops |
| Prompt | inject H* `system_prompt.txt` |
| Trainer | LoRA r=32, α=64, dropout=0.05, 2 epochs, lr 2e-5 linear, seq 16384, `completion_only_loss` |
| Continue | from previous adapter if one exists |
| Retries | `MAX_SFT_RETRIES=0` |
| RL | `ENABLE_RL=0` |

---

## All experiments at a glance

Peak = highest holdout score seen on the chain, including later-rejected stages. Incumbent = `(H, M)` the ratchet actually kept.

| Chain | Model | Harness method | SFT method | Status | Start | Peak | Incumbent |
|---|---|---|---|---|---:|---:|---:|
| rep1 | 9B base | sequential 1+1, 5 rounds, frozen-50 | all evolve trajs, `PER_TASK=3` | DONE 3 iter | 77 | 84 | 84 |
| rep9 | 9B base | sequential, 1-iter smoke | same as rep1, `MIN_TRAJS=80` | DONE 1 iter | 76 | 77 | 77 |
| rep10 | 9B base | sequential + 50-task rotate | `PER_TASK=3`, `MIN=80`, no cap | DONE 3 iter | 74 | 84 | 84 |
| rep12 | 9B base | sequential + alt-50 rotate | same as rep10, SFT retry×2 | FAILED i5 A0 | 77 | 87 | 87 |
| rep13 | 9B base | sequential + rotate | same + short D2 RL | stalled i2 | 74 | 88 | 88 |
| rep14 | 9B SFT-500 | fanout v1 8→2 | all evolve trajs, `PER_TASK=3` | FAILED i2 A | 76 | 80 | 80 |
| rep15 | 9B base | fanout v2 8 keep 3 | never reached SFT | stalled i1 A | 79 | 79 | 79 |
| rep2 | 9B base | sequential + planned RL | never reached SFT | stalled i1 B | 76 | 76 | 76 |
| rep19 | 9B base | tournament 5+5 | mixed siblings, `PER_TASK=8` | DONE 3 iter | 77 | 86 | 86 |
| rep22 | 9B base | tournament 5+5 | winner_only + SFT-gen | through i3; i4 queued | 78 | 86 | 86 |
| rep20 | 2B base | tournament 5+5 | mixed / almost no successes | stalled i2 A | 10 | 10 | 10 |
| rep21 | 4B base | tournament 5+5 | mixed siblings, `PER_TASK=8` | stalled i3 B | 64 | 64 | 64 |
| rep23 | 4B base | tournament 5+5 | winner_only (no SFT yet) | i1 evolve | 59 | 59 | 59 |
| rep27 | 27B | sequential norl | never reached SFT | stalled i1 A | 97 | 97 | 97 |
| rep16 / 17 / 18 | 2B / 9B v2 | fanout v2 | stuck at anchor | stalled | — / 75 / — | — | — |
| loop3 | TB2 28 tasks | early 1+1 | evolve SFT | 1 iter | 22/28 | 22 | 19 |
| tb21-rep1 | TB2 40 holdout | sequential norl | FAILED at SFT | FAILED i1 D | 7/40 | 8 | 8 |

9B incumbent after each outer iter (incomplete later iters carry the last incumbent forward):

| Chain | Anchor | Iter 1 | Iter 2 | Iter 3 | Iter 4 |
|---|---:|---:|---:|---:|---:|
| rep1 sequential frozen-50 | 77 | 81 | 82 | 84 | 84 |
| rep10 sequential rotate | 74 | 82 | 84 | 84 | 84 |
| rep12 alt-50 sequential | 77 | 81 | 87 | 87 | 87 |
| rep13 sequential+RL | 74 | 88 | 88 | 88 | 88 |
| rep19 tournament mixed-SFT | 77 | 83 | 86 | 86 | 86 |
| rep22 tournament winner-only | 78 | 82 | 86 | 86 | 86 |

`scores.tsv` `_rl` suffixes on sequential chains are mostly labels. Only **rep13** actually set `ENABLE_RL=1`.

---

## Per-iter harness vs SFT scores

Each outer iter is `before → H (stage B, frozen M) → SFT (stage E, frozen H*)`.  
`H Δ` is vs the incumbent at the start of the iter. `SFT Δ` is vs the score after the H decision (accepted H, or the previous incumbent if H was rejected). Retries listed as `a0 / a1 / a2`. Bold = ratchet kept that change.

### 9B chains with at least one H and one SFT eval

| Chain | Iter | Before | H | H Δ | H decision | SFT | SFT Δ | SFT decision | After |
|---|---:|---:|---:|---:|---|---|---:|---|---:|
| rep1 | 1 | 77 | 75 | −2 | reject | **81** | +4 | accept | 81 |
| rep1 | 2 | 81 | **82** | +1 | accept | 82 / 81 / 82 | 0 / −1 / 0 | reject | 82 |
| rep1 | 3 | 82 | **84** | +2 | accept | 84 | 0 | tie | 84 |
| rep9 | 1 | 76 | **77** | +1 | accept | 77 | 0 | tie | 77 |
| rep10 | 1 | 74 | **75** | +1 | accept | **82** | +7 | accept | 82 |
| rep10 | 2 | 82 | 81 | −1 | reject | 81 / **84** | −1 / +2 | reject then accept | 84 |
| rep10 | 3 | 84 | 83 | −1 | reject | 80 / 83 / 80 | −4 / −1 / −4 | reject | 84 |
| rep12 | 1 | 77 | **80** | +3 | accept | **81** | +1 | accept | 81 |
| rep12 | 2 | 81 | 80 | −1 | reject | 81 / **87** | 0 / +6 | reject then accept | 87 |
| rep12 | 3 | 87 | 80 | −7 | reject | 86 / 83 / 84 | −1 / −4 / −3 | reject | 87 |
| rep12 | 4 | 87 | 83 | −4 | reject | 87 / 83 / 82 | 0 / −4 / −5 | tie / reject | 87 |
| rep13 | 1 | 74 | **82** | +8 | accept | **88** | +6 | accept | 88 |
| rep14 | 1 | 76 | **78** | +2 | accept | **80** | +2 | accept | 80 |
| rep19 | 1 | 77 | **83** | +6 | accept | 81 | −2 | reject | 83 |
| rep19 | 2 | 83 | **86** | +3 | accept | 84 | −2 | reject | 86 |
| rep19 | 3 | 86 | 80 | −6 | reject | 82 | −4 | reject | 86 |
| rep22 | 1 | 78 | 76 | −2 | reject | **82** | +4 | accept | 82 |
| rep22 | 2 | 82 | **86** | +4 | accept | 84 | −2 | reject | 86 |
| rep22 | 3 | 86 | 82 | −4 | reject | 85 | −1 | reject | 86 |

### Split of the holdout movement (9B, first SFT attempt only)

Count each iter once. `H` column is stage B. `SFT` column is stage E a0 (ignore extra-evolve retries here).

| | n iters | mean Δ | accept / tie / reject |
|---|---:|---:|---|
| Harness (stage B) | 19 | +0.1 | 10 / 0 / 9 |
| SFT first attempt (stage E a0) | 19 | +0.6 | 6 / 5 / 8 |

Retries matter: two of the biggest SFT jumps (rep10 i2 +2, rep12 i2 +6) only appeared after extra evolve + rebuild. Current tournament defaults set `MAX_SFT_RETRIES=0`, so those retries would not fire.

Iter-1 vs later (9B, accepted Δ only):

| | Accepted H Δ | Accepted SFT Δ (incl retries) |
|---|---|---|
| Iter 1 | +1, +1, +3, +8, +2, +6 | +4, +7, +1, +6, +2, +4 |
| Iter ≥2 | +1, +2, +3, +4 | +2 (rep10 i2 retry), +6 (rep12 i2 retry) |

Almost all *first-try* SFT gains are iter 1. Later accepted H still happens (rep1 i2/i3, rep19 i2, rep22 i2), but later SFT on the new recipe has not beaten the H incumbent.

### 4B / 2B (for completeness)

| Chain | Iter | Before | H | H Δ | H decision | SFT | SFT Δ | SFT decision | After |
|---|---:|---:|---:|---:|---|---:|---:|---|---:|
| rep21 4B | 1 | 64 | 64 | 0 | reject | 62 | −2 | reject | 64 |
| rep21 4B | 2 | 64 | 60 | −4 | reject | 62 | −2 | reject | 64 |
| rep20 2B | 1 | 10 | 8 | −2 | reject | 9 | −1 | reject | 10 |

---

## What harness edits actually change

Four allowed levers: **control** (new processors), **instruction** (sibling `system_prompt.txt`), **configuration** (kwargs on stock processors), **action** (new tools). Runtime slots (`sandbox_provider`, model) are not evolvable. Stock baseline already has Bash-only tools plus 10 processors (env context, tool-call correction, time reminder, length-truncation recovery, compaction, parse-retry, post-compaction refresh, bg-install guard, custom edit, custom self-verify).

### All meta proposals (changeset.json)

216 candidate changesets on 9B/4B Tmax chains (rep1/9/10/12/13/14/19/21/22). A changeset can touch more than one lever.

| Lever | Count | Share of 216 | Notes |
|---|---:|---:|---|
| Add a processor (control) | 131 | 61% | Dominant action |
| Prompt rewrite vs 5-line stock (instruction) | 44 | 20% | Counted if candidate `system_prompt.txt` ≠ baseline |
| Remove a processor | 28 | 13% | Usually swapping self-verify |
| Empty / no-op copy | 54 | 25% | Inner-gate revert or copy-forward |
| New tool (action) | 0 | 0% | Never shipped |

Processor *families* among the 131 add-processor events (class name heuristics):

| Family | Count | Share of adds | Typical class |
|---|---:|---:|---|
| Loop / identical-command breaker | 49 | 37% | `LoopDetectionProcessor`, `CyclicLoopBreaker`, `DeadLoopBreaker`, `RepeatedCommandBreaker` |
| Other / one-off | 39 | 30% | OCR advisors, fabricated-test guards, artifact checks |
| Verifier Python dep (`requests`, etc.) | 21 | 16% | `VerifierDepGuard`, `HttpVerifierDepProcessor` |
| Step-budget / lifecycle / self-verify | 17 | 13% | `StepBudgetVerifyProcessor`, `LifecycleSnapshotSelfVerify` |
| Truncation / narration | 5 | 4% | `TruncationLoopGuard`, length-recovery retunes |

### What holdout actually kept (10 accepted incumbents)

These are the harnesses written into `best_harness.tsv` / `incumbent.tsv`, not the inner-gate winners.

| Incumbent | Holdout when accepted | Prompt vs stock | Extra processors vs stock |
|---|---:|---|---|
| rep1 i2 R7 | 82 | rewritten (25 lines) | loop break + loop-detect + lifecycle self-verify (drops CustomSelfVerify) |
| rep1 i3 R4 | 84 | stock 5-line | same loop stack + `VerifierDepGuard` |
| rep9 i1 R2 | 77 | stock | truncation-loop guard + `LoopDetectionProcessor` |
| rep10 i1 R3 | 75 | stock | `StepBudgetDeadlineProcessor` only |
| rep12 i1 R4 | 80 | rewritten (22 lines) | `VerifierDepGuard` only |
| rep13 i1 R4 | 82 | rewritten (42 lines) | `DeadLoopBreaker` |
| rep14 i1 R0 | 78 | stock | **none** (materialized seed; +2 is likely noise) |
| rep19 i1 R2 | 83 | stock | step-budget verify + repeated-command breaker |
| rep19 i2 R1 | 86 | stock | previous two + HTTP `requests` dep reminder |
| rep22 i2 R2 | 86 | stock | `CyclicLoopBreaker` + `HttpVerifierDepProcessor` |

Family mix **on accepted extras** (19 extra processor slots across those 10 incumbents; rep14 contributes 0):

| Family | Slots | Share of accepted extras |
|---|---:|---:|
| Loop breaker | 10 | 53% |
| Step-budget / lifecycle / self-verify | 5 | 26% |
| Verifier dep (`requests`) | 4 | 21% |
| Prompt rewrite | 3 / 10 incumbents | 30% of accepted H |
| New tools | 0 | 0% |

Reading: the meta-agent mostly writes **loop-breakers** and **“pip install requests so the verifier collects”**. Prompt rewrites show up on sequential chains (rep1/12/13) and are rare on tournament winners — rep19 and rep22 both kept the 5-line stock prompt and still reached 86. Tournament H gains so far are almost entirely two control processors, not instruction.

Inner evolve-set winners and holdout incumbents diverge after iter 1 once the 50-set rotates onto harder tasks: later iters add more loop compactors that help the 50 and lose the 102.

---

## Ablations to run

Priority is (1) cheap holdout-only strips of existing 86 harnesses, then (2) 1-iter 9B chains that isolate H vs M vs recipe, then (3) longer / smaller-model work. Do not start from rep21.

### A. Component strips (eval-only, no evolve)

Freeze Qwen3.5-9B **base** (and separately the rep22 i1 LoRA). Eval holdout-102 on surgically edited copies of the two 86 harnesses.

| ID | Edit | Why |
|---|---|---|
| A1 | rep19 i2 R1 minus `RepeatedCommandBreaker` | isolate loop-breaker |
| A2 | rep19 i2 R1 minus `VerifierHttpDepReminder` | isolate requests-dep |
| A3 | rep19 i2 R1 minus `StepBudgetVerifyProcessor` | isolate budget nudge |
| A4 | rep19 i2 R1 minus all three extras (= stock YAML) | full H credit vs 77/78 baseline |
| A5 | rep22 i2 R2 minus `CyclicLoopBreaker` | same as A1 on the other 86 H |
| A6 | rep22 i2 R2 minus `HttpVerifierDepProcessor` | same as A2 |
| A7 | rep22 i2 R2 minus both extras | stock YAML under i1 LoRA — should sit near 82 |
| A8 | stock YAML + *only* the HTTP-dep processor | is +requests enough? |
| A9 | stock YAML + *only* the loop breaker | is loop-break enough? |
| A10 | stock YAML + the 22-line rep12 prompt, no extra processors | instruction-only vs control-only |

Each cell is one holdout-102 (~1.5–2 h on 8×H200). Full A-grid ≈ 20 evals if both models are crossed; start with **base × {A4, A8, A9, A10}** and **i1 LoRA × {A7, A5, A6}**.

### B. Training / recipe ablations (1 outer iter unless noted)

Same evolve-50 seed, same holdout-102, 9B base, tournament 5+5 unless noted. `N_ITERS=1` is enough for the first-try SFT question.

| ID | Condition | Answers |
|---|---|---|
| B1 | **H-only**: tournament evolve, skip SFT (or SFT with empty corpus) | Can H alone reach ~86 again? (rep19 already did; replicate) |
| B2 | **M-only**: freeze stock YAML, SFT-gen 100 + winner_only, no evolve | Can SFT alone match rep22 i1’s 78→82? |
| B3 | **H then M once**: evolve i1, accept/reject H, one SFT, stop | Clean 1-iter coevolve vs 3-iter diminishing returns |
| B4 | Tournament + **mixed siblings** SFT (rep19 recipe) vs **winner_only** (rep22), same job | Recipe, not search method |
| B5 | winner_only **without** SFT-gen (evolve-50 only) vs with SFT-gen 100 | Is B2 top-up necessary? |
| B6 | Sequential 5-round 1+1 + current winner_only SFT | Search method vs tournament, recipe held fixed |
| B7 | `MAX_SFT_RETRIES=2` on tournament winner_only | Did we drop the +6 retry (rep12 i2) by setting retries=0? |
| B8 | SFT on **accepted H trajectories only**, even if B rejected — vs current “train on incumbent H” | Tests the rep22 i1 accident (rejected H forced stock-YAML positives) |

### C. Do later / only if A+B pan out

| ID | Condition | Why later |
|---|---|---|
| C1 | Finish rep22 iters 4–5 (job 3194 already queued) | Confirms 3-iter saturation on the new recipe |
| C2 | 4B winner_only (rep23) to 3 iters | Size transfer; old 4B mixed-SFT was stuck at 64 |
| C3 | Isolated RL (no coevolve) then one coevolve iter | rep13’s 88 mixed a short D2; do not wire RL until this is real |
| C4 | Prompt-evolve-only tournament (`processors` frozen) | Instruction lever at 86, given tournament winners did not rewrite the prompt |
| C5 | Processor-evolve-only (`system_prompt.txt` frozen) | Control lever at 86; likely the default given the 86 harnesses |

### Suggested order

1. **A4 / A7 / A8 / A9** (stock vs loop vs requests vs full 86) — one node, ~8 holdouts. This tells us whether the 86 H is two processors or an interaction.
2. **B2 M-only** and **B1 H-only** 1-iter — the paper-facing H vs M vs H↔M triangle. We already have accidental versions (rep22 i1 ≈ M-only, rep19 ≈ H-only); these make them controlled.
3. **B4 / B5** if B2’s SFT gain replicates — otherwise the recipe debate is moot.
4. **C1** in parallel (already queued). Skip C3 until A/B are done.

---

## Sequential 9B (legacy 1+1)

Harness: one candidate per round, full-eval on evolve-50, 5 rounds by default, plus 2 extra rounds on SFT retry. No fanout. Meta: Opus 4.8.

SFT: scan all `r0…rN` trajs this iter, `PER_TASK=3`, tools 2–60, LoRA 2 epochs.

### rep1 — frozen-50, DONE, 77 → 84

Jobs 1790 → 1861 → 2081. Evolve set frozen at `tasks_tmax_evolve50_list.json`. `MIN_TRAJS=20`, `MAX_TRAJS=100`, `ENABLE_RL=0`. After i1 harness reject, SFT ran on the baseline YAML.

| Stage | Model | Harness | Score | Δ | Decision |
|---|---|---|---:|---:|---|
| 0 anchor | base | baseline YAML | 77 | — | start |
| i1 B harness | base | new H | 75 | −2 | reject |
| i1 E SFT a0 | sft_i1 | incumbent = baseline | 81 | +4 | accept |
| i2 B harness | sft_i1 | i2 R7 | 82 | +1 | accept |
| i2 E SFT a0 / a1 / a2 | sft_i2 ×3 | i2 R7 | 82 / 81 / 82 | 0 / −1 / 0 | reject |
| i3 B harness | sft_i2 incumbent | i3 R4 | 84 | +2 | accept |
| i3 E SFT | sft_i3 | i3 R4 | 84 | 0 | tie accept |

i1 corpus: 90 trajs / 34 tasks / 361 pairs. Main gains: i1 SFT (+4) and i3 harness (+2).

### rep9 — 1-iter smoke, DONE, 76 → 77

| Stage | Score | Δ | Decision |
|---|---:|---:|---|
| 0 anchor | 76 | — | start |
| i1 B harness (R2) | 77 | +1 | accept |
| i1 E SFT | 77 | 0 | tie accept |

### rep10 — rotate-50, DONE, 74 → 84

Same method as rep1, plus `ROTATE_EVOLVE_TASKS=1`, `MIN_TRAJS=80`, `MAX_TRAJS=0`, SFT retry×2. Job 2110.

| Stage | Harness actually used | Score | Δ | Decision |
|---|---|---:|---:|---|
| 0 anchor | baseline | 74 | — | start |
| i1 B | new H R3 accepted | 75 | +1 | accept |
| i1 E SFT | i1 R3 | 82 | +7 | accept |
| i2 B | new H rejected, keep i1 R3 | 81 | −1 | reject |
| i2 E a0 | i1 R3 | 81 | −1 | reject |
| i2 E a1 extra-evolve | i1 R3 | 84 | +2 | accept |
| i3 B | new H rejected | 83 | −1 | reject |
| i3 E a0 / a1 / a2 | i1 R3 | 80 / 83 / 80 | all below 84 | reject |

### rep12 — alt-50 (disjoint from prior evolve sets), FAILED i5, 77 → 87

Same protocol as rep10, `N_ITERS=5`, seed `tasks_tmax_evolve50_alt12_list.json`. Job 2235. Failed sampling tasks at i5 A0.

| Stage | Score | Δ | Decision | Notes |
|---|---:|---:|---|---|
| 0 anchor | 77 | — | start | |
| i1 B | 80 | +3 | accept | R4 |
| i1 E | 81 | +1 | accept | |
| i2 B | 80 | −1 | reject | keep i1 R4 |
| i2 E a0 | 81 | 0 | reject | |
| i2 E a1 | 87 | +6 | accept | retry; 169 trajs / 64 tasks / 887 pairs |
| i3 B | 80 | −7 | reject | |
| i3 E a0 / a1 / a2 | 86 / 83 / 84 | all below 87 | reject | |
| i4 B | 83 | −4 | reject | |
| i4 E a0 / a1 / a2 | 87 / 83 / 82 | tie / drop | tie accept | a0 ties at 87 |

### rep13 — sequential + online RL, stalled, 74 → 88 (best single point)

`h200_tmax_coevolve_rl.sbatch`. Sequential 5-round harness. SFT `MIN_TRAJS=80`, `PER_TASK=3`. D2 planned taxonomy DPPO/GRPO, 512 episodes / 100 tasks. Actual D2 ran ~13 min, then E scored 88. Stalled at i2 rotate / D2 again.

| Stage | Score | Δ | Decision |
|---|---:|---:|---|
| 0 anchor | 74 | — | start |
| i1 B harness R4 | 82 | +8 | accept |
| i1 E SFT (+ short RL) | 88 | +6 | accept |
| i2 | — | — | stalled |

Highest holdout on any chain, but only one outer loop finished. A 13-minute D2 is not enough to credit RL.

### rep2 — sequential + planned RL, anchor only

Stalled at i1 B_holdout. Anchor **76**. No later scores.

---

## Fan-out (screened population)

Outer loop still A→B→C→D→E. Only stage A changes. SFT still ingested all evolve trajs (no `winner_only` / SFT-gen yet).

| Variant | Settings |
|---|---|
| v1 (rep14) | `--fanout 8 --fanout-keep 2 --fanout-concurrent 4 --explore-every 3`. Drop a candidate that breaks more than one parent-solved probe. |
| v2 (rep15 / 16) | `--fanout 8 --fanout-keep 3 --fanout-mode v2 --probe-solved 4 --probe-unsolved 2`. Rank by net gain; allow STACK/MERGE. |

### rep14 — 9B warm-start from SFT-500, FAILED i2, 76 → 80

`INIT_LORA = qwen35_9b_tmax_only500_on102_v2`, not base. Accepted harness path is R0 (materialized seed). i2 evolve died on docker/space (exit 2).

| Stage | Score | Δ | Decision |
|---|---:|---:|---|
| 0 anchor (SFT-500 + baseline) | 76 | — | start |
| i1 B (R0) | 78 | +2 | accept |
| i1 E SFT (88 trajs / 36 tasks) | 80 | +2 | accept |
| i2 A evolve | — | — | failed |

### rep15 — 9B fanout v2

Anchor **79**. Stalled at i1 A_evolve. No harness / SFT scores.

### rep16 / 17 / 18

rep16 was 2B fanout v2 (`TMAX_CONCURRENT=32`). rep17 wrote anchor 75 then stopped. rep18 `scores.tsv` is empty. No usable coevolve result.

---

## Tournament 5+5 (current harness search)

R0 evals the seed; R1 proposes 5 candidates and full-evals all; R2 proposes 5 more from the winner. Inner gate 0.04.

### rep19 — 9B tournament + mixed-fanout SFT, DONE, 77 → 86

SFT ingested all `fe-c*` siblings, `PER_TASK=8`, no `winner_only`. Harness climbed 77→83→86; all three SFTs dropped and were rejected; incumbent model stayed **base**.

| Stage | Score | Δ | Decision | SFT corpus |
|---|---:|---:|---|---|
| 0 anchor | 77 | — | start | |
| i1 B R2 | 83 | +6 | accept | |
| i1 E | 81 | −2 | reject | 149 mixed-sibling trajs, 618 pairs |
| i2 B R1 | 86 | +3 | accept | model still base |
| i2 E | 84 | −2 | reject | 232 mixed trajs, includes i1 siblings |
| i3 B | 80 | −6 | reject | keep i2 R1 @ 86 |
| i3 E | 82 | −4 | reject | |

### rep22 — 9B tournament + winner_only + SFT-gen, in progress 78 → 86

First chain on the current recipe. Job 3151 finished 3 iters; job 3194 resumes from iter 4. Incumbent: **i1 LoRA + i2 R2 harness @ 86**.

| Stage | Score | Δ | Decision | Method / data |
|---|---:|---:|---|---|
| 0 anchor | 78 | — | start | base + baseline YAML |
| i1 B | 76 | −2 | reject | new H rejected → H* = stock YAML |
| i1 E | 82 | +4 | accept | winner_only kept r0-traj only: 30 trajs / 30 tasks / 526 pairs |
| i2 B | 86 | +4 | accept | R2 winner; model still i1 LoRA |
| i2 E | 84 | −2 | reject | B2 SFT-gen on; 50 trajs / 948 pairs, fingerprint = i2 H |
| i3 B | 82 | −4 | reject | H* still i2 R2 |
| i3 E | 85 | −1 | reject | B2 skipped (H*, M unchanged); corpus reused i2’s 50 trajs |
| i4 A | — | — | queued | job 3194 |

i1 is the only accepted SFT gain on the new recipe: harness reject forced the corpus onto stock-YAML R0 successes (78→82). Later SFTs under the accepted i2 harness scored 84 / 85, both below 86.

### Same tournament, different SFT recipe

| | rep19 (old SFT) | rep22 (current SFT) |
|---|---|---|
| Harness | tournament 5+5, 2 rounds | same |
| SFT trajs | all `fe-c*` siblings | winner_only, fingerprint = H* |
| `PER_TASK` | 8 | 1 (i1 actually wrote 2) |
| SFT-gen top-up | none | from iter 2, target 100 unique |
| SFT vs holdout | 81 / 84 / 82 all rejected | 82 accepted, then 84 / 85 rejected |
| Final incumbent M | always base | i1 LoRA |
| Final incumbent H | i2 R1 @ 86 | i2 R2 @ 86 |

---

## Smaller models / 27B / non-Tmax

### rep21 — 4B tournament mixed-SFT, stuck at 64

Same old SFT as rep19: siblings in the corpus, `PER_TASK=8`, `winner_only=false`. Do not resume this replicate on the new recipe.

| Stage | Score | Δ | Decision |
|---|---:|---:|---|
| 0 anchor | 64 | — | start |
| i1 B | 64 | 0 | reject |
| i1 E | 62 | −2 | reject |
| i2 B | 60 | −4 | reject |
| i2 E | 62 | −2 | reject |
| i3 B | — | — | stalled |

### rep23 — 4B new recipe

`h200_tmax_coevolve_tournament_4b_sft.sbatch`, winner_only + SFT-gen. Anchor **59**. Currently in i1 A_evolve. No H or SFT scores yet.

### rep20 — 2B tournament, eval essentially broken

| Stage | Score | Decision |
|---|---:|---|
| 0 anchor | 10 | start |
| i1 B | 8 | reject |
| i1 E | 9 | reject |

i1 corpus kept 19 successes / 6 tasks. Scores are not comparable.

### rep27 — Qwen3.6-27B sequential norl

Protocol matched 9B norl; only `MODEL_SIZE` changed. Anchor **97/102**. i1 evolve never finished. No H/SFT stage scores.

### loop3 / tb21 (not Tmax-102)

| Chain | Split | Stages | Outcome |
|---|---|---|---|
| loop3-rep1 | TB2 28 tasks | 22 → H 22 → SFT 19 | 1 iter, SFT hurt |
| tb21-rep1 | TB2 89 split into evolve-49 + holdout-40 | scan 7/40 → i1 H 8/40 | SFT failed 2026-08-24 |

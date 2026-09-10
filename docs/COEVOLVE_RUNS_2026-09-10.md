# Co-evolution code and run snapshot (2026-09-10)

This snapshot records the currently reproducible code changes and all recent
runs that produced a usable evaluation result. Scores are copied from the
archived `scores.tsv`/`incumbent.tsv` files or from the terminal summary of the
corresponding TB2 evaluation log. Raw stdout/stderr, caches, model weights,
checkpoints, trajectories, and benchmark work directories are intentionally
not stored in Git.

## Current code changes

- `scripts/tmax/run_loop_tmax_coevolve.sh`: safer resume semantics, explicit
  stage/iteration selection, improved model/harness state handling, and support
  for the current harness-only, SFT, and RL experiment variants.
- `scripts/tmax/settings/_submit.sh` and the new settings scripts: reusable
  submission/resume presets for 4B and 9B experiments.
- `scripts/slurm/tmax/h200_tmax_experiment.sbatch`: propagates the new run and
  resume controls into Slurm jobs.
- `recipe/tb2_evolver/run.py`: gate association/resume corrections, with a
  regression test in `test_gate_association_and_resume.py`.
- TB2 transfer evaluation configs and launchers for the 9B base model, rep22
  SFT checkpoint, and rep46 RL2 checkpoint.
- Evaluation launcher for rep48 harness-only R1--R4 holdout-102 controls.
- Pipeline/runbook documentation synchronized with the implemented controls.

## Recent Tmax runs

The full lightweight metadata for reps 45--60 is archived under
`outputs/tmax_coevolve/`. A stale `STATUS=RUNNING` means that a job stopped
without finalizing its status; these are not counted as completed chains.

| Run | Model/update | State | Usable holdout-102 results | Interpretation |
|---|---|---|---|---|
| rep45 | 4B RL | stalled | anchor 62 | Anchor only |
| rep46 | 9B RL | **done** | 79 -> 79 -> 84 -> 86 | Successful fixed-harness RL chain |
| rep47 | 9B | stalled | anchor 81 | Anchor only |
| rep48 | 9B harness-only | **done** | anchor 77; incumbent 77 | Evolve completed, no promoted gain in the main score table |
| rep49 | 9B RL/harness | stalled | anchor 79; H(i3) 80 | Partial result only |
| rep50 | 9B RL2 + harness + RL3 | **done** | anchor 88; H(i3) 90; RL3 85; final incumbent 88 | Best single harness-stage score is 90; RL3 was not promoted |
| rep51 | 4B SFT single | stalled | none | No usable evaluation result |
| rep52 | 4B SFT single | **done** | anchor 67; SFT 62; H/SFT 63/59; H/SFT 64/62; incumbent 67 | No model or harness promotion |
| rep53--55 | 4B variants | stalled | none | No usable evaluation result |
| rep56 | 4B harness | stalled | anchor 64 | Anchor only |
| rep57 | 4B SFT cross | stalled | none | No usable evaluation result |
| rep58 | 4B SFT cross | **done** | anchor 64; H/SFT 66/65; SFT 61; H/SFT 56/55; incumbent 64 | Candidate H reached 66, but final ratchet retained 64 |
| rep59 | 4B RL | stalled | none | No usable evaluation result |
| rep60 | 4B RL | active/stale snapshot | none as of cutoff | Excluded from conclusions |

Important qualification: holdout-102 values are individual evaluations. The
same rep46 RL2 checkpoint scored 84 in rep46 and 88 when re-evaluated as rep50's
anchor, so repeated seeds are required before treating small differences as
significant.

## TB2-89 transfer evaluations

All three runs completed the same 89-task native TB2 evaluation on 2026-09-08.

| Evaluation | Passed | Rate | Delta vs base |
|---|---:|---:|---:|
| Qwen3.5-9B base | 17/89 | 19.1% | -- |
| rep22 best SFT checkpoint | 16/89 | 18.0% | -1 task |
| rep46 RL2 + rep19-H1 | 21/89 | 23.6% | +4 tasks |

These are transfer results, not additional Tmax holdout scores. The launch
configs/scripts in this commit capture the exact checkpoint/harness pairing.

## What is and is not archived

Committed:

- source, configs, launch scripts, tests, and documentation;
- lightweight Tmax run state, score tables, decisions, harness references, and
  checkpoint path/provenance records for reps 45--60;
- this consolidated result index.

Not committed:

- `.out`/`.err` and service logs (hundreds of MB per run in some cases);
- model weights, optimizer states, caches, generated trajectories, and Docker
  or benchmark workspaces;
- credentials or environment files.

The older, broader experiment history through rep27 remains documented in
`COEVOLVE_PAPER_SUMMARY_2026-09-07.md` and the previously archived run records.

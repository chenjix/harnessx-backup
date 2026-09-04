#!/usr/bin/env bash
# Qwen3.5-4B: multi-iteration harness evolve + winner/single-harness SFT rollout.
TMAX_SETTING=4b_sft_single exec bash "$(dirname "$0")/_submit.sh"

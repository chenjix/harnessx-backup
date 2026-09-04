#!/usr/bin/env bash
# Qwen3.5-9B: multi-iteration harness evolve + winner/single-harness SFT rollout.
TMAX_SETTING=9b_sft_single exec bash "$(dirname "$0")/_submit.sh"

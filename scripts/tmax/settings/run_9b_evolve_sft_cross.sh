#!/usr/bin/env bash
# Qwen3.5-9B: multi-iteration harness evolve + cross-candidate-harness SFT data.
TMAX_SETTING=9b_sft_cross exec bash "$(dirname "$0")/_submit.sh"

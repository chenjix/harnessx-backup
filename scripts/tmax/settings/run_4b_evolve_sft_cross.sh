#!/usr/bin/env bash
# Qwen3.5-4B: multi-iteration harness evolve + cross-candidate-harness SFT data.
TMAX_SETTING=4b_sft_cross exec bash "$(dirname "$0")/_submit.sh"

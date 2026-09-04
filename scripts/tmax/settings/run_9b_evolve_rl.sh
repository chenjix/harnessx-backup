#!/usr/bin/env bash
# Qwen3.5-9B: multi-iteration harness evolve + online RL, without SFT.
TMAX_SETTING=9b_rl exec bash "$(dirname "$0")/_submit.sh"

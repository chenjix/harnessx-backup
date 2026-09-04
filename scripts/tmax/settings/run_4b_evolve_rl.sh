#!/usr/bin/env bash
# Qwen3.5-4B: multi-iteration harness evolve + online RL, without SFT.
TMAX_SETTING=4b_rl exec bash "$(dirname "$0")/_submit.sh"

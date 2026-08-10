MODEL_SIZE ?= 4b
STAGES ?= evolve,sft,eval-sft

.PHONY: doctor status evolve build-sft train-sft eval-base eval-sft build-grpo train-grpo pipeline
doctor:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/doctor.sh
status:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/status.sh
evolve:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/evolve.sh
build-sft:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/build_sft_data.sh
train-sft:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/train_sft.sh
eval-base:
	MODEL_SIZE=$(MODEL_SIZE) EVAL_SFT=0 bash scripts/evaluate.sh
eval-sft:
	MODEL_SIZE=$(MODEL_SIZE) EVAL_SFT=1 bash scripts/evaluate.sh
build-grpo:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/build_grpo_replay.sh
train-grpo:
	MODEL_SIZE=$(MODEL_SIZE) bash scripts/train_grpo.sh
pipeline:
	bash scripts/run_pipeline.sh $(MODEL_SIZE) $(STAGES)

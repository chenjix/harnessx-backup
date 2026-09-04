from datasets import Dataset

from open_instruct.data_loader import HFDataLoader, StreamingDataLoaderConfig


def _loader_for_weights() -> HFDataLoader:
    loader = object.__new__(HFDataLoader)
    loader._full_dataset = Dataset.from_dict(
        {"ground_truth": ["frontier", "easy", "unknown"], "index": [0, 1, 2]}
    )
    loader._frontier_prior = {
        "frontier": {"successes": 5, "attempts": 10},
        "easy": {"successes": 10, "attempts": 10},
    }
    loader._frontier_online = {}
    loader._frontier_num_rollouts = 8
    loader._frontier_exploration_fraction = 0.2
    loader._frontier_domain_balanced = False
    return loader


def test_frontier_weight_prefers_disagreement_over_easy_task() -> None:
    loader = _loader_for_weights()
    assert loader._frontier_weight(0) > loader._frontier_weight(1)
    assert loader._frontier_weight(1) > 0.0  # anchors remain explorable


def test_online_outcomes_move_task_out_of_frontier() -> None:
    loader = _loader_for_weights()
    before = loader._frontier_weight(2)
    loader.record_frontier_outcome(2, [1.0] * 32)
    assert loader._frontier_weight(2) < before


def test_non_submit_all_fail_is_less_valuable_than_submitted_all_fail() -> None:
    loader = _loader_for_weights()
    loader.record_frontier_outcome(2, [0.0] * 8, [False] * 8)
    non_submit_weight = loader._frontier_weight(2)
    loader._frontier_online = {}
    loader.record_frontier_outcome(2, [0.0] * 8, [True] * 8)
    assert loader._frontier_weight(2) > non_submit_weight


def test_domain_balanced_order_starts_with_distinct_domains() -> None:
    loader = _loader_for_weights()
    loader._full_dataset = Dataset.from_dict(
        {
            "ground_truth": ["a0", "a1", "b0", "b1", "c0"],
            "domain": ["a", "a", "b", "b", "c"],
            "index": list(range(5)),
        }
    )
    order = loader._domain_balanced_indices(
        __import__("torch").ones(5, dtype=__import__("torch").float64),
        __import__("torch").Generator().manual_seed(1),
    )
    assert len({loader._full_dataset[int(i)]["domain"] for i in order[:3]}) == 3


def test_sampling_budget_cannot_be_smaller_than_target_batch() -> None:
    try:
        StreamingDataLoaderConfig(
            active_sampling=True,
            filter_zero_std_samples=True,
            async_steps=2,
            num_unique_prompts_rollout=4,
            max_sampled_prompt_groups_per_step=3,
        )
    except ValueError as exc:
        assert "max_sampled_prompt_groups_per_step" in str(exc)
    else:
        raise AssertionError("expected invalid active-sampling budget to fail")

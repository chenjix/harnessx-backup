#!/usr/bin/env python3
"""Unit tests for Tmax RL mixer construction. Run: python this_file.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import build_tmax_rl_dataset as D  # noqa: E402

SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def test_wrap_user_message_embeds_submit_and_task() -> None:
    wrapped = D.wrap_user_message("fix the bug in foo.py")
    assert wrapped.startswith("Please solve this task:")
    assert "fix the bug in foo.py" in wrapped
    assert SUBMIT in wrapped
    assert "{{task}}" not in wrapped


def test_wrap_user_message_is_idempotent() -> None:
    once = D.wrap_user_message("do the thing")
    twice = D.wrap_user_message(once)
    assert once == twice


def test_row_to_record_user_is_vanillux_instance() -> None:
    rec = D._row_to_record(
        {
            "task_id": "task_000001_abcd",
            "description": "Write /home/user/out.txt",
            "test_final_state": "assert True",
            "container_def": "From: ubuntu:22.04\n%post\necho hi\n",
            "domain": "debugging",
        },
        env_name="swerl_vanillux_sandbox",
        image_mode="local",
        image_registry="",
    )
    assert rec is not None
    user = rec["messages"][1]["content"]
    assert user.startswith("Please solve this task:")
    assert "Write /home/user/out.txt" in user
    assert SUBMIT in user
    assert rec["dataset"] == "passthrough"
    assert rec["env_config"]["env_name"] == "swerl_vanillux_sandbox"
    assert rec["env_config"]["image"].startswith("tmax-eval:")


def test_write_dataset_records_prompt_schema() -> None:
    rec = D._row_to_record(
        {
            "task_id": "task_000002_efgh",
            "description": "Create a file",
            "test_final_state": "assert True",
            "container_def": "From: ubuntu:22.04\n%post\ntrue\n",
        },
        env_name="swerl_vanillux_sandbox",
        image_mode="local",
        image_registry="",
    )
    assert rec is not None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rlset"
        summary = D.write_dataset([rec], out)
        assert summary["prompt_schema"] == D.PROMPT_SCHEMA
        row = json.loads((out / "train.jsonl").read_text().splitlines()[0])
        assert SUBMIT in row["messages"][1]["content"]
        # instruction.md stays the raw task, not the wrapped user turn
        inst = (out / "task_data" / "task_000002_efgh" / "instruction.md").read_text()
        assert inst.strip() == "Create a file"
        assert SUBMIT not in inst


def test_write_dataset_rejects_unwrapped_user() -> None:
    bad = {
        "messages": [{"role": "user", "content": "raw taxonomy only"}],
        "ground_truth": "task_bad",
        "dataset": "passthrough",
        "env_config": {"env_name": "swerl_vanillux_sandbox", "task_id": "task_bad", "image": "tmax-eval:x"},
        "_raw": {
            "task_id": "task_bad",
            "description": "raw taxonomy only",
            "test_final_state": "assert True",
            "container_def": "",
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        try:
            D.write_dataset([bad], Path(tmp) / "bad")
        except SystemExit as exc:
            assert "COMPLETE_TASK" in str(exc)
        else:
            raise AssertionError("expected SystemExit for unwrapped user message")


if __name__ == "__main__":
    test_wrap_user_message_embeds_submit_and_task()
    test_wrap_user_message_is_idempotent()
    test_row_to_record_user_is_vanillux_instance()
    test_write_dataset_records_prompt_schema()
    test_write_dataset_rejects_unwrapped_user()
    print("ok")

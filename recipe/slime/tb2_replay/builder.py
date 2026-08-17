# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harnessx.rl.task import RLTask

if TYPE_CHECKING:
    from slime.utils.types import Sample


class Tb2ReplayTaskBuilder:
    """Build RLTask instances from replay-buffer samples.

    Expected sample schema:
      - prompt: str
      - label: optional str
    """

    def build(self, sample: "Sample") -> RLTask:
        raw_prompt: Any = sample.prompt
        label: str = sample.label or ""
        description = raw_prompt if isinstance(raw_prompt, str) else str(raw_prompt or "")
        return RLTask(
            description=description,
            label=label,
            task_type="tb2_replay",
            metadata={},
        )

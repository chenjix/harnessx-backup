# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

from harnessx.tools.builtin import bash_tool
from harnessx.tools.inmemory import InMemoryToolRegistry


def get_registry() -> InMemoryToolRegistry:
    """Return an InMemoryToolRegistry with Bash registered."""
    registry = InMemoryToolRegistry()
    registry.register(bash_tool)
    return registry


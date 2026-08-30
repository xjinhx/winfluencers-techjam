"""Submission entry point.

The harness imports `Agent` from here and constructs it with the catalog path.
All logic lives in the `shopping_copilot` package; this file only chooses which
configuration to load, so the entry point stays a stable, boring seam.

Set SHOPPING_COPILOT_CONFIG to a JSON file to override tuned parameters without
touching code (see `tools/tune.py`, which writes exactly that file).
"""

from __future__ import annotations

import os
from pathlib import Path

from shopping_copilot.agent import Agent as _Agent
from shopping_copilot.config import Config

_CONFIG_ENV = "SHOPPING_COPILOT_CONFIG"
_DEFAULT_TUNED = Path(__file__).resolve().parent.parent / "config" / "tuned.json"


def _load_config() -> Config | None:
    override = os.environ.get(_CONFIG_ENV)
    if override and Path(override).is_file():
        return Config.load(override)
    if _DEFAULT_TUNED.is_file():
        return Config.load(_DEFAULT_TUNED)
    return None


class Agent(_Agent):
    """The contract-facing Agent: `reset(...)` and `respond(...)`."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        super().__init__(catalog_path, config=_load_config())

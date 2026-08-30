"""Shared harness for the tuning and ablation tools.

Both need the same thing: build the agent once, then score many configurations
against the official evaluator. Rebuilding the indexes per trial would cost
twenty seconds a time for no reason, so `apply_config` patches the live agent
instead.

Everything here calls the *official* `evaluator.evaluate`. Neither tool
reimplements scoring, so a number produced here is the number the harness
produces.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from shopping_copilot.agent import Agent
from shopping_copilot.config import Config

CATALOG = "data/catalog.jsonl"
DATASET = "data/public_set.jsonl"


class Bench:
    """One built agent plus the loaded evaluation fixtures."""

    def __init__(self, catalog_path: str = CATALOG, dataset_path: str = DATASET) -> None:
        self.samples = load_jsonl(dataset_path)
        self.catalog_ids, self.categories, self.products = catalog_index(catalog_path)
        self.agent = Agent(catalog_path, config=Config())

    def subset(self, n: int | None, seed: int = 20240501) -> list[dict]:
        """A stratified sample, for fast tuning passes.

        Stratified by scenario because the mix (40/40/15/5) is fixed and a
        uniform sample of 60 would routinely contain two boundary sessions or
        none, making the score jump around for reasons unrelated to the config.
        """
        if not n or n >= len(self.samples):
            return self.samples
        buckets: dict[str, list[dict]] = {}
        for sample in self.samples:
            buckets.setdefault(sample["scenario_type"], []).append(sample)
        rng = random.Random(seed)
        chosen: list[dict] = []
        for scenario, rows in sorted(buckets.items()):
            take = max(1, round(n * len(rows) / len(self.samples)))
            chosen.extend(rng.sample(rows, min(take, len(rows))))
        chosen.sort(key=lambda s: s["sample_id"])
        return chosen

    def score(self, config: Config, samples: list[dict] | None = None) -> dict:
        self.agent.apply_config(config)
        return evaluate(
            self.agent,
            samples if samples is not None else self.samples,
            self.catalog_ids,
            self.categories,
            self.products,
        )


def summarise(result: dict) -> dict:
    return {
        "technical_score": result["recommended_technical_score"],
        "hit_rate_at_10": result["hit_rate_at_10"],
        "mrr": result["mrr"],
        "mttc": result["mttc"],
        "efficiency": result["efficiency"],
    }


def write_json(path: str | Path, payload: object) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

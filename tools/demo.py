"""Print a full multi-turn session transcript.

The competition asks for one demonstrated multi-turn session as a deliverable.
This drives the real agent against the real simulated customer and prints what
actually happened each turn -- the intent route, the slots that had been
resolved, the question asked and why the gate allowed it, and where the hidden
target sat in the returned list.

Run:  python -m tools.demo                     (first override session)
      python -m tools.demo --sample public_0007
      python -m tools.demo --scenario browsing --count 3
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    behavior_for,
    coarse_category,
    customer_reply,
    initial_message,
    intent_card,
)
from shopping_copilot.intent import route
from starter.agent import Agent

RULE = "-" * 78


def run_session(agent: Agent, sample: dict, products: dict, categories: dict) -> None:
    target = str(sample["ground_truth"]["parent_asin"])
    product = products[target]
    card = intent_card(product)
    rng = random.Random(f"{sample.get('sample_id', '')}\0{sample.get('scenario_type', '')}")
    behaviour = behavior_for(str(sample["scenario_type"]), card, rng)
    effective = {**sample, "intent_card": card, "behavior": behaviour}

    print(RULE)
    print(f"session   {sample['sample_id']}   scenario={sample['scenario_type']}   "
          f"difficulty={sample.get('difficulty_bucket')}")
    print(f"profile   {sample['user_profile'].get('summary', '')}")
    print(f"TARGET    {target}  {str(product.get('title'))[:66]}")
    print(f"          (hidden from the agent; shown here only to mark the hit)")
    print(RULE)

    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    message = initial_message(effective, coarse_category(categories.get(target, [])), disclosed)

    agent.reset(sample["sample_id"], sample["user_profile"])
    for turn in range(1, MAX_TURNS + 1):
        response = agent.respond(sample["sample_id"], message, turn, TOP_K)
        state = agent.sessions[sample["sample_id"]]
        decision = route(state, agent.config.dialogue)
        asins = [item["parent_asin"] for item in response["recommendations"]]
        rank = asins.index(target) + 1 if target in asins else None

        print(f"\nturn {turn}")
        print(f"  customer  {message}")
        print(f"  agent     {response['message']}")
        if response["ask_attribute"]:
            print(f"  asks      ask_attribute={response['ask_attribute']!r}")
        print(f"  intent    {decision.intent} (confidence {decision.confidence:.2f})")
        print(f"  slots     {state.constraints.filled_slots() or 'none resolved'}")
        print(f"  disclosed {state.disclosed_count} constraint span(s)"
              + (f", override at turn {state.override_turn}" if state.override_turn else ""))
        for position, asin in enumerate(asins[:5], start=1):
            mark = "  <-- TARGET" if asin == target else ""
            title = str(products[asin].get("title", ""))[:56]
            print(f"    {position:>2}. {asin}  {title}{mark}")

        if rank is not None and override_applied:
            print(f"\n  HIT at turn {turn}, rank {rank} (reciprocal rank {1 / rank:.3f})")
            return
        if rank is not None and not override_applied:
            print("\n  target is present but the override has not been sent yet, "
                  "so this cannot convert")

        if turn == MAX_TURNS:
            break
        override = effective.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            message = str(override.get("message", "Actually, please ignore my earlier preference."))
        else:
            message, boundary_used = customer_reply(
                effective, response.get("ask_attribute"), disclosed, boundary_used
            )
    print("\n  MISS -- ran out of turns")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-turn session demo")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--sample", default=None, help="a specific sample_id")
    parser.add_argument("--scenario", default="intent_override")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()

    products: dict[str, dict] = {}
    categories: dict[str, list[str]] = {}
    with Path(args.catalog).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            asin = str(row["parent_asin"])
            products[asin] = row
            categories[asin] = [str(v) for v in row.get("categories") or []]

    samples = [
        json.loads(line)
        for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.sample:
        chosen = [s for s in samples if s["sample_id"] == args.sample]
    else:
        chosen = [s for s in samples if s["scenario_type"] == args.scenario][: args.count]
    if not chosen:
        raise SystemExit("no matching sessions")

    agent = Agent(args.catalog)
    for sample in chosen:
        run_session(agent, sample, products, categories)


if __name__ == "__main__":
    main()

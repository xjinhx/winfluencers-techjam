from __future__ import annotations

import argparse
import json
import logging
import random
import re
import statistics
import uuid
from collections import defaultdict
from pathlib import Path

from starter.agent import Agent

logger = logging.getLogger(__name__)

MAX_TURNS = 10
TOP_K = 10
ALLOWED_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
}
MATERIALS = ("cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon", "fabric")
SEARCH_FIELDS = ("title", "features", "details", "description", "categories", "store")
MATERIAL_RE = re.compile(r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric)\b", re.I)
COLOR_RE = re.compile(r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange)\b", re.I)


def searchable_text(product: dict) -> str:
    parts: list[str] = []
    for field in SEARCH_FIELDS:
        value = product.get(field)
        if isinstance(value, dict):
            parts.extend(f"{key} {item}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).strip()


def _flatten_values(value: object) -> list[str]:
    # Non-scalar values (e.g. a nested "Best Sellers Rank" dict) are excluded here
    # so a raw Python repr never becomes customer-facing constraint text. They
    # remain harmless, unchanged raw text for searchable_text()'s full-text corpus.
    if isinstance(value, dict):
        return [
            f"{key}: {item}"
            for key, item in value.items()
            if item not in (None, "", []) and not isinstance(item, (dict, list))
        ]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "") and not isinstance(item, (dict, list))]
    return [str(value)] if value not in (None, "") else []


def _clean_constraint(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip(" -;,.\t\n")[:limit].rstrip()


def intent_card(product: dict, limit: int = 180) -> dict:
    title = _clean_constraint(str(product.get("title") or "product"), limit)
    candidates = [*_flatten_values(product.get("features")), *_flatten_values(product.get("details"))]
    corpus = searchable_text(product)
    material = MATERIAL_RE.search(corpus)
    color = COLOR_RE.search(corpus)
    if material:
        candidates.insert(0, material.group(1).lower())
    if color:
        candidates.insert(1, f"color: {color.group(1).lower()}")
    price = product.get("price")
    # Only a real numeric price is budget-worthy. Non-numeric strings ("—",
    # "from 12.99") are the catalog's own "unavailable"/"range" placeholders,
    # not malformed data — treat them the same as a missing price here.
    # The catalog's own price field is never modified.
    if isinstance(price, (int, float)):
        candidates.append(f"budget around ${price}")
    cleaned = list(dict.fromkeys(_clean_constraint(item, limit) for item in candidates if _clean_constraint(item, limit)))
    if not cleaned:
        cleaned = [title]
    return {
        "target_category": title,
        "hard_constraints": cleaned[:2],
        "soft_preferences": cleaned[2:4] or cleaned[:1],
    }


def behavior_for(scenario: str, card: dict, rng: random.Random) -> dict:
    behavior: dict = {"scenario_type": scenario}
    if scenario == "intent_override":
        hard = card["hard_constraints"]
        soft = card["soft_preferences"]
        old_value = soft[-1] if soft else "I prefer a different style."
        new_value = hard[0] if hard else "Please prioritize the target requirements."
        behavior["override"] = {
            "turn": rng.choice([3, 4]),
            "old_value": old_value,
            "new_value": new_value,
            "message": f"Actually, ignore my earlier preference. What I need is: {new_value}.",
        }
    return behavior


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalize_recommendations(payload: object, catalog_ids: set[str]) -> list[str]:
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in payload:
        value = item.get("parent_asin", "") if isinstance(item, dict) else item
        parent_asin = str(value).strip()
        if not parent_asin or parent_asin in seen or parent_asin not in catalog_ids:
            continue
        seen.add(parent_asin)
        result.append(parent_asin)
        if len(result) >= TOP_K:
            break
    return result


def catalog_index(
    catalog_path: str | Path,
) -> tuple[set[str], dict[str, list[str]], dict[str, dict], dict]:
    identifiers: set[str] = set()
    categories: dict[str, list[str]] = {}
    products: dict[str, dict] = {}
    report = {
        "total_lines": 0,
        "blank_lines": 0,
        "malformed_json": 0,
        "non_dict_records": 0,
        "valid_records": 0,
        "skipped_records": 0,
        "normalized": {"categories_wrapped_as_list": 0, "duplicate_parent_asin_skipped": 0},
        "warnings": [],
    }

    def _warn(line_no: int, parent_asin: str | None, field: str | None, reason: str, action: str) -> None:
        report["warnings"].append({
            "line": line_no, "parent_asin": parent_asin, "field": field,
            "reason": reason, "action": action,
        })
        logger.warning("catalog line %s (%s): %s -> %s", line_no, field, reason, action)

    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            report["total_lines"] += 1
            if not line.strip():
                report["blank_lines"] += 1
                continue

            try:
                product = json.loads(line)
            except json.JSONDecodeError as exc:
                report["malformed_json"] += 1
                report["skipped_records"] += 1
                _warn(line_no, None, None, f"malformed JSON: {exc}", "skip_record")
                continue

            if not isinstance(product, dict):
                report["non_dict_records"] += 1
                report["skipped_records"] += 1
                _warn(line_no, None, None, "record is not a JSON object", "skip_record")
                continue

            raw_parent_asin = product.get("parent_asin")
            parent_asin = raw_parent_asin.strip() if isinstance(raw_parent_asin, str) else ""
            if not parent_asin:
                report["skipped_records"] += 1
                _warn(line_no, None, "parent_asin", "missing, empty, or non-string parent_asin", "skip_record")
                continue

            if parent_asin in identifiers:
                report["skipped_records"] += 1
                report["normalized"]["duplicate_parent_asin_skipped"] += 1
                _warn(line_no, parent_asin, "parent_asin", "duplicate parent_asin, kept first occurrence", "skip_record")
                continue

            raw_categories = product.get("categories")
            if isinstance(raw_categories, list):
                category_list = [str(value) for value in raw_categories]
            elif isinstance(raw_categories, str):
                category_list = [raw_categories]
                report["normalized"]["categories_wrapped_as_list"] += 1
                _warn(line_no, parent_asin, "categories", "categories was a string, wrapped as a single-element list", "normalize")
            else:
                category_list = []

            identifiers.add(parent_asin)
            categories[parent_asin] = category_list
            products[parent_asin] = product
            report["valid_records"] += 1

    return identifiers, categories, products, report


def coarse_category(values: list[str]) -> str:
    excluded = {"clothing", "clothing shoes & jewelry", "clothing, shoes & jewelry"}
    cleaned: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part and part.lower() not in excluded:
                cleaned.append(part)
    return " ".join(cleaned[-2:]) if cleaned else "clothing item"


def classify_constraint(value: str) -> str:
    lowered = value.lower()
    if "budget" in lowered or re.search(r"(?:\$|<=|under)\s*\d", lowered):
        return "budget"
    if any(material in lowered for material in MATERIALS):
        return "material"
    if any(word in lowered for word in ("color", "black", "white", "blue", "red", "pink", "green")):
        return "color"
    if any(word in lowered for word in ("size", "sizing", "width", "wide", "narrow")):
        return "size"
    if any(word in lowered for word in ("department", "style", "fit", "sleeve", "neck")):
        return "style"
    if any(word in lowered for word in ("hiking", "running", "gym", "winter", "outdoor", "work")):
        return "use_case"
    return "feature"


def initial_message(sample: dict, category: str, disclosed: set[str]) -> str:
    scenario = sample["scenario_type"]
    if scenario == "buying" and sample["intent_card"].get("hard_constraints"):
        constraint = str(sample["intent_card"]["hard_constraints"][0])
        disclosed.add(constraint)
        return f"I'm looking for {category}. A key requirement is: {constraint}."
    if scenario == "intent_override":
        old_value = str(sample["behavior"]["override"]["old_value"])
        return f"I'm looking for {category}. {old_value}"
    return f"I'm looking for {category}, but I'm still exploring."


def customer_reply(sample: dict, ask_attribute: object, disclosed: set[str], boundary_used: bool) -> tuple[str, bool]:
    attribute = ask_attribute if isinstance(ask_attribute, str) else None
    if sample["scenario_type"] == "boundary" and not boundary_used and attribute:
        return f"I don't have a preference for {attribute}; please use your judgment.", True
    if not attribute:
        return "Those options are not quite right yet. Ask me about one specific attribute.", boundary_used
    if attribute not in ALLOWED_ATTRIBUTES:
        attribute = "other"
    constraints = [
        *[str(value) for value in sample["intent_card"].get("hard_constraints", [])],
        *[str(value) for value in sample["intent_card"].get("soft_preferences", [])],
    ]
    matches = [
        value for value in constraints
        if value not in disclosed and (attribute == "other" or classify_constraint(value) == attribute)
    ][:2]
    if not matches:
        return f"I don't have an additional preference for {attribute}.", boundary_used
    disclosed.update(matches)
    return "For that, what matters is: " + "; ".join(matches) + ".", boundary_used


def metric_summary(sessions: list[dict]) -> dict:
    if not sessions:
        return {"sample_count": 0, "hit_rate_at_10": 0.0, "mrr": 0.0, "mttc": None}
    hit_rate = sum(int(item["hit"]) for item in sessions) / len(sessions)
    mrr = statistics.fmean(item["reciprocal_rank"] for item in sessions)
    mttc = statistics.fmean(
        item["first_hit_turn"] if item["first_hit_turn"] is not None else MAX_TURNS + 1 for item in sessions
    )
    return {
        "sample_count": len(sessions),
        "hit_rate_at_10": round(hit_rate, 6),
        "mrr": round(mrr, 6),
        "mttc": round(mttc, 6),
    }


def materialize_hidden_fields(sample: dict, products: dict[str, dict]) -> tuple[dict, dict]:
    if "intent_card" in sample and "behavior" in sample:
        return sample["intent_card"], sample["behavior"]
    target = str(sample["ground_truth"]["parent_asin"]).strip()
    product = products[target]
    card = intent_card(product)
    seed_source = f"{sample.get('sample_id', '')}\0{sample.get('scenario_type', '')}"
    rng = random.Random(seed_source)
    behavior = behavior_for(str(sample["scenario_type"]), card, rng)
    return card, behavior


class InvalidSessionError(Exception):
    """Raised when the evaluator cannot legitimately construct a scoreable session.

    Distinct from an agent execution failure: this means the evaluation DATA
    itself is invalid (no target, no way to build a compliant reset_request),
    not that the agent failed a legitimate test. See docs/PHASE3_DESIGN.md §5-6.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def validate_session(sample: object, catalog_ids: set[str]) -> None:
    """Structural pre-check for the INVALID EVALUATION RECORD gate.

    Checks presence/shape only (not value quality) — a wrong-typed but
    present user_profile sub-field, or an unrecognized scenario_type value,
    both pass through: the evaluator can still legitimately run the
    protocol for those. Raises InvalidSessionError otherwise.
    """
    if not isinstance(sample, dict):
        raise InvalidSessionError("<session>", "session record is not a JSON object")

    ground_truth = sample.get("ground_truth")
    if not isinstance(ground_truth, dict):
        raise InvalidSessionError("ground_truth", "missing or not an object")

    raw_target = ground_truth.get("parent_asin")
    target = raw_target.strip() if isinstance(raw_target, str) else ""
    if not target:
        raise InvalidSessionError("ground_truth.parent_asin", "missing or not a non-empty string")
    if target not in catalog_ids:
        raise InvalidSessionError("ground_truth.parent_asin", "target parent_asin not present in catalog")

    if "user_profile" not in sample:
        raise InvalidSessionError("user_profile", "missing required field")
    if "scenario_type" not in sample:
        raise InvalidSessionError("scenario_type", "missing required field")


def run_session(
    agent: Agent,
    sample: dict,
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> tuple[dict, int, int]:
    """Execute one already-validated session. Exceptions here (including a
    reset() failure) are agent execution failures, not data invalidity —
    the caller treats them as a documented miss, not an exclusion.
    """
    target = str(sample["ground_truth"]["parent_asin"]).strip()
    session_id = f"public_{uuid.uuid4().hex}"
    agent.reset(session_id, sample["user_profile"])
    effective_intent_card, effective_behavior = materialize_hidden_fields(sample, products)
    effective_sample = {**sample, "intent_card": effective_intent_card, "behavior": effective_behavior}
    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    user_message = initial_message(effective_sample, coarse_category(categories.get(target, [])), disclosed)
    hit_turn: int | None = None
    best_rank: int | None = None
    prompt_tokens = 0
    completion_tokens = 0
    for turn in range(1, MAX_TURNS + 1):
        try:
            response = agent.respond(session_id, user_message, turn, TOP_K)
        except Exception:
            response = {"message": "", "ask_attribute": None, "recommendations": []}
        if not isinstance(response, dict) or not isinstance(response.get("message"), str):
            response = {"message": "", "ask_attribute": None, "recommendations": []}
        usage = response.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] >= 0:
                prompt_tokens += usage["prompt_tokens"]
            if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] >= 0:
                completion_tokens += usage["completion_tokens"]
        ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
        if override_applied and target in ranked:
            best_rank = ranked.index(target) + 1
            hit_turn = turn
            break
        if turn == MAX_TURNS:
            break
        override = effective_sample.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            user_message = str(override.get("message", "Actually, please ignore my earlier preference."))
        else:
            user_message, boundary_used = customer_reply(
                effective_sample, response.get("ask_attribute"), disclosed, boundary_used
            )
    session_result = {
        "sample_id": sample.get("sample_id"),
        "scenario_type": sample["scenario_type"],
        "hit": hit_turn is not None,
        "first_hit_turn": hit_turn,
        "best_rank": best_rank,
        "reciprocal_rank": 0.0 if best_rank is None else 1.0 / best_rank,
    }
    return session_result, prompt_tokens, completion_tokens


def evaluate(
    agent: Agent,
    samples: list[dict],
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> dict:
    sessions: list[dict] = []
    errors: list[dict] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    for index, sample in enumerate(samples):
        sample_id = sample.get("sample_id") if isinstance(sample, dict) else None
        identity = sample_id if sample_id is not None else f"<index {index}>"

        try:
            validate_session(sample, catalog_ids)
        except InvalidSessionError as exc:
            errors.append({
                "sample_id": identity,
                "field": exc.field,
                "reason": exc.reason,
                "action": "skip_session",
            })
            logger.error("session %s: %s (%s) -> skip_session", identity, exc.reason, exc.field)
            continue

        try:
            session_result, prompt_tokens, completion_tokens = run_session(
                agent, sample, catalog_ids, categories, products
            )
        except Exception as exc:
            logger.warning("session %s: agent execution failed (%s) -> counted as miss", identity, exc)
            session_result = {
                "sample_id": identity,
                "scenario_type": sample.get("scenario_type"),
                "hit": False,
                "first_hit_turn": None,
                "best_rank": None,
                "reciprocal_rank": 0.0,
            }
            prompt_tokens = 0
            completion_tokens = 0

        sessions.append(session_result)
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens

    overall = metric_summary(sessions)
    mttc = overall["mttc"]
    efficiency = 0.0 if mttc is None else max(0.0, min(1.0, (11.0 - float(mttc)) / 10.0))
    technical_score = 0.50 * overall["hit_rate_at_10"] + 0.30 * overall["mrr"] + 0.20 * efficiency
    grouped: dict[str, list[dict]] = defaultdict(list)
    for session in sessions:
        grouped[session["scenario_type"]].append(session)
    return {
        **overall,
        "efficiency": round(efficiency, 6),
        "recommended_technical_score": round(technical_score, 6),
        "reported_token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
        },
        "scenario_metrics": {name: metric_summary(grouped[name]) for name in sorted(grouped)},
        "sessions": sessions,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="TechJam public-set local evaluator")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="results.json")
    args = parser.parse_args()
    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products, catalog_report = catalog_index(args.catalog)
    result = evaluate(Agent(args.catalog), samples, catalog_ids, categories, products)
    result["catalog_report"] = catalog_report
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()

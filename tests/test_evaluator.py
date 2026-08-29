from __future__ import annotations

import unittest
from pathlib import Path
import json
import tempfile

from evaluator.local_evaluator import (
    catalog_index,
    evaluate,
    intent_card,
    metric_summary,
    normalize_recommendations,
    validate_session,
    InvalidSessionError,
)


class EchoTargetAgent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        self.session_id = session_id

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        asin = "A"
        if "B" in user_message:
            asin = "B"
        return {"message": "ok", "ask_attribute": None, "recommendations": [{"parent_asin": asin}]}


class AlwaysHitsAgent:
    """Always recommends whatever target is embedded in the message on turn 1."""

    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self, session_id: str, user_profile: dict) -> None:
        self.reset_calls += 1

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {"message": "ok", "ask_attribute": None, "recommendations": [{"parent_asin": "A"}]}


class ResetRaisesAgent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        raise RuntimeError("reset() boom")

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {"message": "ok", "ask_attribute": None, "recommendations": [{"parent_asin": "A"}]}


class RespondRaisesAgent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        pass

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        raise RuntimeError("respond() boom")


class InvalidOutputAgent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        pass

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return "not a dict"


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _catalog_row(**overrides: object) -> dict:
    row = {
        "parent_asin": "A",
        "title": "Base Product",
        "features": ["cotton"],
        "description": ["a base product"],
        "price": 19.99,
        "categories": ["Clothing, Shoes & Jewelry", "Men", "Shirts"],
        "details": {"Brand": "Acme"},
        "average_rating": 4.5,
        "rating_number": 10,
        "store": "Acme Store",
    }
    row.update(overrides)
    return row


def _session(**overrides: object) -> dict:
    sample = {
        "sample_id": "public_0001",
        "scenario_type": "buying",
        "user_profile": {
            "purchase_frequency": "3-4 prior purchases",
            "average_prior_rating": 4.2,
            "rating_style": "usually positive",
            "preference_tags": ["fit"],
            "summary": "x",
        },
        "ground_truth": {"parent_asin": "A"},
    }
    sample.update(overrides)
    return sample


class EvaluatorTest(unittest.TestCase):
    def test_normalization_preserves_first_valid_unique_order(self) -> None:
        payload = [
            {"parent_asin": "A"}, {"parent_asin": "bad"}, {"parent_asin": "A"},
            "B", {"parent_asin": "C"},
        ]
        self.assertEqual(normalize_recommendations(payload, {"A", "B", "C"}), ["A", "B", "C"])

    def test_metric_summary_assigns_turn_11_to_miss(self) -> None:
        sessions = [
            {"hit": True, "reciprocal_rank": .5, "first_hit_turn": 2},
            {"hit": False, "reciprocal_rank": 0.0, "first_hit_turn": None},
        ]
        self.assertEqual(metric_summary(sessions), {
            "sample_count": 2,
            "hit_rate_at_10": .5,
            "mrr": .25,
            "mttc": 6.5,
        })

    def test_evaluate_derives_hidden_fields_when_public_set_omits_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.jsonl"
            catalog_rows = [
                {
                    "parent_asin": "A",
                    "title": "Blue running shoe",
                    "features": ["cotton"],
                    "details": {"department": "womens"},
                    "description": ["walking shoe"],
                    "categories": ["Clothing", "Shoes"],
                    "store": "Example",
                    "average_rating": 4.2,
                    "rating_number": 10,
                    "price": 49.0,
                },
                {
                    "parent_asin": "B",
                    "title": "Black winter boot",
                    "features": ["leather"],
                    "details": {"department": "womens"},
                    "description": ["winter boot"],
                    "categories": ["Clothing", "Boots"],
                    "store": "Example",
                    "average_rating": 4.4,
                    "rating_number": 12,
                    "price": 89.0,
                },
            ]
            catalog_path.write_text("".join(json.dumps(row) + "\n" for row in catalog_rows), encoding="utf-8")
            catalog_ids, categories, products, _report = catalog_index(catalog_path)
            samples = [{
                "sample_id": "public_v2_0001",
                "scenario_type": "buying",
                "user_profile": {"summary": "x"},
                "ground_truth": {"parent_asin": "A"},
            }]
            result = evaluate(EchoTargetAgent(), samples, catalog_ids, categories, products)
            self.assertEqual(result["hit_rate_at_10"], 1.0)


class CatalogValidationTest(unittest.TestCase):
    def _index(self, lines: list[str]):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            _write_jsonl(path, lines)
            return catalog_index(path)

    def test_valid_record_loads(self) -> None:
        ids, categories, products, report = self._index([json.dumps(_catalog_row())])
        self.assertEqual(ids, {"A"})
        self.assertEqual(report["valid_records"], 1)
        self.assertEqual(report["skipped_records"], 0)

    def test_malformed_json_line_is_skipped_and_surrounding_records_load(self) -> None:
        lines = [json.dumps(_catalog_row(parent_asin="A")), "{not valid json", json.dumps(_catalog_row(parent_asin="B"))]
        ids, categories, products, report = self._index(lines)
        self.assertEqual(ids, {"A", "B"})
        self.assertEqual(report["malformed_json"], 1)
        self.assertEqual(report["skipped_records"], 1)

    def test_blank_line_is_skipped_silently(self) -> None:
        lines = [json.dumps(_catalog_row(parent_asin="A")), "", json.dumps(_catalog_row(parent_asin="B"))]
        ids, categories, products, report = self._index(lines)
        self.assertEqual(ids, {"A", "B"})
        self.assertEqual(report["blank_lines"], 1)
        self.assertEqual(report["skipped_records"], 0)

    def test_non_dict_record_is_skipped(self) -> None:
        lines = [json.dumps(_catalog_row(parent_asin="A")), json.dumps([1, 2, 3])]
        ids, categories, products, report = self._index(lines)
        self.assertEqual(ids, {"A"})
        self.assertEqual(report["non_dict_records"], 1)

    def test_missing_parent_asin_is_skipped(self) -> None:
        row = {k: v for k, v in _catalog_row().items() if k != "parent_asin"}
        ids, categories, products, report = self._index([json.dumps(row)])
        self.assertEqual(ids, set())
        self.assertEqual(report["skipped_records"], 1)

    def test_wrong_type_parent_asin_is_rejected_not_coerced(self) -> None:
        for bad_value in (12345, None, ["x"]):
            with self.subTest(bad_value=bad_value):
                ids, categories, products, report = self._index([json.dumps(_catalog_row(parent_asin=bad_value))])
                self.assertEqual(ids, set())
                self.assertNotIn("12345", ids)
                self.assertNotIn("None", ids)

    def test_empty_parent_asin_is_rejected(self) -> None:
        ids, categories, products, report = self._index([json.dumps(_catalog_row(parent_asin="   "))])
        self.assertEqual(ids, set())
        self.assertEqual(report["skipped_records"], 1)

    def test_whitespace_parent_asin_is_stripped(self) -> None:
        ids, categories, products, report = self._index([json.dumps(_catalog_row(parent_asin=" B000X "))])
        self.assertEqual(ids, {"B000X"})
        recommended = normalize_recommendations([{"parent_asin": "B000X"}], ids)
        self.assertEqual(recommended, ["B000X"])

    def test_duplicate_parent_asin_keeps_first_occurrence(self) -> None:
        lines = [
            json.dumps(_catalog_row(parent_asin="DUP", title="First")),
            json.dumps(_catalog_row(parent_asin="DUP", title="Second")),
        ]
        ids, categories, products, report = self._index(lines)
        self.assertEqual(ids, {"DUP"})
        self.assertEqual(products["DUP"]["title"], "First")
        self.assertEqual(report["normalized"]["duplicate_parent_asin_skipped"], 1)

    def test_categories_list_unchanged(self) -> None:
        ids, categories, products, report = self._index([json.dumps(_catalog_row(categories=["Clothing", "Men"]))])
        self.assertEqual(categories["A"], ["Clothing", "Men"])
        self.assertEqual(report["normalized"]["categories_wrapped_as_list"], 0)

    def test_categories_string_is_wrapped_not_split(self) -> None:
        ids, categories, products, report = self._index([json.dumps(_catalog_row(categories="Clothing"))])
        self.assertEqual(categories["A"], ["Clothing"])
        self.assertEqual(report["normalized"]["categories_wrapped_as_list"], 1)

    def test_sparse_valid_metadata_passes_through_unchanged(self) -> None:
        row = _catalog_row(features=[], description=[], details={}, store=None)
        ids, categories, products, report = self._index([json.dumps(row)])
        self.assertEqual(ids, {"A"})
        self.assertEqual(products["A"]["features"], [])
        self.assertEqual(products["A"]["description"], [])
        self.assertEqual(products["A"]["details"], {})
        self.assertIsNone(products["A"]["store"])
        self.assertEqual(report["skipped_records"], 0)


class IntentCardPriceAndDetailsTest(unittest.TestCase):
    def test_nested_dict_details_value_excluded_from_candidates(self) -> None:
        row = _catalog_row(details={"Best Sellers Rank": {"Clothing": 12345, "Shoes": 678}})
        card = intent_card(row)
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertFalse(any("Best Sellers Rank" in item for item in visible))

    def test_scalar_details_value_unchanged(self) -> None:
        row = _catalog_row(details={"Brand": "Acme"})
        card = intent_card(row)
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertTrue(any("Brand: Acme" in item for item in visible))

    def test_em_dash_price_produces_no_budget_candidate(self) -> None:
        card = intent_card(_catalog_row(price="—"))
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertFalse(any("budget" in item for item in visible))

    def test_from_range_price_produces_no_budget_candidate_and_is_not_rewritten(self) -> None:
        row = _catalog_row(price="from 12.99")
        card = intent_card(row)
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertFalse(any("budget" in item for item in visible))
        # The original catalog value itself must never be rewritten to a bare number.
        self.assertEqual(row["price"], "from 12.99")

    def test_null_price_still_suppressed(self) -> None:
        card = intent_card(_catalog_row(price=None))
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertFalse(any("budget" in item for item in visible))

    def test_numeric_price_still_included(self) -> None:
        card = intent_card(_catalog_row(price=19.99, features=[], details={}))
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertTrue(any("budget around $19.99" in item for item in visible))

    def test_zero_price_still_included(self) -> None:
        card = intent_card(_catalog_row(price=0, features=[], details={}))
        visible = card["hard_constraints"] + card["soft_preferences"]
        self.assertTrue(any("budget around $0" in item for item in visible))


class SessionValidationTest(unittest.TestCase):
    CATALOG_IDS = {"A"}

    def test_valid_session_accepted(self) -> None:
        validate_session(_session(), self.CATALOG_IDS)  # must not raise

    def test_missing_ground_truth_rejected(self) -> None:
        sample = {k: v for k, v in _session().items() if k != "ground_truth"}
        with self.assertRaises(InvalidSessionError) as ctx:
            validate_session(sample, self.CATALOG_IDS)
        self.assertEqual(ctx.exception.field, "ground_truth")

    def test_malformed_ground_truth_rejected(self) -> None:
        sample = _session(ground_truth="B0000000")
        with self.assertRaises(InvalidSessionError):
            validate_session(sample, self.CATALOG_IDS)

    def test_missing_target_rejected(self) -> None:
        sample = _session(ground_truth={})
        with self.assertRaises(InvalidSessionError) as ctx:
            validate_session(sample, self.CATALOG_IDS)
        self.assertEqual(ctx.exception.field, "ground_truth.parent_asin")

    def test_target_not_in_catalog_rejected(self) -> None:
        sample = _session(ground_truth={"parent_asin": "NOT_REAL"})
        with self.assertRaises(InvalidSessionError) as ctx:
            validate_session(sample, self.CATALOG_IDS)
        self.assertEqual(ctx.exception.field, "ground_truth.parent_asin")

    def test_missing_user_profile_rejected(self) -> None:
        sample = {k: v for k, v in _session().items() if k != "user_profile"}
        with self.assertRaises(InvalidSessionError) as ctx:
            validate_session(sample, self.CATALOG_IDS)
        self.assertEqual(ctx.exception.field, "user_profile")

    def test_malformed_but_present_user_profile_is_accepted(self) -> None:
        sample = _session(user_profile={"preference_tags": "not-a-list"})
        validate_session(sample, self.CATALOG_IDS)  # must not raise

    def test_missing_scenario_type_rejected(self) -> None:
        sample = {k: v for k, v in _session().items() if k != "scenario_type"}
        with self.assertRaises(InvalidSessionError) as ctx:
            validate_session(sample, self.CATALOG_IDS)
        self.assertEqual(ctx.exception.field, "scenario_type")

    def test_invalid_but_present_scenario_type_is_accepted(self) -> None:
        sample = _session(scenario_type="not_a_real_scenario")
        validate_session(sample, self.CATALOG_IDS)  # must not raise


class FailureIsolationTest(unittest.TestCase):
    def _catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.jsonl"
            _write_jsonl(path, [json.dumps(_catalog_row(parent_asin="A"))])
            return catalog_index(path)

    def test_invalid_evaluation_record_is_excluded_not_scored(self) -> None:
        ids, categories, products, _report = self._catalog()
        bad = _session(sample_id="bad", ground_truth={})
        result = evaluate(AlwaysHitsAgent(), [bad], ids, categories, products)
        self.assertEqual(result["sessions"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["sample_id"], "bad")
        self.assertEqual(result["errors"][0]["action"], "skip_session")

    def test_target_not_in_catalog_is_excluded_not_counted_as_miss(self) -> None:
        ids, categories, products, _report = self._catalog()
        bad = _session(sample_id="bad", ground_truth={"parent_asin": "NOT_REAL"})
        result = evaluate(AlwaysHitsAgent(), [bad], ids, categories, products)
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(len(result["errors"]), 1)

    def test_reset_failure_on_valid_session_counts_as_miss(self) -> None:
        ids, categories, products, _report = self._catalog()
        result = evaluate(ResetRaisesAgent(), [_session()], ids, categories, products)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(result["sessions"][0]["hit"])

    def test_respond_failure_on_valid_session_counts_as_miss(self) -> None:
        ids, categories, products, _report = self._catalog()
        result = evaluate(RespondRaisesAgent(), [_session()], ids, categories, products)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(result["sessions"][0]["hit"])

    def test_invalid_agent_output_counts_as_miss(self) -> None:
        ids, categories, products, _report = self._catalog()
        result = evaluate(InvalidOutputAgent(), [_session()], ids, categories, products)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sample_count"], 1)
        self.assertFalse(result["sessions"][0]["hit"])

    def test_malformed_session_between_valid_sessions_does_not_destroy_results(self) -> None:
        ids, categories, products, _report = self._catalog()
        valid_a = _session(sample_id="A")
        valid_b = _session(sample_id="B")
        malformed_c = _session(sample_id="C", ground_truth={})
        valid_d = _session(sample_id="D")
        result = evaluate(AlwaysHitsAgent(), [valid_a, valid_b, malformed_c, valid_d], ids, categories, products)
        scored_ids = {session["sample_id"] for session in result["sessions"]}
        self.assertEqual(scored_ids, {"A", "B", "D"})
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["sample_id"], "C")
        self.assertTrue(all(session["hit"] for session in result["sessions"]))

    def test_all_valid_sessions_score_identically_to_before(self) -> None:
        ids, categories, products, _report = self._catalog()
        samples = [_session(sample_id="A"), _session(sample_id="B")]
        result = evaluate(AlwaysHitsAgent(), samples, ids, categories, products)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["hit_rate_at_10"], 1.0)
        self.assertEqual(result["mrr"], 1.0)


if __name__ == "__main__":
    unittest.main()

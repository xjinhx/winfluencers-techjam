"""Unit tests for the agent's components.

These cover the things that are cheap to get wrong and expensive to notice:
utterance parsing, override semantics, three-way constraint outcomes, contract
conformance, and determinism. They do not need the 50k catalog -- a fixture of
a few rows keeps the suite fast enough to run on every change.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_copilot.catalog import Catalog
from shopping_copilot.clarify import ALLOWED_ATTRIBUTES
from shopping_copilot.config import Config
from shopping_copilot.features import FEATURE_NAMES, extract
from shopping_copilot.profile import ShopperProfile
from shopping_copilot.ranking import LinearModel, Ranker
from shopping_copilot.state import ShoppingState, parse_utterance
from shopping_copilot.structured import (
    SATISFIED,
    UNKNOWN,
    VIOLATED,
    ConstraintExtractor,
    Constraints,
    check_gender,
    evaluate_all,
)
from shopping_copilot.text import stem, tokenize

FIXTURE = [
    {
        "parent_asin": "B000000001",
        "title": "Acme Women's Cotton V-Neck Undershirt Multipack",
        "features": ["100% Cotton", "Pull On closure", "Tagless comfort"],
        "description": ["A soft undershirt."],
        "price": 19.99,
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Underwear", "Undershirts"],
        "details": {"Department": "Womens", "Manufacturer": "Acme"},
        "average_rating": 4.5,
        "rating_number": 5000,
        "store": "Acme",
    },
    {
        "parent_asin": "B000000002",
        "title": "Zenith Men's Leather Belt Two Row Stitch",
        "features": ["100% Leather", "Buckle closure"],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Men", "Accessories", "Belts"],
        "details": {"Department": "Mens"},
        "average_rating": 4.0,
        "rating_number": 12,
        "store": "Zenith Leather",
    },
    {
        "parent_asin": "B000000003",
        "title": "Nova Basketball Mesh Shorts",
        "features": ["100% Polyester", "Drawstring closure"],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Boys", "Active", "Shorts"],
        "details": {},
        "average_rating": 3.8,
        "rating_number": 300,
        "store": "Nova",
    },
]


def build_catalog(directory: str) -> Catalog:
    path = Path(directory) / "catalog.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in FIXTURE) + "\n", encoding="utf-8"
    )
    return Catalog(path)


class TextTests(unittest.TestCase):
    def test_plural_stripping_is_conservative(self):
        self.assertEqual(stem("necklaces"), "necklace")
        self.assertEqual(stem("shorts"), "short")
        # Words that merely end in s must survive intact.
        self.assertEqual(stem("dress"), "dress")
        self.assertEqual(stem("this"), "this")

    def test_category_and_title_share_a_vocabulary(self):
        # The whole point of stemming here: the simulator says "Necklaces",
        # the title says "Necklace".
        self.assertEqual(tokenize("Necklaces"), tokenize("necklace"))


class ParsingTests(unittest.TestCase):
    def test_buying_opener_splits_category_from_constraint(self):
        u = parse_utterance(
            "I'm looking for Jewelry Necklaces. A key requirement is: Material:alloy.", 1
        )
        self.assertEqual(u.category_phrase, "Jewelry Necklaces")
        self.assertEqual(u.constraints, ["Material:alloy"])

    def test_template_words_never_become_constraints(self):
        # "A *key* requirement" must not retrieve every listing about keys.
        u = parse_utterance("I'm looking for Necklaces. A key requirement is: alloy.", 1)
        self.assertNotIn("key", " ".join(u.constraints).lower())

    def test_multiple_spans_split_on_semicolon(self):
        u = parse_utterance("For that, what matters is: Imported; Buckle closure.", 2)
        self.assertEqual(u.constraints, ["Imported", "Buckle closure"])

    def test_refusal_yields_no_constraints(self):
        u = parse_utterance("I don't have an additional preference for material.", 3)
        self.assertEqual(u.refused_attribute, "material")
        self.assertEqual(u.constraints, [])

    def test_boundary_refusal_is_detected(self):
        u = parse_utterance(
            "I don't have a preference for color; please use your judgment.", 2
        )
        self.assertEqual(u.refused_attribute, "color")
        self.assertEqual(u.constraints, [])

    def test_nudge_carries_no_content(self):
        u = parse_utterance(
            "Those options are not quite right yet. Ask me about one specific attribute.", 4
        )
        self.assertTrue(u.is_empty_feedback)
        self.assertEqual(u.constraints, [])

    def test_override_is_flagged_with_its_new_value(self):
        u = parse_utterance(
            "Actually, ignore my earlier preference. What I need is: 100% Leather.", 3
        )
        self.assertTrue(u.is_override)
        self.assertEqual(u.constraints, ["100% Leather"])

    def test_browsing_opener_has_category_and_no_constraint(self):
        u = parse_utterance("I'm looking for Women Dresses, but I'm still exploring.", 1)
        self.assertEqual(u.category_phrase, "Women Dresses")
        self.assertEqual(u.constraints, [])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.catalog = build_catalog(self.tmp.name)
        self.state = ShoppingState(
            "s1", ShopperProfile.parse({}), ConstraintExtractor(self.catalog)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_constraints_accumulate_across_turns(self):
        self.state.observe("I'm looking for Belts. A key requirement is: leather.", 1)
        self.state.observe("For that, what matters is: Buckle closure.", 2)
        self.assertEqual(self.state.disclosed_count, 2)

    def test_override_supersedes_earlier_spans_without_deleting_them(self):
        self.state.observe("I'm looking for Belts. A key requirement is: suede.", 1)
        self.state.observe(
            "Actually, ignore my earlier preference. What I need is: 100% Leather.", 3
        )
        self.assertEqual(self.state.override_turn, 3)
        superseded = [s for s in self.state.spans if s.superseded]
        self.assertEqual(len(superseded), 1)
        # Demoted, not deleted: the terms stay available to retrieval...
        self.assertLess(superseded[0].weight, 1.0)
        self.assertGreater(superseded[0].weight, 0.0)
        # ...but stop counting as a live constraint.
        self.assertNotIn("suede", self.state.active_terms())
        self.assertIn("leather", self.state.active_terms())

    def test_refused_attribute_is_never_asked_again(self):
        self.state.observe("I don't have a preference for color; use your judgment.", 2)
        self.assertIn("color", self.state.exhausted_attributes)

    def test_query_routes_category_at_the_category_index(self):
        self.state.observe("I'm looking for Underwear Undershirts. A key requirement is: cotton.", 1)
        queries = self.state.query()
        category_terms = {term for term, _ in queries["categories"]}
        self.assertIn("undershirt", category_terms)
        # Category terms outweigh constraint terms on the categories index.
        weights = dict(queries["categories"])
        self.assertGreater(weights["undershirt"], weights.get("cotton", 0.0))


class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.catalog = build_catalog(self.tmp.name)
        self.extractor = ConstraintExtractor(self.catalog)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gender_is_three_way_not_binary(self):
        womens = self.catalog.get("B000000001")
        mens = self.catalog.get("B000000002")
        constraints = Constraints(gender="women")
        self.assertEqual(check_gender(womens, constraints), SATISFIED)
        self.assertEqual(check_gender(mens, constraints), VIOLATED)
        # No stated gender means unknown, never violated.
        self.assertEqual(check_gender(mens, Constraints()), UNKNOWN)

    def test_missing_field_is_unknown_not_violated(self):
        # price is null on this row; a budget must not eliminate it.
        beltless_price = self.catalog.get("B000000002")
        outcomes = evaluate_all(beltless_price, Constraints(price_max=25.0))
        self.assertEqual(outcomes["price"], UNKNOWN)

    def test_material_absence_is_unknown_not_violated(self):
        shorts = self.catalog.get("B000000003")
        outcomes = evaluate_all(shorts, Constraints(materials={"leather"}))
        self.assertEqual(outcomes["material"], UNKNOWN)

    def test_gender_falls_back_to_the_title(self):
        # B000000003 has no details.Department at all.
        self.assertIsNone(self.catalog.get("B000000003").gender)
        self.assertEqual(self.catalog.get("B000000003").effective_gender, "boys")

    def test_material_and_gender_extracted_from_text(self):
        constraints = self.extractor.update(Constraints(), "womens 100% Leather belt")
        self.assertEqual(constraints.gender, "women")
        self.assertIn("leather", constraints.materials)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.catalog = build_catalog(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_vector_width_matches_the_declared_names(self):
        from shopping_copilot.features import ScoringContext

        ctx = ScoringContext(
            catalog=self.catalog,
            constraints=Constraints(),
            profile=ShopperProfile.parse({}),
            fused={}, per_field={}, dense={},
            query_terms=set(), query_bigrams=set(), category_terms=set(),
        )
        vector = extract(self.catalog.get("B000000001"), ctx)
        self.assertEqual(len(vector), len(FEATURE_NAMES))
        self.assertTrue(all(isinstance(v, float) for v in vector))


class ContractTests(unittest.TestCase):
    """The response must satisfy `docs/agent_api_contract.json`."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.catalog_path = Path(cls.tmp.name) / "catalog.jsonl"
        cls.catalog_path.write_text(
            "\n".join(json.dumps(row) for row in FIXTURE) + "\n", encoding="utf-8"
        )
        from shopping_copilot.agent import Agent

        cls.agent = Agent(cls.catalog_path, config=Config())

    @classmethod
    def tearDownClass(cls):
        cls.agent.close()
        cls.tmp.cleanup()

    def _respond(self, message: str, turn: int = 1) -> dict:
        self.agent.reset("t1", {"preference_tags": ["fit"], "purchase_frequency": "2 prior purchases"})
        return self.agent.respond("t1", message, turn, 10)

    def test_response_shape(self):
        response = self._respond("I'm looking for Underwear Undershirts. A key requirement is: cotton.")
        self.assertIsInstance(response["message"], str)
        self.assertTrue(
            response["ask_attribute"] is None
            or response["ask_attribute"] in ALLOWED_ATTRIBUTES
        )
        self.assertIsInstance(response["recommendations"], list)
        self.assertLessEqual(len(response["recommendations"]), 10)
        for item in response["recommendations"]:
            self.assertIn("parent_asin", item)
        usage = response["usage"]
        self.assertGreaterEqual(usage["prompt_tokens"], 0)
        self.assertGreaterEqual(usage["completion_tokens"], 0)

    def test_recommendations_are_unique_and_in_catalog(self):
        response = self._respond("I'm looking for Belts. A key requirement is: leather.")
        asins = [item["parent_asin"] for item in response["recommendations"]]
        self.assertEqual(len(asins), len(set(asins)))
        for asin in asins:
            self.assertIsNotNone(self.agent.catalog.get(asin))

    def test_is_deterministic(self):
        first = self._respond("I'm looking for Belts. A key requirement is: leather.")
        second = self._respond("I'm looking for Belts. A key requirement is: leather.")
        self.assertEqual(first["recommendations"], second["recommendations"])
        self.assertEqual(first["ask_attribute"], second["ask_attribute"])

    def test_empty_message_does_not_raise(self):
        response = self._respond("")
        self.assertIsInstance(response["message"], str)

    def test_respond_without_reset_degrades_instead_of_raising(self):
        response = self.agent.respond("never-reset", "I'm looking for Belts.", 1, 10)
        self.assertIsInstance(response["recommendations"], list)

    def test_recommends_on_ask_turns(self):
        # Decision D2: asking and answering are not mutually exclusive, and a
        # silent turn is a discarded chance at the hit.
        self.agent.reset("t2", {})
        response = self.agent.respond("t2", "I'm looking for Shorts, but I'm still exploring.", 1, 10)
        if response["ask_attribute"] is not None:
            self.assertTrue(response["recommendations"])


class IntentFusionTests(unittest.TestCase):
    """Per-intent fusion weight (see CLAUDE.md).

    `fused` double-counts the lexical and dense signals already in the vector,
    which drowns the structured features on constraint-bearing turns but is the
    best evidence available on browsing turns.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.catalog = build_catalog(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _ctx(self, intent):
        from shopping_copilot.features import ScoringContext

        return ScoringContext(
            catalog=self.catalog,
            constraints=Constraints(),
            profile=ShopperProfile.parse({}),
            fused={self.catalog.get("B000000001").idx: 1.0},
            per_field={}, dense={},
            query_terms=set(), query_bigrams=set(), category_terms=set(),
            intent=intent,
        )

    def test_absent_overrides_leave_the_default_path_untouched(self):
        config = Config()
        ranker = Ranker(config.ranking, config.priors, config.constraints)
        self.assertEqual(ranker.intent_models, {})

    def test_override_applies_only_to_its_own_intent(self):
        config = Config()
        config.ranking.w_fused_buying = 0.0
        ranker = Ranker(config.ranking, config.priors, config.constraints)
        product = self.catalog.get("B000000001")

        buying, _ = ranker.score_candidate(product, self._ctx("buying"))
        browsing, _ = ranker.score_candidate(product, self._ctx("browsing"))
        # Browsing keeps the full fused contribution; buying drops it.
        self.assertAlmostEqual(browsing - buying, config.ranking.w_fused, places=6)

    def test_a_supplied_model_is_never_rewritten(self):
        # The GBDT seam keeps control of its own vector.
        config = Config()
        config.ranking.w_fused_buying = 0.0
        ranker = Ranker(
            config.ranking, config.priors, config.constraints,
            model=LinearModel({"fused": 1.0}),
        )
        self.assertEqual(ranker.intent_models, {})


if __name__ == "__main__":
    unittest.main()

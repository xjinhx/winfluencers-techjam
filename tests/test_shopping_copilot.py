"""Unit tests for the agent's components.

These cover the things that are cheap to get wrong and expensive to notice:
utterance parsing, override semantics, three-way constraint outcomes, contract
conformance, and determinism. They do not need the 50k catalog -- a fixture of
a few rows keeps the suite fast enough to run on every change.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_copilot.catalog import Catalog
from shopping_copilot.clarify import ALLOWED_ATTRIBUTES, ClarificationPolicy, nqc
from shopping_copilot.config import Config
from shopping_copilot.features import FEATURE_NAMES, ScoringContext, extract
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
from shopping_copilot.text import (
    install_plural_exceptions,
    reset_plural_exceptions,
    stem,
    tokenize,
)

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
    def tearDown(self):
        # `stem` reads module-level state; a map left installed by one test
        # would leak into every test after it.
        reset_plural_exceptions()

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

    def test_uninstalled_map_keeps_the_original_rule(self):
        # No catalog built, no exceptions installed: behaviour must be exactly
        # what it was before the map existed, so a bare import is unaffected.
        reset_plural_exceptions()
        self.assertEqual(stem("hoodies"), "hoody")
        self.assertEqual(stem("bodies"), "body")

    def test_corpus_promotes_the_attested_ie_singular(self):
        # "Hoodies" in the query, "Hoodie" in every title. The default rule
        # sends them to 'hoody' and 'hoodie' -- two index terms whose postings
        # never meet -- so the corpus has to break the tie.
        install_plural_exceptions({
            "hoodies": 1066, "hoodie": 733, "hoody": 36,
            "booties": 183, "bootie": 608, "booty": 35,
        })
        self.assertEqual(stem("hoodies"), "hoodie")
        self.assertEqual(stem("booties"), "bootie")
        self.assertEqual(tokenize("Hoodies"), tokenize("Hoodie"))

    def test_corpus_leaves_genuine_y_plurals_alone(self):
        # The -ies -> -y rule is right far more often than it is wrong; the
        # fix must not cost us the cases it already handles.
        install_plural_exceptions({
            "bodies": 51, "body": 765,
            "babies": 231, "baby": 3500,
            "accessories": 7188, "accessory": 111,
        })
        self.assertEqual(stem("bodies"), "body")
        self.assertEqual(stem("babies"), "baby")
        self.assertEqual(stem("accessories"), "accessory")

    def test_es_is_trimmed_only_after_a_sibilant(self):
        # 'watches' must reach 'watch'; 'capes' must not reach 'cap', which is
        # a different garment with 709 listings of its own.
        install_plural_exceptions({
            "watches": 2373, "watch": 1965,
            "boxes": 278, "box": 361,
            "capes": 33, "cape": 76, "cap": 709,
        })
        self.assertEqual(stem("watches"), stem("watch"))
        self.assertEqual(stem("boxes"), stem("box"))
        self.assertEqual(stem("capes"), "cape")
        self.assertNotEqual(stem("capes"), stem("cap"))

    def test_ves_resolves_only_where_the_f_form_is_real(self):
        # scarf/scarves is an f-alternation. glove/gloves only looks like one.
        install_plural_exceptions({
            "scarves": 523, "scarf": 407,
            "gloves": 742, "glove": 113,
            "sleeves": 289, "sleeve": 5529,
        })
        self.assertEqual(stem("scarves"), "scarf")
        self.assertEqual(stem("gloves"), "glove")
        self.assertEqual(stem("sleeves"), "sleeve")

    def test_i_plurals_survive_the_is_guard(self):
        # The 'is' guard exists to protect 'tennis'; it also blocked every
        # garment whose singular ends in i.
        install_plural_exceptions({
            "bikinis": 345, "bikini": 434,
            "capris": 76, "capri": 180,
            "tennis": 434,
        })
        self.assertEqual(stem("bikinis"), "bikini")
        self.assertEqual(stem("capris"), "capri")
        self.assertEqual(stem("tennis"), "tennis")

    def test_singular_ending_in_s_is_not_stripped(self):
        # 'lens' has no attested reading as a plural, so it must not become
        # 'len' -- that would split it right back off 'lenses'.
        install_plural_exceptions({"lenses": 149, "lens": 125, "lense": 1})
        self.assertEqual(stem("lens"), "lens")
        self.assertEqual(stem("lenses"), stem("lens"))

    def test_a_plural_with_a_rare_singular_still_merges(self):
        # The identity guard above must not fire for a real plural: 'legging'
        # is thinly attested but it is still the singular of 'leggings'.
        install_plural_exceptions({"leggings": 500, "legging": 15})
        self.assertEqual(stem("leggings"), stem("legging"))

    def test_support_floor_rejects_thin_evidence(self):
        # 'footie' is too rare to displace the default rule.
        install_plural_exceptions({"footies": 101, "footie": 14, "footy": 0})
        self.assertEqual(stem("footies"), "footy")

    def test_irregular_plurals_land_on_the_attested_spelling(self):
        install_plural_exceptions({"women": 51340, "woman": 152,
                                   "men": 21911, "man": 114})
        self.assertEqual(stem("woman"), stem("women"))
        self.assertEqual(stem("man"), stem("men"))

    def test_different_words_never_collide(self):
        # 'brass' is a material and 'bras' is underwear; the -ss guard is the
        # only thing keeping them apart.
        install_plural_exceptions({"brass": 56, "bras": 966, "bra": 657})
        self.assertNotEqual(stem("brass"), stem("bras"))
        self.assertEqual(stem("bras"), "bra")


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

    def test_kids_is_a_parent_of_boys_and_girls_not_a_sibling(self):
        # A customer who says "toddler" sets gender="kids"; a listing filed
        # `boys` satisfies that. Treating them as siblings made 313 catalog
        # rows score VIOLATED (-0.23) against their own opening line.
        boys = self.catalog.get("B000000003")
        self.assertEqual(boys.effective_gender, "boys")
        self.assertEqual(check_gender(boys, Constraints(gender="kids")), SATISFIED)
        # The reverse is missing specificity, not contrary evidence.
        as_kids = replace(boys, gender="kids", gender_fallback="kids")
        self.assertEqual(check_gender(as_kids, Constraints(gender="boys")), UNKNOWN)
        # ...but siblings genuinely conflict, and adult rules are untouched.
        self.assertEqual(check_gender(boys, Constraints(gender="girls")), VIOLATED)
        self.assertEqual(
            check_gender(self.catalog.get("B000000002"), Constraints(gender="women")),
            VIOLATED,
        )

    def test_specific_child_audience_beats_the_generic_one(self):
        # "baby" hit first and resolved to `kids`, discarding the "girls"
        # standing next to it -- and the opening line is built from the
        # target's own category path, so this phrasing is common.
        self.assertEqual(
            self.extractor.update(Constraints(), "Baby Girls Bodysuits").gender, "girls"
        )
        self.assertEqual(
            self.extractor.update(Constraints(), "Baby Boys Bodysuits").gender, "boys"
        )
        # A generic phrase still resolves generically, and first-match
        # semantics are preserved everywhere else.
        self.assertEqual(
            self.extractor.update(Constraints(), "toddler pajamas").gender, "kids"
        )
        self.assertEqual(
            self.extractor.update(Constraints(), "kids shoes for women").gender, "kids"
        )


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

    def test_low_coverage_interactions_are_explicit_features(self):
        from shopping_copilot.features import FEATURE_INDEX, ScoringContext

        product = self.catalog.get("B000000001")
        ctx = ScoringContext(
            catalog=self.catalog,
            constraints=Constraints(),
            profile=ShopperProfile.parse({}),
            fused={}, per_field={"title": {product.idx: 0.75}}, dense={},
            query_terms={"not-in-product"}, query_bigrams=set(), category_terms=set(),
        )
        vector = extract(product, ctx)
        self.assertAlmostEqual(vector[FEATURE_INDEX["title_low_coverage"]], 0.75)
        self.assertAlmostEqual(
            vector[FEATURE_INDEX["popularity_low_coverage"]], product.popularity
        )


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

    def test_title_override_applies_only_to_its_own_intent(self):
        config = Config()
        config.ranking.w_bm25_title_buying = 0.0
        ranker = Ranker(config.ranking, config.priors, config.constraints)
        product = self.catalog.get("B000000001")
        buying_ctx = self._ctx("buying")
        browsing_ctx = self._ctx("browsing")
        buying_ctx.per_field = {"title": {product.idx: 1.0}}
        browsing_ctx.per_field = {"title": {product.idx: 1.0}}

        buying, _ = ranker.score_candidate(product, buying_ctx)
        browsing, _ = ranker.score_candidate(product, browsing_ctx)
        self.assertAlmostEqual(browsing - buying, config.ranking.w_bm25_title, places=6)

    def test_a_supplied_model_is_never_rewritten(self):
        # The GBDT seam keeps control of its own vector.
        config = Config()
        config.ranking.w_fused_buying = 0.0
        ranker = Ranker(
            config.ranking, config.priors, config.constraints,
            model=LinearModel({"fused": 1.0}),
        )
        self.assertEqual(ranker.intent_models, {})


class SpanMatchTest(unittest.TestCase):
    """`span_all` is the conjunctive bit `phrase_*` cannot express.

    Ordered-bigram overlap gives the same value for "3 of 4 spans matched" and
    "all 4"; `span_all` separates them, and that separation is what took
    `public_0092` from 284 candidates to 2. See CLAUDE.md.
    """

    def _ctx(self, catalog, spans):
        return ScoringContext(
            catalog=catalog, constraints=Constraints(),
            profile=ShopperProfile.parse(None), fused={}, per_field={}, dense={},
            query_terms=set(), query_bigrams=set(), category_terms=set(),
            constraint_spans=spans,
        )

    def test_vector_length_matches_feature_names(self):
        catalog, product = _tiny_catalog()
        vector = extract(product, self._ctx(catalog, ("cotton blend",)))
        self.assertEqual(len(vector), len(FEATURE_NAMES))

    def test_all_spans_matched_sets_span_all(self):
        catalog, product = _tiny_catalog()
        ctx = self._ctx(catalog, ("cotton blend", "button closure"))
        f = dict(zip(FEATURE_NAMES, extract(product, ctx)))
        self.assertEqual(f["span_coverage"], 1.0)
        self.assertEqual(f["span_all"], 1.0)

    def test_partial_match_clears_span_all_but_not_coverage(self):
        catalog, product = _tiny_catalog()
        ctx = self._ctx(catalog, ("cotton blend", "not in this product at all"))
        f = dict(zip(FEATURE_NAMES, extract(product, ctx)))
        self.assertEqual(f["span_coverage"], 0.5)
        self.assertEqual(f["span_all"], 0.0, "3-of-4 must not read as all-matched")

    def test_no_spans_is_inert(self):
        catalog, product = _tiny_catalog()
        f = dict(zip(FEATURE_NAMES, extract(product, self._ctx(catalog, ()))))
        self.assertEqual(f["span_coverage"], 0.0)
        self.assertEqual(f["span_all"], 0.0)


def _tiny_catalog():
    row = {
        "parent_asin": "SPANTEST1", "title": "Test Pajama Set",
        "features": ["cotton blend", "Button closure"],
        "details": {"Department": "womens"},
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Sleep & Lounge", "Sets"],
        "description": [], "price": 20.0, "average_rating": 4.0, "rating_number": 10,
        "store": "TestStore",
    }
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.jsonl"
        path.write_text(json.dumps(row) + chr(10), encoding="utf-8")
        catalog = Catalog(str(path))
    return catalog, catalog.by_asin["SPANTEST1"]


class RecommendationGateTests(unittest.TestCase):
    """The NQC recommendation gate (`DialogueConfig.min_recommend_confidence`).

    The evaluator breaks on first hit, so an early list locks in whatever rank
    it has. These pin the two things that must not drift: `nqc` has one
    definition shared by the ask gate and the recommendation gate, and the
    gate's own arithmetic -- including that 0.0 is a true off-switch, which is
    what makes rollback a config value rather than a code change.
    """

    def test_confidence_delegates_to_module_level_nqc(self):
        # A second copy of this formula is the failure mode CLAUDE.md already
        # records once (offline_eval's stale unknown-penalty loop).
        scores = [0.9, 0.4, 0.35, 0.3, 0.1]
        policy = ClarificationPolicy(Config().dialogue)
        self.assertEqual(policy._confidence(scores), nqc(scores))

    def test_nqc_edge_cases(self):
        self.assertEqual(nqc([]), 1.0, "no pool cannot be uncommitted")
        self.assertEqual(nqc([0.5]), 1.0, "a single candidate is fully separated")
        self.assertEqual(nqc([0.0, 0.0]), 0.0, "a non-positive top score cannot normalise")
        self.assertEqual(nqc([1.0, 1.0, 1.0]), 0.0, "bunched scores mean no commitment")

    def test_nqc_reads_only_the_top_ten(self):
        # The live gate passes the whole ranked pool; the replay in
        # tools/offline_eval passes the same. Both must ignore the tail.
        head = [1.0] + [0.5] * 9
        self.assertEqual(nqc(head), nqc(head + [0.01] * 500))

    def test_gate_arithmetic_matches_the_replay(self):
        from tools.offline_eval import recommendations_withheld

        config = Config()
        config.dialogue.min_recommend_confidence = 0.054
        config.dialogue.recommend_turn_fallback = 3
        bunched = [1.0] + [0.999] * 9      # nqc ~ 0, well under the threshold
        spread = [1.0] + [0.2] * 9         # nqc well over it

        self.assertTrue(recommendations_withheld(bunched, 1, config))
        self.assertFalse(recommendations_withheld(spread, 1, config),
                         "a committed ranker must not be held back")
        self.assertFalse(recommendations_withheld(bunched, 3, config),
                         "the fallback turn always shows, however uncommitted")

    def test_zero_threshold_disables_the_gate(self):
        from tools.offline_eval import recommendations_withheld

        config = Config()   # min_recommend_confidence defaults to 0.0
        self.assertFalse(recommendations_withheld([1.0] + [0.999] * 9, 1, config))
        self.assertFalse(recommendations_withheld([1.0] + [0.999] * 9, 1, None))


class DisclosedAskDecayTests(unittest.TestCase):
    """`DialogueConfig.disclosed_ask_decay` -- damping an attribute the customer
    already answered by disclosure rather than in reply to an ask.

    Measured at -0.0002 on the public 200 and NOT adopted (see CLAUDE.md), so
    what these pin is that the default is a true no-op and that the lever
    still does what it says if anyone turns it on.
    """

    class _FakeConstraints:
        def __init__(self, filled):
            self._filled = filled

        def filled_slots(self):
            return list(self._filled)

    class _FakeState:
        def __init__(self, filled):
            self.constraints = DisclosedAskDecayTests._FakeConstraints(filled)
            self.exhausted_attributes = set()
            self.asked_attributes = []

    def _gain(self, attribute, filled, decay):
        config = Config().dialogue
        config.disclosed_ask_decay = decay
        # Only this attribute is askable, so _best_attribute must return it.
        # Every other prior is zeroed explicitly -- an absent key falls back to
        # 0.2 inside _best_attribute, which would let a rival attribute win.
        config.attribute_prior = {name: 0.0 for name in ALLOWED_ATTRIBUTES}
        config.attribute_prior[attribute] = 1.0
        config.attribute_prior_floor = 0.0
        policy = ClarificationPolicy(config)
        _, product = _tiny_catalog()
        state = self._FakeState(filled)
        return policy._best_attribute(state, [product])

    def test_default_is_a_no_op(self):
        filled = self._gain("material", {"materials"}, 1.0)
        empty = self._gain("material", set(), 1.0)
        self.assertEqual(filled, empty,
                         "1.0 must leave an already-answered attribute untouched")

    def test_decay_damps_an_already_answered_attribute(self):
        attribute, full = self._gain("material", set(), 0.25)
        _, damped = self._gain("material", {"materials"}, 0.25)
        self.assertEqual(attribute, "material")
        self.assertAlmostEqual(damped, full * 0.25)

    def test_zero_is_a_hard_skip(self):
        self.assertEqual(self._gain("material", {"materials"}, 0.0), (None, 0.0))

    def test_slotless_attributes_are_never_considered_answered(self):
        # `feature`/`style`/`other` are the evaluator's catch-alls for spans
        # this system never parses into a structured slot, so "already
        # answered" is unobservable for them -- they must stay askable.
        _, full = self._gain("feature", set(), 0.0)
        _, still = self._gain("feature", {"materials", "colors", "categories"}, 0.0)
        self.assertEqual(full, still)


if __name__ == "__main__":
    unittest.main()

"""Thin HTTP wrapper around the real agent, for the hosted demo frontend.

Per PRD_demo_frontend.md: this is a presentation-layer service only. It does
not modify `shopping_copilot/`, `starter/agent.py`, or `evaluator/` -- it
imports the real, unmodified submission entry point (`starter.agent.Agent`,
which is exactly what the competition harness constructs) and the real,
unmodified customer-simulation logic from `evaluator.local_evaluator` (the
same functions the CLI evaluator itself uses), and exposes them over HTTP.

No agent/ranking/evaluator logic is reimplemented here -- `/simulate` calls
straight into `evaluator.local_evaluator`'s own `initial_message` /
`customer_reply` / `materialize_hidden_fields`, so this demo can never drift
from what the real evaluator does. The only thing added here is catalog
lookups to turn a bare `parent_asin` into something a UI can render (title,
price, rating, store) -- the agent's `respond()` contract returns
identifiers, not display data.
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from evaluator.local_evaluator import (  # noqa: E402
    MAX_TURNS,
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
    normalize_recommendations,
)
from starter.agent import Agent  # noqa: E402

CATALOG_PATH = Path(os.environ.get("CATALOG_PATH", str(REPO_ROOT / "data" / "catalog.jsonl")))
PUBLIC_SET_PATH = REPO_ROOT / "data" / "public_set.jsonl"
TURN_LIMIT = 10


def _ensure_catalog() -> None:
    """Download the catalog from bucket storage if it isn't on disk.

    The catalog is frozen/gitignored per submission rules (see root
    CLAUDE.md), so it never ships in the repo -- it has to reach the
    deployed container some other way. This pulls it from an S3-compatible
    bucket (Railway Bucket, or anything else that speaks the same API) on
    first boot, then leaves it in place for subsequent restarts.
    """
    if CATALOG_PATH.is_file():
        return

    bucket = os.environ.get("CATALOG_BUCKET_NAME")
    endpoint = os.environ.get("CATALOG_BUCKET_ENDPOINT")
    access_key = os.environ.get("CATALOG_BUCKET_ACCESS_KEY_ID")
    secret_key = os.environ.get("CATALOG_BUCKET_SECRET_ACCESS_KEY")
    if not all([bucket, endpoint, access_key, secret_key]):
        raise RuntimeError(
            f"{CATALOG_PATH} is missing and CATALOG_BUCKET_* env vars are not "
            "fully set -- cannot fetch the catalog."
        )
    object_key = os.environ.get("CATALOG_BUCKET_KEY", "catalog.jsonl")
    region = os.environ.get("CATALOG_BUCKET_REGION", "auto")

    import boto3  # noqa: PLC0415 -- only needed on the cold-start download path

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CATALOG_PATH.with_suffix(".jsonl.partial")
    client.download_file(bucket, object_key, str(tmp_path))
    tmp_path.rename(CATALOG_PATH)


_ensure_catalog()

app = FastAPI(title="Buyte Demo API")

_frontend_origin = os.environ.get("FRONTEND_ORIGIN", "")
_allow_origins = [o.strip() for o in _frontend_origin.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One shared Agent instance: loading the 50k-row catalog + indexes takes a
# few seconds, so it happens once at process startup, not per request. The
# agent's own `sessions` dict keys conversation state by session_id, which is
# what makes sharing one instance across requests safe for this demo's
# request volume. `_lock` serialises turns so two concurrent demo users can't
# interleave writes into the same Agent's mutable session state.
_agent = Agent(catalog_path=str(CATALOG_PATH))
_lock = Lock()

# Raw catalog rows + category lists, matching what evaluator.local_evaluator
# itself builds via catalog_index() -- needed by coarse_category() and
# materialize_hidden_fields() below. This is a second in-memory copy of the
# catalog alongside the Agent's own parsed Catalog; fine at this dataset size
# (50k rows) for a demo service.
_catalog_ids, _categories, _products = catalog_index(str(CATALOG_PATH))

_demo_samples: list[dict[str, Any]] = load_jsonl(PUBLIC_SET_PATH) if PUBLIC_SET_PATH.is_file() else []


class ResetRequest(BaseModel):
    session_id: str
    user_profile: dict[str, Any] = {}


class RespondRequest(BaseModel):
    session_id: str
    user_message: str
    turn: int
    top_k: int = 10


def _enrich(parent_asin: str) -> dict[str, Any]:
    product = _agent.catalog.get(parent_asin)
    if product is None:
        return {
            "parent_asin": parent_asin,
            "title": parent_asin,
            "store": None,
            "average_rating": None,
            "rating_number": None,
            "price": None,
            "category": None,
        }
    return {
        "parent_asin": product.parent_asin,
        "title": product.title,
        "store": product.store or None,
        "average_rating": product.average_rating,
        "rating_number": product.rating_number,
        "price": product.price,
        "category": product.category_path[-1] if product.category_path else None,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "catalog_size": len(_agent.catalog)}


@app.get("/demo-profile")
def demo_profile(dev: bool = False) -> dict[str, Any]:
    if not _demo_samples:
        raise HTTPException(status_code=503, detail="No public_set.jsonl available on this server.")
    picked = random.choice(_demo_samples)
    if not dev:
        return {"sample_id": picked["sample_id"], "user_profile": picked["user_profile"]}
    ground_truth = picked.get("ground_truth")
    return {
        "sample_id": picked["sample_id"],
        "user_profile": picked["user_profile"],
        "ground_truth": _enrich(ground_truth["parent_asin"]) if ground_truth else None,
    }


def _run_simulation(sample: dict[str, Any]) -> dict[str, Any]:
    """Replay one public-set sample exactly the way the CLI evaluator does.

    Drives the real Agent against evaluator.local_evaluator's real customer
    simulator (initial_message / customer_reply / the intent_override
    behaviour), turn by turn, and returns the full transcript plus the
    hit/miss verdict -- the same mechanics as evaluate()'s per-sample loop
    and tools/demo.py's run_session(), just captured as data instead of
    printed or scored in aggregate.
    """
    target = str(sample["ground_truth"]["parent_asin"])
    card, behaviour = materialize_hidden_fields(sample, _products)
    effective = {**sample, "intent_card": card, "behavior": behaviour}

    session_id = f"sim_{uuid.uuid4().hex}"
    with _lock:
        _agent.reset(session_id, sample["user_profile"])

    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    message = initial_message(effective, coarse_category(_categories.get(target, [])), disclosed)

    turns: list[dict[str, Any]] = []
    hit_turn: int | None = None
    best_rank: int | None = None

    for turn in range(1, MAX_TURNS + 1):
        with _lock:
            response = _agent.respond(session_id, message, turn, TOP_K)
        ranked = normalize_recommendations(response.get("recommendations"), _catalog_ids)
        rank = ranked.index(target) + 1 if target in ranked else None

        turns.append(
            {
                "turn": turn,
                "customer_message": message,
                "agent_message": response.get("message", ""),
                "ask_attribute": response.get("ask_attribute"),
                "recommendations": [_enrich(asin) for asin in ranked],
                "target_rank": rank if override_applied else None,
            }
        )

        if rank is not None and override_applied:
            hit_turn = turn
            best_rank = rank
            break
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

    return {
        "sample_id": sample["sample_id"],
        "scenario_type": sample["scenario_type"],
        "difficulty_bucket": sample.get("difficulty_bucket"),
        "user_profile_summary": sample.get("user_profile", {}).get("summary", ""),
        "target": _enrich(target),
        "hit": hit_turn is not None,
        "first_hit_turn": hit_turn,
        "best_rank": best_rank,
        "reciprocal_rank": 0.0 if best_rank is None else round(1.0 / best_rank, 6),
        "turns": turns,
    }


@app.post("/simulate")
def simulate(sample_id: str | None = None) -> dict[str, Any]:
    if not _demo_samples:
        raise HTTPException(status_code=503, detail="No public_set.jsonl available on this server.")
    if sample_id:
        matches = [s for s in _demo_samples if s.get("sample_id") == sample_id]
        if not matches:
            raise HTTPException(status_code=404, detail=f"No sample with sample_id={sample_id!r}")
        sample = matches[0]
    else:
        sample = random.choice(_demo_samples)
    return _run_simulation(sample)


@app.post("/reset")
def reset(body: ResetRequest) -> dict[str, Any]:
    with _lock:
        _agent.reset(body.session_id, body.user_profile)
    return {"ok": True}


@app.post("/respond")
def respond_endpoint(body: RespondRequest) -> dict[str, Any]:
    if not (1 <= body.turn <= TURN_LIMIT):
        raise HTTPException(status_code=400, detail=f"turn must be between 1 and {TURN_LIMIT}")
    with _lock:
        result = _agent.respond(body.session_id, body.user_message, body.turn, body.top_k)
    return {
        "message": result["message"],
        "ask_attribute": result["ask_attribute"],
        "recommendations": [_enrich(r["parent_asin"]) for r in result["recommendations"]],
    }

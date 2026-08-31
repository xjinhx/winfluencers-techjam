"""Thin HTTP wrapper around the real agent, for the hosted demo frontend.

Per PRD_demo_frontend.md: this is a presentation-layer service only. It does
not modify `shopping_copilot/` or `starter/agent.py` -- it imports the real,
unmodified submission entry point (`starter.agent.Agent`, which is exactly
what the competition harness constructs) and exposes two endpoints over it.

No agent/ranking/evaluator code lives in this file. The only thing added here
is catalog lookups to turn a bare `parent_asin` into something a UI can render
(title, price, rating, store) -- the agent's `respond()` contract returns
identifiers, not display data.
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path
from threading import Lock
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

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

app = FastAPI(title="Shopping Copilot Demo API")

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


def _load_demo_profiles() -> list[dict[str, Any]]:
    if not PUBLIC_SET_PATH.is_file():
        return []
    import json

    profiles = []
    with PUBLIC_SET_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            profile = row.get("user_profile")
            if profile:
                profiles.append({"sample_id": row.get("sample_id", ""), "user_profile": profile})
    return profiles


_demo_profiles = _load_demo_profiles()


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
def demo_profile() -> dict[str, Any]:
    if not _demo_profiles:
        raise HTTPException(status_code=503, detail="No public_set.jsonl available on this server.")
    return random.choice(_demo_profiles)


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

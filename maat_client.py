"""Thin Maat HTTP client for the CrewAI Flows demo.

No imports from maat-api — talks to the public (or local) conductor over HTTP.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

PLAN_ID = "crewai_order_flow_v1"
HANDOFF_PRICING_TO_INVOICE = "pricing_to_invoice"

DEFAULT_MAAT_URL = "https://api.synwe.ai"
MOCK_PATH = Path(__file__).resolve().parent / "mock_response.json"

# Contract: list €1,200; authorized discount 10% → expected bill €1,080.
# Defective pricing applies 40% → €720.
LIST_TOTAL_EUR = 1200.0
AUTHORIZED_DISCOUNT_PCT = 10.0
DEFECTIVE_DISCOUNT_PCT = 40.0


def maat_url() -> str:
    return os.environ.get("MAAT_URL", DEFAULT_MAAT_URL).rstrip("/")


def maat_key() -> str:
    return os.environ.get("MAAT_KEY", "").strip()


def _headers(key: str | None = None) -> dict[str, str]:
    token = (key if key is not None else maat_key()) or "dev-token"
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def build_demo_plan() -> dict[str, Any]:
    """Minimal plan: Intake → Pricing → Invoice with a commercial discount cap."""
    hid = HANDOFF_PRICING_TO_INVOICE
    required = [
        "order_id",
        "customer",
        "list_total_eur",
        "discount_pct",
        "discounted_total_eur",
    ]
    pricing_to_invoice = {
        "id": hid,
        "artifact": "pricing_quote",
        "source": "pricing",
        "target": "invoice",
        "channel": "api",
        "required_fields": required,
        "autonomy": "A",
    }
    intake_to_pricing = {
        "id": "intake_to_pricing",
        "artifact": "order_intake",
        "source": "intake",
        "target": "pricing",
        "channel": "api",
        "required_fields": ["order_id", "customer", "list_total_eur"],
        "autonomy": "A",
    }

    def node(nid: str, title: str, owner: str, inputs: list, outputs: list) -> dict:
        return {
            "id": nid,
            "title": title,
            "owner": owner,
            "level": "operations",
            "goal": {
                "statement": f"{title} stage",
                "target_indicators": [{"name": "done", "check": "handoff delivered"}],
            },
            "resources": [{"name": "time", "quantity": "1m"}],
            "forecast": [{"description": "handoff defect", "monitor": "validate rejected"}],
            "plan": {"how": "deterministic", "with_what": "crewai-flow"},
            "inputs": inputs,
            "outputs": outputs,
        }

    return {
        "id": PLAN_ID,
        "goal": {
            "statement": "Issue an invoice that respects the authorized commercial discount",
            "target_indicators": [
                {"name": "invoice_total", "check": "within authorized discount"}
            ],
        },
        "nodes": [
            node("intake", "Intake", "intake_agent", [], [intake_to_pricing]),
            node(
                "pricing",
                "Pricing",
                "pricing_agent",
                [intake_to_pricing],
                [pricing_to_invoice],
            ),
            node("invoice", "Invoice", "invoice_agent", [pricing_to_invoice], []),
        ],
        "handoffs": [intake_to_pricing, pricing_to_invoice],
        "anchor": {
            "anchor_id": "crewai_order_contract_v1",
            "workflow_goal": "Bill orders within authorized discount",
            "source_data": {
                "commercial_terms": {
                    "discount_authorization_pct": AUTHORIZED_DISCOUNT_PCT,
                    "currency": "EUR",
                    "list_total_eur": LIST_TOTAL_EUR,
                }
            },
            "immutable": True,
        },
    }


def register_plan(*, url: str | None = None, key: str | None = None) -> dict[str, Any]:
    """POST /v1/plans/register — required once before --live validates."""
    base = (url or maat_url()).rstrip("/")
    plan = build_demo_plan()
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{base}/v1/plans/register",
            headers=_headers(key),
            json=plan,
        )
        resp.raise_for_status()
        return resp.json()


def validate_handoff(
    produced: dict[str, Any],
    *,
    plan_id: str = PLAN_ID,
    handoff_id: str = HANDOFF_PRICING_TO_INVOICE,
    url: str | None = None,
    key: str | None = None,
    risk: str = "high",
    depth: str = "standard",
) -> dict[str, Any]:
    """POST /v1/handoffs/validate — returns accepted + findings."""
    base = (url or maat_url()).rstrip("/")
    body = {
        "plan_id": plan_id,
        "handoff_id": handoff_id,
        "produced": produced,
        "risk": risk,
        "depth": depth,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{base}/v1/handoffs/validate",
            headers=_headers(key),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


def load_mock_response() -> dict[str, Any]:
    if not MOCK_PATH.exists():
        raise FileNotFoundError(
            f"Missing {MOCK_PATH.name}. Run with --live against a conductor first "
            "to capture a real response."
        )
    return json.loads(MOCK_PATH.read_text(encoding="utf-8"))


def save_mock_response(verdict: dict[str, Any]) -> None:
    MOCK_PATH.write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

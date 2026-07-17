"""Deliberately broken CrewAI Flow for the structural analyzer demo.

Wiring defects (no field annotations needed to see these):
  1. ``compliance_audit`` @listen("ghost_emitter") — dangling edge to a
     step that does not exist, and the audit step is unreachable from @start.
  2. ``abandoned_report`` is a second @start with no listeners — a live
     orphan branch that nothing consumes.

Field-level unmet requirements need ``--fields``; business rules need a
contract (see CONFIGURATION.md).
"""
from __future__ import annotations

from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel


class BrokenState(BaseModel):
    note: str = ""


class BrokenOrderFlow(Flow[BrokenState]):
    """Intake → Pricing → Invoice, plus two broken branches."""

    @start()
    def intake(self) -> dict:
        return {"order_id": "ORD-BROKEN"}

    @listen(intake)
    def pricing(self, order: dict) -> dict:
        return {**order, "price": 100}

    @listen(pricing)
    def invoice(self, quote: dict) -> dict:
        return {"invoice_id": "INV-1", "total": quote.get("price")}

    # Dangling listen: trigger method "ghost_emitter" does not exist.
    @listen("ghost_emitter")
    def compliance_audit(self, payload: dict) -> dict:
        return {"ok": False}

    # Orphan start: reachable as a start, but nothing listens to it.
    @start()
    def abandoned_report(self) -> dict:
        return {"report": "never consumed"}

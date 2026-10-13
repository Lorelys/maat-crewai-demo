"""CrewAI Flow demo: Intake → Pricing → Invoice with Maat at the handoff.

The pricing step applies a 40% discount where the contract authorizes 10%.
List total €1,200 → bills €720 instead of €1,080. Arithmetic is correct;
the discount premise is wrong — a business-rule violation that looks valid.

Modes:
  --mock     (default) replay a recorded real Maat verdict (no API key)
  --live     call MAAT_URL /v1/handoffs/validate (needs MAAT_KEY)
  --no-maat  ungoverned path: cheerful success at €720
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Literal

# Keep CrewAI from writing under ~/Library or phoning home during the demo.
os.environ.setdefault("CREWAI_STORAGE_DIR", "crewai_order_flow_demo")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING", "false")

from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel, Field

import maat_client as maat

Mode = Literal["mock", "live", "no-maat"]


class OrderState(BaseModel):
    order_id: str = "ORD-1001"
    customer: str = "Acme GmbH"
    list_total_eur: float = maat.LIST_TOTAL_EUR
    discount_pct: float = 0.0
    discounted_total_eur: float = 0.0
    invoice_total_eur: float = 0.0
    maat_accepted: bool | None = None
    maat_findings: list[dict[str, Any]] = Field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""


class OrderFlow(Flow[OrderState]):
    """Three explicit handoff boundaries via @start / @listen."""

    mode: Mode = "mock"
    capture_mock: bool = False

    @start()
    def intake(self) -> dict[str, Any]:
        print("─" * 60)
        print("INTAKE agent")
        print(f"  order_id   = {self.state.order_id}")
        print(f"  customer   = {self.state.customer}")
        print(f"  list_total = €{self.state.list_total_eur:,.2f}")
        payload = {
            "order_id": self.state.order_id,
            "customer": self.state.customer,
            "list_total_eur": self.state.list_total_eur,
        }
        print("  → handoff intake_to_pricing")
        return payload

    @listen(intake)
    def pricing(self, order: dict[str, Any]) -> dict[str, Any] | None:
        print("─" * 60)
        print("PRICING agent")
        # Defect: applies 40% where contract authorizes 10%.
        discount_pct = maat.DEFECTIVE_DISCOUNT_PCT
        list_total = float(order["list_total_eur"])
        discounted = round(list_total * (1.0 - discount_pct / 100.0), 2)

        self.state.discount_pct = discount_pct
        self.state.discounted_total_eur = discounted

        quote = {
            "order_id": order["order_id"],
            "customer": order["customer"],
            "list_total_eur": list_total,
            "discount_pct": discount_pct,
            "discounted_total_eur": discounted,
        }
        print(f"  list_total       = €{list_total:,.2f}")
        print(f"  discount_pct     = {discount_pct:.0f}%   ← premise (should be 10%)")
        print(f"  discounted_total = €{discounted:,.2f}")
        print("  → handoff pricing_to_invoice")

        if self.mode == "no-maat":
            print("  [no-maat] skipping Maat — handoff ungoverned")
            return quote

        verdict = self._validate_with_maat(quote)
        if self.capture_mock:
            maat.save_mock_response(verdict)
            print(f"  wrote {maat.MOCK_PATH.name}")

        self.state.maat_accepted = verdict.get("accepted") is True
        self.state.maat_findings = list(verdict.get("findings") or [])

        if self.state.maat_accepted:
            print("  Maat: ACCEPTED")
            return quote

        self.state.halted = True
        self.state.halt_reason = "Maat rejected pricing_to_invoice"
        self._print_maat_halt(verdict)
        return None

    @listen(pricing)
    def invoice(self, quote: dict[str, Any] | None) -> dict[str, Any] | None:
        print("─" * 60)
        if quote is None or self.state.halted:
            print("INVOICE agent — SKIPPED (flow halted upstream)")
            return None

        total = float(quote["discounted_total_eur"])
        self.state.invoice_total_eur = total
        invoice = {
            "order_id": quote["order_id"],
            "customer": quote["customer"],
            "invoice_total_eur": total,
            "currency": "EUR",
            "status": "issued",
        }
        print("INVOICE agent")
        print(f"  issuing invoice for €{total:,.2f}")
        if self.mode == "no-maat":
            print()
            print("✓ SUCCESS — invoice issued for €720.00")
            print("  (ungoverned run: the unauthorized 40% discount was invisible)")
        else:
            print(f"✓ Invoice issued for €{total:,.2f}")
        return invoice

    def _validate_with_maat(self, produced: dict[str, Any]) -> dict[str, Any]:
        if self.mode == "mock":
            print("  [mock] using recorded Maat response")
            return maat.load_mock_response()
        print(f"  [live] POST {maat.maat_url()}/v1/handoffs/validate")
        return maat.validate_handoff(produced)

    def _print_maat_halt(self, verdict: dict[str, Any]) -> None:
        print()
        print("✗ Maat HALTED the handoff pricing → invoice")
        findings = verdict.get("findings") or []
        if not findings:
            print("  (no findings payload)")
            return
        for f in findings:
            print("  finding:")
            print(f"    code      = {f.get('code')}")
            print(f"    severity  = {f.get('severity')}")
            print(f"    owner     = {f.get('owner')}   ← which agent")
            print(f"    handoff   = {f.get('handoff_id')}")
            print(f"    message   = {f.get('message')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CrewAI Flow + Maat handoff demo")
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--mock",
        action="store_true",
        help="Replay recorded Maat response (default; no API key)",
    )
    g.add_argument(
        "--live",
        action="store_true",
        help="Call real Maat API (MAAT_URL + MAAT_KEY)",
    )
    g.add_argument(
        "--no-maat",
        action="store_true",
        help="Ungoverned run — bills €720 with a cheerful success",
    )
    p.add_argument(
        "--register",
        action="store_true",
        help="Register the demo plan before running (use with --live)",
    )
    p.add_argument(
        "--capture-mock",
        action="store_true",
        help="After a --live call, write mock_response.json from the real verdict",
    )
    return p.parse_args(argv)


def resolve_mode(args: argparse.Namespace) -> Mode:
    if args.no_maat:
        return "no-maat"
    if args.live:
        return "live"
    return "mock"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mode = resolve_mode(args)

    if args.capture_mock and mode != "live":
        print("--capture-mock requires --live", file=sys.stderr)
        return 2

    if mode == "live":
        remote = "localhost" not in maat.maat_url() and "127.0.0.1" not in maat.maat_url()
        if remote and not maat.maat_key():
            print(
                "MAAT_KEY is required for --live against a remote API.\n"
                "Request access: hello@synwe.ai",
                file=sys.stderr,
            )
            return 2
        if args.register:
            print(f"Registering plan {maat.PLAN_ID} at {maat.maat_url()} …")
            verdict = maat.register_plan()
            n = len(verdict.get("findings") or [])
            print(f"  register accepted={verdict.get('accepted')} findings={n}")
            if not verdict.get("accepted", True):
                for f in verdict.get("findings") or []:
                    print(f"    - {f.get('code')}: {f.get('message')}")
                return 1

    print(f"Mode: {mode}")
    authorized = maat.LIST_TOTAL_EUR * (1 - maat.AUTHORIZED_DISCOUNT_PCT / 100)
    defective = maat.LIST_TOTAL_EUR * (1 - maat.DEFECTIVE_DISCOUNT_PCT / 100)
    print(f"Expected authorized bill: €{authorized:,.2f}")
    print(f"Defecting pricing will propose: €{defective:,.2f}")
    print()

    flow = OrderFlow()
    flow.mode = mode
    flow.capture_mock = bool(args.capture_mock)
    flow.kickoff()

    if mode == "no-maat":
        return 0
    return 1 if flow.state.halted else 0


if __name__ == "__main__":
    raise SystemExit(main())

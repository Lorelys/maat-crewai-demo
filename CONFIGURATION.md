# Configuring Maat for business-rule checks

## The honest split

**Structural checks** (default) need no contract. Maat — or the connector in
structural mode — can tell you that a required field is missing, that a value
is empty, or that a claim carries no provenance. Those failures are about
*shape and presence*.

**Business-rule checks** need your contract. A 40% discount on a €1,200 order
that should have been 10% is arithmetically fine and structurally complete. It
is still wrong. Maat cannot know that without your authorized limits. If you
drop the connector into a Flow and never configure a contract, you will get
structural findings only — not silent business-rule protection.

Translating a real contract into checkable rules is the configuration step.
That is where the value is created.

---

## What you must provide

### (a) The plan — agents, handoffs, produces / needs

A plan is the process graph Maat governs:

- **agents (nodes)** — who produces what (e.g. `intake`, `pricing`, `invoice`)
- **handoffs** — named seams between them (`pricing_to_invoice`)
- **needs / required_fields** — what the consumer must receive
- **produces / artifact** — what the producer claims to emit

Without a plan, the connector stays in **structural mode** (local checks,
no `MAAT_PLAN_ID`). With a plan registered on the conductor and
`MAAT_PLAN_ID` set, `validate()` calls `POST /v1/handoffs/validate`.

### (b) The anchor — authorized limits and rules

The anchor is frozen ground truth for the engagement: commercial terms,
coverage caps, tax treatment, approved scopes. Example from this demo:

```json
{
  "anchor_id": "crewai_order_contract_v1",
  "workflow_goal": "Bill orders within authorized discount",
  "source_data": {
    "commercial_terms": {
      "discount_authorization_pct": 10,
      "currency": "EUR",
      "list_total_eur": 1200
    }
  }
}
```

`discount_authorization_pct: 10` is the contractual ceiling. Pricing may still
emit `discount_pct: 40` with a clean payload — only the anchor makes that a
blocker.

### (c) Which rules apply at which handoff

Rules are bound to handoff ids. In the worked example:

| Handoff | Rule | Fires when |
|---------|------|------------|
| `pricing_to_invoice` | `REQ_DISCOUNT_EXCEEDED` | `discount_pct` in the produced quote exceeds `commercial_terms.discount_authorization_pct` on the anchor |

Structural codes (`INFO_INSUFFICIENT`, `INFO_EMPTY`, `INFO_UNRELIABLE`, …)
apply whenever the handoff contract defines required fields / provenance
expectations. Business codes (`REQ_*`) apply only where you have wired a rule
to that handoff **and** supplied the anchor values it compares against.

---

## Worked example (this repo’s demo)

1. Register the demo plan (agents + `pricing_to_invoice` handoff + anchor).
2. Pricing emits `{ discount_pct: 40, discounted_total_eur: 720, … }`.
3. Maat rejects with `REQ_DISCOUNT_EXCEEDED` — owner `pricing_agent`, field
   called out in the message, handoff `pricing_to_invoice`.

Run `python flow.py --mock` (or `--live` against a conductor that has the
plan) to see that catch. The same Flow without a contract would only see
structural findings (e.g. `INFO_UNRELIABLE`) and would **not** block the
unauthorized discount.

---

## Connector wiring

```python
from maat_crewai import validate

# Structural only (default): no MAAT_PLAN_ID
result = validate(
    handoff_id="pricing_to_invoice",
    produced=state.pricing_quote,
    required_fields=["order_id", "discount_pct", "discounted_total_eur"],
)

# Contract mode: plan registered on the conductor
# export MAAT_URL=...  MAAT_KEY=...  MAAT_PLAN_ID=crewai_order_flow_v1
result = validate(handoff_id="pricing_to_invoice", produced=state.pricing_quote)
if not result.accepted:
    print(result.report())
```

After every call, `validate()` prints a short summary: how many structural
findings, and — when no business-rule findings appeared — an explicit note
pointing here.

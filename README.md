# CrewAI × Maat

Three layers:

1. **Structural chain analyzer** (`analyze.py`) — free front door. Point it at
   a Flow, get wiring findings in seconds. No LLM, no agents executed, no API
   spend, no key required (local by default).
2. **Connector** (`maat_crewai/`) — drop `validate()` into a live Flow handoff.
3. **Demo** (`flow.py`) — see a business-rule catch with a pre-configured contract.

**Privacy:** the connector and analyzer send nothing anywhere except your
configured `MAAT_URL`, and only when you explicitly opt into remote/contract
mode (`MAAT_PLAN_ID` or `analyze.py --remote`). No telemetry, no phone-home.
If usage reporting is ever added, it will be opt-in and metadata-only
(finding codes and counts — never payload content).

---

## Structural analyzer — free, deterministic front door

```bash
python analyze.py --flow examples/broken_flow.py --hash
python analyze.py --flow examples/broken_flow.py --hash   # same SHA-256
```

Same input → byte-identical JSON report (prove it with `--hash` twice).

What unannotated analysis catches: **dangling edges**, **unreachable /
orphaned steps**. CrewAI Flows do not declare `produces` / `needs`, so
field-level unmet requirements need optional annotations:

```bash
python analyze.py --flow examples/broken_flow.py \
  --fields examples/fields_broken.json
```

The report always prints a **capability ladder**: wiring today → fields next →
contract for business rules ([CONFIGURATION.md](CONFIGURATION.md)).

| Flag | Meaning |
|------|---------|
| `--flow path.py` | Introspect a CrewAI Flow class (`@start` / `@listen`) |
| `--json graph.json` | Analyze a pre-built `{nodes, edges}` document |
| `--fields fields.json` | step → `{produces, needs}` for unmet-requirement checks |
| `--remote` | Also `POST {MAAT_URL}/v1/chain/analyze` (**auth required today**) |
| `--hash` | Print SHA-256 of the stable JSON (determinism demo) |

Default path is **local** (in-process analyzer). `--remote` needs `MAAT_KEY`
because `/v1/chain/analyze` is behind `require_token` — there is no public
key-free HTTP path yet.

---

## Connector — drop into any Flow

```python
from maat_crewai import validate

result = validate(
    handoff_id="pricing_to_invoice",
    produced=state.pricing_quote,
    required_fields=["order_id", "discount_pct", "discounted_total_eur"],
)
if not result.accepted:
    print(result.report())
```

| Mode | When | What it catches |
|------|------|-----------------|
| **Structural** (default) | No `MAAT_PLAN_ID` | Missing/empty fields, empty payload, unverifiable claims |
| **Contract** | `MAAT_PLAN_ID` + key | Structural **plus** business rules on the anchor |

Without a contract, Maat cannot catch a discount over authorization or a
payout over a cap. Details: [CONFIGURATION.md](CONFIGURATION.md).

---

## Demo — business-rule catch (pre-configured contract)

Intake → Pricing → Invoice. Pricing applies **40%** where the contract
authorizes **10%** (€1,200 → €720 instead of €1,080). Arithmetic is correct;
the premise is wrong.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python flow.py --mock      # recorded real Maat reject
python flow.py --no-maat   # cheerful €720 success (failure invisible)
```

Access: **hello@synwe.ai** · API: `https://api.synwe.ai`

---

## Layout

| Path | Role |
|------|------|
| `analyze.py` | Structural chain analyzer CLI |
| `examples/broken_flow.py` | Deliberately broken Flow (dangling + orphan) |
| `maat_crewai/` | `validate`, extractor, local chain analyzer |
| `CONFIGURATION.md` | Plan + anchor + rules for business checks |
| `flow.py` | Discount-violation demo |

This directory does not import maat-api.

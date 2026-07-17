"""Maat connector for CrewAI Flows — structural checks first, contracts for business rules.

Public API
----------
    from maat_crewai import validate

    result = validate(handoff_id="pricing_to_invoice", produced=state.pricing_quote)
    if not result.accepted:
        print(result.report())
        # halt / escalate

Env
---
    MAAT_URL   conductor base URL (default https://api.synwe.ai)
    MAAT_KEY   bearer token
    MAAT_PLAN_ID  optional — when set, validate() calls the live conductor
                  with this plan (structural + any configured business rules)

No telemetry. The only network destination is MAAT_URL, and only when a plan
id is configured (live / contract mode). Structural mode sends nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

DEFAULT_MAAT_URL = "https://api.synwe.ai"

# Codes treated as structural (shape / presence / provenance), not business rules.
_STRUCTURAL_PREFIXES = ("INFO_", "PLAN_", "HANDOFF_")

_CONTRACT_NOTE = (
    "To catch business-rule violations (a discount exceeding authorization, "
    "a payout over a contractual cap, a wrong tax treatment), Maat needs your "
    "contract configured. See CONFIGURATION.md."
)

_PROVENANCE_KEYS = frozenset({
    "provenance",
    "evidence",
    "source_ref",
    "anchor_ref",
    "confidence",
})


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    owner: str | None = None
    handoff_id: str | None = None
    node_id: str | None = None
    defect_type: str | None = None
    requires_human_review: bool = False

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Finding:
        return cls(
            code=str(raw.get("code") or "UNKNOWN"),
            severity=str(raw.get("severity") or "warning"),
            message=str(raw.get("message") or ""),
            owner=raw.get("owner"),
            handoff_id=raw.get("handoff_id"),
            node_id=raw.get("node_id"),
            defect_type=raw.get("defect_type"),
            requires_human_review=bool(raw.get("requires_human_review") or False),
        )


@dataclass
class ValidationResult:
    """Outcome of validate(). ``mode`` is ``structural`` or ``contract``."""

    accepted: bool
    findings: list[Finding] = field(default_factory=list)
    handoff_id: str = ""
    mode: Literal["structural", "contract"] = "structural"
    depth: str | None = None
    plan_id: str | None = None

    @property
    def structural_findings(self) -> list[Finding]:
        return [f for f in self.findings if _is_structural(f.code)]

    @property
    def business_findings(self) -> list[Finding]:
        return [f for f in self.findings if not _is_structural(f.code)]

    def report(self) -> str:
        """Human-readable findings (code, severity, owner, message)."""
        if not self.findings:
            return "Maat: accepted (no findings)."
        lines = [
            f"Maat: {'ACCEPTED' if self.accepted else 'REJECTED'} "
            f"[{self.mode}] handoff={self.handoff_id!r}"
        ]
        for f in self.findings:
            lines.append(f"  [{f.severity}] {f.code}")
            if f.owner:
                lines.append(f"    owner   = {f.owner}")
            if f.handoff_id:
                lines.append(f"    handoff = {f.handoff_id}")
            lines.append(f"    message = {f.message}")
        return "\n".join(lines)

    def summary(self) -> str:
        """Structural count + honest note about what requires a contract."""
        n_struct = len(self.structural_findings)
        n_biz = len(self.business_findings)
        lines = [
            f"Maat found {n_struct} structural issue"
            f"{'' if n_struct == 1 else 's'}"
            + (f" and {n_biz} business-rule finding"
               f"{'' if n_biz == 1 else 's'}." if n_biz else "."),
        ]
        if n_biz == 0:
            lines.append(_CONTRACT_NOTE)
        return "\n".join(lines)


def _is_structural(code: str) -> bool:
    return any(code.startswith(p) for p in _STRUCTURAL_PREFIXES)


def _maat_url() -> str:
    return os.environ.get("MAAT_URL", DEFAULT_MAAT_URL).rstrip("/")


def _maat_key() -> str:
    return os.environ.get("MAAT_KEY", "").strip()


def _plan_id(explicit: str | None) -> str | None:
    if explicit is not None and explicit.strip():
        return explicit.strip()
    env = os.environ.get("MAAT_PLAN_ID", "").strip()
    return env or None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _has_provenance_signal(produced: dict[str, Any]) -> bool:
    for key, value in produced.items():
        key_l = str(key).lower()
        if key_l in _PROVENANCE_KEYS or key_l.endswith("_provenance"):
            if not _is_empty(value):
                return True
        if isinstance(value, dict) and _has_provenance_signal(value):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and _has_provenance_signal(item):
                    return True
    return False


def _structural_check(
    handoff_id: str,
    produced: Any,
    required_fields: list[str] | None,
) -> list[Finding]:
    """Local structural checks when no plan/anchor is configured.

    Mirrors Maat's INFO_* shape checks: missing required fields, empty values,
    empty payload, unverifiable claims. Does **not** evaluate business rules.
    """
    findings: list[Finding] = []

    if not isinstance(produced, dict):
        findings.append(Finding(
            code="INFO_INSUFFICIENT",
            severity="blocker",
            message=(
                f"Handoff '{handoff_id}' produced a non-object payload "
                f"({type(produced).__name__}); expected a JSON object."
            ),
            handoff_id=handoff_id,
            defect_type="insufficient",
        ))
        return findings

    if len(produced) == 0:
        findings.append(Finding(
            code="INFO_INSUFFICIENT",
            severity="blocker",
            message=f"Handoff '{handoff_id}' produced an empty payload.",
            handoff_id=handoff_id,
            defect_type="insufficient",
        ))
        return findings

    required = list(required_fields or [])
    if required:
        present = set(produced.keys())
        missing = sorted(set(required) - present)
        if missing:
            findings.append(Finding(
                code="INFO_INSUFFICIENT",
                severity="blocker",
                message=(
                    f"Required fields missing for handoff '{handoff_id}': {missing}"
                ),
                handoff_id=handoff_id,
                defect_type="insufficient",
            ))
        empty = sorted(
            f for f in (set(required) & present) if _is_empty(produced.get(f))
        )
        if empty:
            findings.append(Finding(
                code="INFO_EMPTY",
                severity="blocker",
                message=(
                    f"Required fields present but empty for handoff "
                    f"'{handoff_id}': {empty}"
                ),
                handoff_id=handoff_id,
                defect_type="insufficient",
            ))

    if not _has_provenance_signal(produced):
        findings.append(Finding(
            code="INFO_UNRELIABLE",
            severity="warning",
            message=(
                f"Handoff '{handoff_id}' carries no provenance/evidence; "
                f"cannot be verified."
            ),
            handoff_id=handoff_id,
            defect_type="unreliable",
        ))

    return findings


def _call_conductor(
    *,
    plan_id: str,
    handoff_id: str,
    produced: dict[str, Any],
    risk: str,
    depth: str | None,
) -> ValidationResult:
    url = _maat_url()
    key = _maat_key()
    if not key and "localhost" not in url and "127.0.0.1" not in url:
        raise RuntimeError(
            "MAAT_KEY is required when calling a remote Maat conductor. "
            "Request access: hello@synwe.ai"
        )
    token = key or "dev-token"
    body: dict[str, Any] = {
        "plan_id": plan_id,
        "handoff_id": handoff_id,
        "produced": produced,
        "risk": risk,
    }
    if depth:
        body["depth"] = depth

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{url}/v1/handoffs/validate",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        payload = resp.json()

    findings = [Finding.from_api(f) for f in (payload.get("findings") or [])]
    return ValidationResult(
        accepted=bool(payload.get("accepted")),
        findings=findings,
        handoff_id=handoff_id,
        mode="contract",
        depth=payload.get("depth"),
        plan_id=plan_id,
    )


def validate(
    handoff_id: str,
    produced: dict[str, Any] | Any,
    *,
    plan_id: str | None = None,
    required_fields: list[str] | None = None,
    risk: str = "normal",
    depth: str | None = None,
    print_summary: bool = True,
) -> ValidationResult:
    """Validate a CrewAI Flow handoff payload.

    **Structural mode (default)** — no ``plan_id`` / ``MAAT_PLAN_ID``:
    checks missing/empty fields and unverifiable claims locally. Sends no
    network traffic. Cannot catch business-rule violations.

    **Contract mode** — ``plan_id`` argument or ``MAAT_PLAN_ID`` set:
    POSTs to ``{MAAT_URL}/v1/handoffs/validate``. Structural findings still
    apply; business-rule findings appear only if the plan carries an anchor
    and rules for this handoff (see CONFIGURATION.md).

    Args:
        handoff_id: Stable id of the handoff seam (e.g. ``pricing_to_invoice``).
        produced: Payload the producing step emitted.
        plan_id: Optional plan id; overrides ``MAAT_PLAN_ID``.
        required_fields: Optional field names for structural presence checks
            when no plan is configured.
        risk: Declared risk passed to the conductor in contract mode.
        depth: Optional validation depth override (``light`` / ``standard`` /
            ``thorough``).
        print_summary: If True, print structural count + contract note.
    """
    resolved_plan = _plan_id(plan_id)

    if resolved_plan:
        if not isinstance(produced, dict):
            result = ValidationResult(
                accepted=False,
                findings=[Finding(
                    code="INFO_INSUFFICIENT",
                    severity="blocker",
                    message=(
                        f"Handoff '{handoff_id}' produced a non-object payload "
                        f"({type(produced).__name__})."
                    ),
                    handoff_id=handoff_id,
                )],
                handoff_id=handoff_id,
                mode="contract",
                plan_id=resolved_plan,
            )
        else:
            result = _call_conductor(
                plan_id=resolved_plan,
                handoff_id=handoff_id,
                produced=produced,
                risk=risk,
                depth=depth,
            )
    else:
        findings = _structural_check(handoff_id, produced, required_fields)
        blockers = [f for f in findings if f.severity == "blocker"]
        result = ValidationResult(
            accepted=len(blockers) == 0,
            findings=findings,
            handoff_id=handoff_id,
            mode="structural",
            plan_id=None,
        )

    if print_summary:
        print(result.summary())
    return result

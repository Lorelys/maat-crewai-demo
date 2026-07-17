"""Local structural chain analyzer (mirrors Maat ``POST /v1/chain/analyze``).

Runs entirely in-process — no LLM, no network, deterministic.
Also flags UNREACHABLE_STEP when start methods are known (Flow extraction).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ChainFinding:
    code: str
    severity: str  # blocker | warning
    process_id: str
    message: str
    reason: str | None = None
    field_name: str | None = None


def analyze_chain(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    start_methods: list[str] | None = None,
) -> list[ChainFinding]:
    findings: list[ChainFinding] = []
    by_id = {str(n["id"]): n for n in nodes}
    ids = set(by_id)

    valid_edges: list[tuple[str, str]] = []
    for e in edges:
        src, tgt = str(e["source"]), str(e["target"])
        if src not in ids or tgt not in ids:
            findings.append(ChainFinding(
                code="DANGLING_EDGE",
                severity="warning",
                process_id=src if src in ids else tgt,
                message=(
                    f"Edge {src} -> {tgt} references a process not in the chain."
                ),
            ))
        else:
            valid_edges.append((src, tgt))

    incoming: dict[str, list[str]] = {i: [] for i in ids}
    outgoing: dict[str, list[str]] = {i: [] for i in ids}
    for src, tgt in valid_edges:
        incoming[tgt].append(src)
        outgoing[src].append(tgt)

    # Demand: unmet requirements (only when needs are annotated)
    for nid, node in by_id.items():
        needs = [str(x) for x in (node.get("needs") or [])]
        if not needs:
            continue
        suppliers = incoming.get(nid, [])
        if not suppliers:
            findings.append(ChainFinding(
                code="NO_PRODUCER",
                severity="blocker",
                process_id=nid,
                message=(
                    f"'{nid}' requires {sorted(needs)} to go live, "
                    f"but has no incoming process supplying anything."
                ),
            ))
            continue
        supplied: set[str] = set()
        for s in suppliers:
            supplied |= {str(x) for x in (by_id[s].get("produces") or [])}
        unmet = sorted(set(needs) - supplied)
        if unmet:
            findings.append(ChainFinding(
                code="UNMET_REQUIREMENT",
                severity="blocker",
                process_id=nid,
                message=(
                    f"'{nid}' needs {unmet} to execute and produce its output, "
                    f"but upstream {suppliers} supplies {sorted(supplied)}. "
                    f"Cannot go live."
                ),
            ))

    # Supply: orphan outputs (only when produces + downstream needs annotated)
    for nid, node in by_id.items():
        produces = [str(x) for x in (node.get("produces") or [])]
        if not produces:
            continue
        consumers = outgoing.get(nid, [])
        if not consumers:
            continue
        demanded: set[str] = set()
        for c in consumers:
            demanded |= {str(x) for x in (by_id[c].get("needs") or [])}
        orphans = sorted(set(produces) - demanded)
        if orphans:
            findings.append(ChainFinding(
                code="ORPHAN_OUTPUT",
                severity="warning",
                process_id=nid,
                message=(
                    f"'{nid}' produces {orphans} that no downstream process "
                    f"requires — possible wiring waste or mis-map."
                ),
            ))

    # Reachability from start methods (Flow-aware)
    if start_methods:
        starts = [s for s in start_methods if s in ids]
        reachable: set[str] = set()
        stack = list(starts)
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(outgoing.get(cur, []))
        for nid in sorted(ids):
            if nid not in reachable:
                findings.append(ChainFinding(
                    code="UNREACHABLE_STEP",
                    severity="warning",
                    process_id=nid,
                    message=(
                        f"'{nid}' is not reachable from any @start method "
                        f"{sorted(starts)} — orphaned / dead branch."
                    ),
                ))
        # Orphan starts: @start with no listeners (nothing consumes them)
        for s in sorted(starts):
            if not outgoing.get(s):
                findings.append(ChainFinding(
                    code="ORPHAN_START",
                    severity="warning",
                    process_id=s,
                    message=(
                        f"@start '{s}' has no @listen consumers — "
                        f"orphaned branch that nothing uses."
                    ),
                ))

    # Deterministic order
    findings.sort(key=lambda f: (f.code, f.process_id, f.message))
    return findings


def findings_to_dicts(findings: list[ChainFinding]) -> list[dict[str, Any]]:
    return [asdict(f) for f in findings]


def has_field_annotations(nodes: list[dict[str, Any]]) -> bool:
    for n in nodes:
        if n.get("produces") or n.get("needs"):
            return True
    return False

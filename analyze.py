#!/usr/bin/env python3
"""Structural chain analyzer CLI — free, no-AI, deterministic front door.

    python analyze.py --flow examples/broken_flow.py
    python analyze.py --flow examples/broken_flow.py --fields examples/fields_broken.json
    python analyze.py --json graph.json

By default analysis runs locally (no API key, no network). Optional
``--remote`` POSTs to ``{MAAT_URL}/v1/chain/analyze`` (requires auth today).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure demo root is on path when run as a script.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from maat_crewai.chain_analyze import (  # noqa: E402
    analyze_chain,
    findings_to_dicts,
    has_field_annotations,
)
from maat_crewai.extractor import (  # noqa: E402
    extract_graph,
    graph_from_json,
    load_fields_json,
    load_flow_class,
)


def _stable_dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _print_ladder(*, wiring_findings: list[dict], fields_used: bool) -> None:
    print()
    print("═" * 64)
    print("CAPABILITY LADDER (honest)")
    print("═" * 64)
    print(
        "1. Unannotated Flow (this run's wiring layer):\n"
        "   Catches orphans, dangling edges, unreachable steps.\n"
        f"   This run: {len(wiring_findings)} wiring finding(s)."
    )
    if fields_used:
        print(
            "2. With --fields annotations (ACTIVE):\n"
            "   Also catches unmet requirements / orphan outputs / no-producer."
        )
    else:
        print(
            "2. With --fields step→{produces,needs} (not provided):\n"
            "   Would catch unmet requirements (consumer needs a field no\n"
            "   upstream producer emits), NO_PRODUCER, ORPHAN_OUTPUT."
        )
    print(
        "3. With a Maat contract (plan + anchor + rules):\n"
        "   Would catch business-rule violations — e.g. discount over\n"
        "   authorization, payout over cap. See CONFIGURATION.md.\n"
        "   Structure alone cannot see those."
    )
    print("═" * 64)


def _call_remote(nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    import httpx

    url = os.environ.get("MAAT_URL", "https://api.synwe.ai").rstrip("/")
    key = os.environ.get("MAAT_KEY", "").strip()
    if not key and "localhost" not in url and "127.0.0.1" not in url:
        raise SystemExit(
            "POST /v1/chain/analyze requires auth (Bearer MAAT_KEY).\n"
            "Omit --remote to analyze locally with no key, or set MAAT_KEY.\n"
            "See README auth note / ask SynWe about a public structural path."
        )
    token = key or "dev-token"
    # Strip extractor-only keys before POST
    payload_nodes = [
        {
            "id": n["id"],
            "produces": n.get("produces") or [],
            "needs": n.get("needs") or [],
        }
        for n in nodes
    ]
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{url}/v1/chain/analyze",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"nodes": payload_nodes, "edges": edges},
        )
        if resp.status_code == 401:
            raise SystemExit(
                "401 Unauthorized from /v1/chain/analyze — require_token rejected "
                "the Bearer token. Local analysis needs no key (default)."
            )
        resp.raise_for_status()
        return resp.json()


def build_report(graph: dict[str, Any], *, remote: bool) -> dict[str, Any]:
    nodes = graph["nodes"]
    edges = graph["edges"]
    starts = list(graph.get("start_methods") or [])

    local_findings = analyze_chain(nodes, edges, start_methods=starts or None)
    findings = findings_to_dicts(local_findings)
    source = "local"

    if remote:
        remote_body = _call_remote(nodes, edges)
        # Prefer remote findings for the Maat codes; keep local UNREACHABLE_STEP
        # (Flow-specific) that the HTTP API does not know about.
        remote_findings = list(remote_body.get("findings") or [])
        local_extra = [
            f for f in findings if f.get("code") == "UNREACHABLE_STEP"
        ]
        merged = remote_findings + local_extra
        merged.sort(
            key=lambda f: (
                str(f.get("code")),
                str(f.get("process_id")),
                str(f.get("message")),
            )
        )
        findings = merged
        source = "remote+local_reachability"

    wiring_codes = {"DANGLING_EDGE", "UNREACHABLE_STEP", "ORPHAN_START"}
    wiring_findings = [f for f in findings if f.get("code") in wiring_codes]

    accepted = not any(f.get("severity") == "blocker" for f in findings)
    report = {
        "analyzer": "maat-crewai-demo/analyze.py",
        "source": source,
        "flow_class": graph.get("flow_class"),
        "start_methods": starts,
        "graph": {
            "nodes": [
                {
                    "id": n["id"],
                    "produces": n.get("produces") or [],
                    "needs": n.get("needs") or [],
                }
                for n in nodes
            ],
            "edges": edges,
        },
        "accepted": accepted,
        "findings": findings,
        "meta": {
            "field_annotations": has_field_annotations(nodes),
            "wiring_finding_count": len(wiring_findings),
            "deterministic": True,
            "llm": False,
            "network": source.startswith("remote"),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Structural chain analyzer for CrewAI Flows (no LLM)"
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--flow", help="Path to a .py file defining a CrewAI Flow")
    src.add_argument("--json", dest="json_path", help="Pre-extracted {nodes, edges}")
    p.add_argument("--class", dest="class_name", default=None, help="Flow class name")
    p.add_argument(
        "--fields",
        default=None,
        help="Optional fields.json: step → {produces, needs}",
    )
    p.add_argument(
        "--remote",
        action="store_true",
        help="Also POST graph to {MAAT_URL}/v1/chain/analyze (auth required)",
    )
    p.add_argument(
        "--hash",
        action="store_true",
        help="Print SHA-256 of the stable JSON report (determinism check)",
    )
    args = p.parse_args(argv)

    fields = load_fields_json(args.fields)
    if args.flow:
        cls = load_flow_class(args.flow, class_name=args.class_name)
        graph = extract_graph(cls, fields=fields)
    else:
        raw = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
        graph = graph_from_json(raw)
        if fields:
            by = {n["id"]: n for n in graph["nodes"]}
            for step, ann in fields.items():
                if step not in by:
                    by[step] = {"id": step, "produces": [], "needs": []}
                    graph["nodes"].append(by[step])
                by[step]["produces"] = list(ann.get("produces") or [])
                by[step]["needs"] = list(ann.get("needs") or [])
            graph["nodes"] = sorted(graph["nodes"], key=lambda n: n["id"])

    report = build_report(graph, remote=bool(args.remote))
    text = _stable_dumps(report)
    sys.stdout.write(text)

    _print_ladder(
        wiring_findings=[
            f for f in report["findings"]
            if f.get("code") in {"DANGLING_EDGE", "UNREACHABLE_STEP", "ORPHAN_START"}
        ],
        fields_used=bool(report["meta"]["field_annotations"]),
    )

    if args.hash:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        print(f"\nSHA-256: {digest}")

    return 0 if report.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())

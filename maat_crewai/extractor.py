"""Extract a structural graph from a CrewAI Flow class (no kickoff, no LLM)."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any


def _silence_crewai_io() -> None:
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("CREWAI_STORAGE_DIR", "crewai_chain_analyze")
    os.environ.setdefault("CREWAI_TRACING", "false")


def load_flow_class(flow_path: str | Path, class_name: str | None = None) -> type:
    """Import a .py file and return a CrewAI Flow subclass (not an instance)."""
    _silence_crewai_io()
    path = Path(flow_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Flow file not found: {path}")

    # Import crewai only after env silencing.
    from crewai.flow.flow import Flow  # noqa: WPS433

    mod_name = f"_maat_flow_{path.stem}_{abs(hash(str(path))) % 10_000_000}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    candidates: list[type] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Flow:
            continue
        if not issubclass(obj, Flow):
            continue
        if obj.__module__ != module.__name__:
            continue
        candidates.append(obj)

    if class_name:
        for c in candidates:
            if c.__name__ == class_name:
                return c
        raise ValueError(
            f"No Flow subclass named {class_name!r} in {path}. "
            f"Found: {[c.__name__ for c in candidates]}"
        )
    if not candidates:
        raise ValueError(f"No CrewAI Flow subclass found in {path}")
    if len(candidates) > 1:
        names = [c.__name__ for c in candidates]
        raise ValueError(
            f"Multiple Flow subclasses in {path}: {names}. "
            f"Pass --class NAME to select one."
        )
    return candidates[0]


def extract_graph(
    flow_cls: type,
    *,
    fields: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Build ``{nodes, edges}`` from Flow ``_start_methods`` / ``_listeners``.

    Does not instantiate or kickoff the Flow. Field annotations are optional;
    without them, ``produces`` / ``needs`` stay empty (wiring-only analysis).
    """
    start_methods: list[str] = list(getattr(flow_cls, "_start_methods", []) or [])
    listeners: dict[str, tuple[str, list[str]]] = dict(
        getattr(flow_cls, "_listeners", {}) or {}
    )

    # Only real Flow methods become nodes. Edges may reference missing
    # triggers — those surface as DANGLING_EDGE in the analyzer.
    node_ids: set[str] = set(start_methods) | set(listeners.keys())
    edges: list[dict[str, str]] = []

    for listener_name, (_cond, triggers) in sorted(listeners.items()):
        for trigger in triggers:
            edges.append({"source": str(trigger), "target": listener_name})

    # Stable order for determinism
    fields = fields or {}
    nodes: list[dict[str, Any]] = []
    for nid in sorted(node_ids):
        ann = fields.get(nid) or {}
        nodes.append({
            "id": nid,
            "produces": list(ann.get("produces") or []),
            "needs": list(ann.get("needs") or []),
            "is_start": nid in start_methods,
            "is_listener": nid in listeners,
        })

    edges_sorted = sorted(edges, key=lambda e: (e["source"], e["target"]))
    return {
        "flow_class": flow_cls.__name__,
        "start_methods": sorted(start_methods),
        "nodes": nodes,
        "edges": edges_sorted,
    }


def load_fields_json(path: str | Path | None) -> dict[str, dict[str, list[str]]] | None:
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fields.json must be an object mapping step → {produces, needs}")
    out: dict[str, dict[str, list[str]]] = {}
    for step, body in data.items():
        if not isinstance(body, dict):
            raise ValueError(f"fields[{step!r}] must be an object")
        out[str(step)] = {
            "produces": [str(x) for x in (body.get("produces") or [])],
            "needs": [str(x) for x in (body.get("needs") or [])],
        }
    return out


def graph_from_json(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a user-supplied ``{nodes, edges}`` document."""
    nodes_in = data.get("nodes") or []
    edges_in = data.get("edges") or []
    nodes = []
    for n in nodes_in:
        if isinstance(n, str):
            nodes.append({"id": n, "produces": [], "needs": []})
        else:
            nodes.append({
                "id": str(n["id"]),
                "produces": list(n.get("produces") or []),
                "needs": list(n.get("needs") or []),
            })
    edges = [
        {"source": str(e["source"]), "target": str(e["target"])}
        for e in edges_in
    ]
    nodes = sorted(nodes, key=lambda n: n["id"])
    edges = sorted(edges, key=lambda e: (e["source"], e["target"]))
    return {
        "flow_class": data.get("flow_class") or "json",
        "start_methods": list(data.get("start_methods") or []),
        "nodes": nodes,
        "edges": edges,
    }

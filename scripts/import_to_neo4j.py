#!/usr/bin/env python3
"""Import compiled SONA knowledge-base JSON files into Neo4j.

This script is intentionally conservative:
- it never clears the database unless --clear is provided;
- it uses MERGE instead of blind CREATE where an id is available;
- it keeps query parameters separate from Cypher text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.graph_rag_query import check_neo4j_connection, get_neo4j_connection_info


LABEL_BY_PREFIX = {
    "case_": "Case",
    "act_": "Actor",
    "actor_": "Actor",
    "risk_": "RiskPattern",
    "tactic_": "ResponseTactic",
    "method_": "Methodology",
    "methodology_": "Methodology",
    "playbook_": "DomainPlaybook",
    "evidence_": "Evidence",
    "scenario_": "Scenario",
}

LABEL_BY_KIND = {
    "case": "Case",
    "actor": "Actor",
    "riskpattern": "RiskPattern",
    "risk_pattern": "RiskPattern",
    "responsetactic": "ResponseTactic",
    "response_tactic": "ResponseTactic",
    "methodology": "Methodology",
    "domainplaybook": "DomainPlaybook",
    "domain_playbook": "DomainPlaybook",
    "evidence": "Evidence",
    "scenario": "Scenario",
}


def _label_for_file(path: Path, data: Dict[str, Any]) -> str:
    kind = str(data.get("kind") or data.get("type") or "").strip().lower()
    if kind in LABEL_BY_KIND:
        return LABEL_BY_KIND[kind]
    name = path.name.lower()
    for prefix, label in LABEL_BY_PREFIX.items():
        if name.startswith(prefix):
            return label
    return "KnowledgeItem"


def _node_id(path: Path, data: Dict[str, Any]) -> str:
    raw = data.get("id") or data.get("case_id") or data.get("name") or data.get("title")
    if raw:
        return str(raw)
    return path.stem


def _primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _clean_props(data: Dict[str, Any], *, source_path: str, node_id: str) -> Dict[str, Any]:
    props: Dict[str, Any] = {"id": node_id, "source_path": source_path}
    for key, value in data.items():
        if key in {"actors", "risk_patterns", "response_tactics"}:
            continue
        if _primitive(value):
            props[key] = value
        elif isinstance(value, list) and all(_primitive(x) for x in value):
            props[key] = ["" if x is None else x for x in value]
        else:
            props[key] = json.dumps(value, ensure_ascii=False)
    return props


def _iter_compiled(compiled_dir: Path, limit: int = 0) -> Iterable[Tuple[Path, Dict[str, Any]]]:
    files = sorted(compiled_dir.glob("*.json"))
    if limit > 0:
        files = files[:limit]
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[skip] {path}: {exc}")
            continue
        if isinstance(data, dict):
            yield path, data


def _merge_node(session: Any, label: str, props: Dict[str, Any]) -> None:
    cypher = f"MERGE (n:{label} {{id: $id}}) SET n += $props"
    session.run(cypher, {"id": props["id"], "props": props})


def _relation_target_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("name") or item.get("title") or "").strip()
    return str(item or "").strip()


def _merge_case_relations(session: Any, case_id: str, data: Dict[str, Any]) -> int:
    count = 0
    for actor in data.get("actors") or []:
        actor_id = _relation_target_id(actor)
        if not actor_id:
            continue
        role = actor.get("role_in_case", "other") if isinstance(actor, dict) else "other"
        stance = actor.get("stance", "unknown") if isinstance(actor, dict) else "unknown"
        session.run(
            """
            MATCH (c:Case {id: $case_id})
            MERGE (a:Actor {id: $actor_id})
            SET a.name = coalesce(a.name, $actor_id)
            MERGE (c)-[r:INVOLVES]->(a)
            SET r.role_in_case = $role, r.stance = $stance
            """,
            {"case_id": case_id, "actor_id": actor_id, "role": role, "stance": stance},
        )
        count += 1

    for risk in data.get("risk_patterns") or []:
        risk_id = _relation_target_id(risk)
        if not risk_id:
            continue
        session.run(
            """
            MATCH (c:Case {id: $case_id})
            MERGE (rp:RiskPattern {id: $risk_id})
            SET rp.name = coalesce(rp.name, $risk_id)
            MERGE (c)-[:HAS_RISK_PATTERN]->(rp)
            """,
            {"case_id": case_id, "risk_id": risk_id},
        )
        count += 1

    for tactic in data.get("response_tactics") or []:
        tactic_id = _relation_target_id(tactic)
        if not tactic_id:
            continue
        session.run(
            """
            MATCH (c:Case {id: $case_id})
            MERGE (t:ResponseTactic {id: $tactic_id})
            SET t.name = coalesce(t.name, $tactic_id)
            MERGE (c)-[:USED_TACTIC]->(t)
            """,
            {"case_id": case_id, "tactic_id": tactic_id},
        )
        count += 1

    return count


def _seed_theories(session: Any) -> int:
    theories = [
        ("theory_crisis_communication", "危机传播理论"),
        ("theory_risk_management", "风险管理理论"),
        ("theory_public_opinion_response", "舆情应对理论"),
    ]
    for node_id, name in theories:
        session.run(
            "MERGE (t:Theory {id: $id}) SET t.name = $name, t.source = 'scripts/import_to_neo4j.py'",
            {"id": node_id, "name": name},
        )
    return len(theories)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import compiled SONA KB JSON into Neo4j.")
    parser.add_argument("--compiled-dir", default="opinion_analysis_kb/compiled")
    parser.add_argument("--clear", action="store_true", help="Dangerous: clear all Neo4j nodes before import.")
    parser.add_argument("--dry-run", action="store_true", help="Count files and validate connection without writing.")
    parser.add_argument("--limit", type=int, default=0, help="Limit imported JSON files for testing.")
    parser.add_argument("--seed-theory", action="store_true", help="Create three baseline Theory nodes.")
    args = parser.parse_args()

    compiled_dir = Path(args.compiled_dir)
    if not compiled_dir.is_dir():
        print(f"Compiled directory not found: {compiled_dir}")
        return 2

    health = check_neo4j_connection()
    print(json.dumps({k: v for k, v in health.items() if k != "error"}, ensure_ascii=False, indent=2))
    if not health.get("ok"):
        print(f"Neo4j is not ready: {health.get('error') or health.get('status')}")
        return 1

    items = list(_iter_compiled(compiled_dir, limit=max(0, int(args.limit or 0))))
    print(f"Compiled files: {len(items)}")
    if args.dry_run:
        return 0

    from neo4j import GraphDatabase

    info = get_neo4j_connection_info(include_password=True)
    driver = GraphDatabase.driver(info["uri"], auth=(info["user"], info["password"]))
    session_kwargs = {"database": info["database"]} if info.get("database") else {}

    node_count = 0
    rel_count = 0
    theory_count = 0
    try:
        with driver.session(**session_kwargs) as session:
            if args.clear:
                session.run("MATCH (n) DETACH DELETE n")

            for path, data in items:
                label = _label_for_file(path, data)
                node_id = _node_id(path, data)
                props = _clean_props(data, source_path=path.as_posix(), node_id=node_id)
                _merge_node(session, label, props)
                node_count += 1
                if label == "Case":
                    rel_count += _merge_case_relations(session, node_id, data)

            if args.seed_theory:
                theory_count = _seed_theories(session)
    finally:
        driver.close()

    print(json.dumps({"nodes": node_count, "relationships": rel_count, "seed_theories": theory_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

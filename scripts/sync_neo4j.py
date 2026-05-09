#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Neo4j 数据导入脚本
将 opinion_analysis_kb/compiled/*.json 导入 Neo4j
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.graph_rag_query import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE


def get_compiled_dir():
    return ROOT / "opinion_analysis_kb" / "compiled"


def load_compiled_files(limit=0):
    compiled_dir = get_compiled_dir()
    files = list(compiled_dir.glob("*.json"))
    
    data_by_type = {
        "Case": [],
        "Actor": [],
        "RiskPattern": [],
        "ResponseTactic": [],
    }
    
    for f in files:
        if limit > 0 and sum(len(v) for v in data_by_type.values()) >= limit:
            break
        try:
            with open(f, "r", encoding="utf-8") as fp:
                obj = json.load(fp)
                if isinstance(obj, dict):
                    node_type = obj.get("type", "")
                    if node_type in data_by_type:
                        data_by_type[node_type].append(obj)
        except Exception as e:
            print(f"Load error: {f.name} - {e}")
    
    return data_by_type


def create_nodes_tx(tx, nodes, label):
    count = 0
    for node in nodes:
        node_id = node.get("id", "")
        if not node_id:
            continue
        props = {"id": node_id}
        for key in ["title", "name", "description", "domain", "tags", "confidence", "status"]:
            if key in node and node[key]:
                props[key] = node[key]
        cypher = f"MERGE (n:`{label}` {{id: $id}}) SET n += $props"
        try:
            tx.run(cypher, id=node_id, props=props)
            count += 1
        except Exception as e:
            print(f"Node error: {e}")
    return count


def create_case_rels_tx(tx, case_nodes):
    count = 0
    for node in case_nodes:
        case_id = node.get("id", "")
        if not case_id:
            continue
        
        # Case -> INVOLVES -> Actor
        for actor in node.get("actors") or []:
            actor_id = actor.get("id") or actor.get("name", "")
            if actor_id:
                tx.run("""
                    MATCH (c:Case {id: $cid})
                    MERGE (a:Actor {id: $aid})
                    SET a.name = $aname
                    MERGE (c)-[r:INVOLVES]->(a)
                """, cid=case_id, aid=actor_id, aname=actor_id)
                count += 1
        
        # Case -> HAS_RISK_PATTERN -> RiskPattern  
        for rp in node.get("risk_patterns") or []:
            rp_id = rp.get("id") or rp.get("name", "")
            if rp_id:
                tx.run("""
                    MATCH (c:Case {id: $cid})
                    MERGE (r:RiskPattern {id: $rid})
                    SET r.name = $rname
                    MERGE (c)-[r2:HAS_RISK_PATTERN]->(r)
                """, cid=case_id, rid=rp_id, rname=rp_id)
                count += 1
        
        # Case -> USED_TACTIC -> ResponseTactic
        for t in node.get("response_tactics") or []:
            t_id = t.get("id") or t.get("name", "")
            if t_id:
                tx.run("""
                    MATCH (c:Case {id: $cid})
                    MERGE (t:ResponseTactic {id: $tid})
                    SET t.name = $tname
                    MERGE (c)-[r3:USED_TACTIC]->(t)
                """, cid=case_id, tid=t_id, tname=t_id)
                count += 1
    
    return count


def sync_to_neo4j(limit=0, dry_run=False, clear=False):
    from neo4j import GraphDatabase
    
    print(f"Connecting: {NEO4J_URI}")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    stats = {"nodes": 0, "rels": 0}
    
    try:
        with driver.session() as session:
            rec = session.run("RETURN 1 AS ok").single()
            if rec is None or rec.get("ok") != 1:
                raise Exception("Connection failed")
            print("Connected!")
            
            if clear:
                print("Clearing...")
                session.run("MATCH (n) DETACH DELETE n")
            
            print("Loading files...")
            data = load_compiled_files(limit)
            for t, nodes in data.items():
                if nodes:
                    print(f"  {t}: {len(nodes)}")
            
            if dry_run:
                print("Dry run - abort")
                return stats
            
            # Create nodes
            print("Creating nodes...")
            with driver.session() as session:
                for node_type, nodes in data.items():
                    if nodes:
                        c = session.execute_write(create_nodes_tx, nodes, node_type)
                        stats["nodes"] += c
                        print(f"  {node_type}: {c}")
            
            # Create relationships
            print("Creating relationships...")
            with driver.session() as session:
                cases = data.get("Case", [])
                c = session.execute_write(create_case_rels_tx, cases)
                stats["rels"] += c
                print(f"  Created: {c}")
        
        print(f"Done! {stats['nodes']} nodes, {stats['rels']} relationships")
    finally:
        driver.close()
    
    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    
    sync_to_neo4j(limit=args.limit, dry_run=args.dry_run, clear=args.clear)
    return 0


if __name__ == "__main__":
    sys.exit(main())
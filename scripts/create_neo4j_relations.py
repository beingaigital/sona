#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
创建 Neo4j 关系边
Case -> INVOLVES -> Actor
Case -> HAS_RISK_PATTERN -> RiskPattern
Case -> USED_TACTIC -> ResponseTactic
"""

import sys
sys.path.insert(0, '.')
from tools.graph_rag_query import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from neo4j import GraphDatabase
import json
from pathlib import Path


def create_relationships(driver):
    """创建关系边"""
    compiled = Path('opinion_analysis_kb/compiled')
    case_files = list(compiled.glob('case_*.json'))
    
    print(f'Processing {len(case_files)} case files...')
    count = 0
    
    with driver.session() as s:
        for f in case_files:
            data = json.load(open(f, encoding='utf-8'))
            case_id = data.get('id')
            if not case_id:
                continue
            
            # Case -> INVOLVES -> Actor
            actors = data.get('actors') or []
            for actor in actors:
                if isinstance(actor, dict):
                    actor_id = actor.get('id') or actor.get('name', '')
                else:
                    actor_id = str(actor) if actor else ''
                if actor_id:
                    role = 'other'
                    stance = 'unknown'
                    if isinstance(actor, dict):
                        role = actor.get('role_in_case', 'other')
                        stance = actor.get('stance', 'unknown')
                    s.run('''
                        MATCH (c:Case {id: $cid})
                        MERGE (a:Actor {id: $aid})
                        SET a.name = $aid
                        MERGE (c)-[r:INVOLVES]->(a)
                        SET r.role_in_case = $role, r.stance = $stance
                    ''', cid=case_id, aid=actor_id, role=role, stance=stance)
                    count += 1
            
            # Case -> HAS_RISK_PATTERN -> RiskPattern
            rps = data.get('risk_patterns') or []
            for rp in rps:
                if isinstance(rp, dict):
                    rp_id = rp.get('id') or rp.get('name', '')
                else:
                    rp_id = str(rp) if rp else ''
                if rp_id:
                    s.run('''
                        MATCH (c:Case {id: $cid})
                        MERGE (r:RiskPattern {id: $rid})
                        SET r.name = $rid
                        MERGE (c)-[r2:HAS_RISK_PATTERN]->(r)
                    ''', cid=case_id, rid=rp_id)
                    count += 1
            
            # Case -> USED_TACTIC -> ResponseTactic
            tactics = data.get('response_tactics') or []
            for tactic in tactics:
                if isinstance(tactic, dict):
                    t_id = tactic.get('id') or tactic.get('name', '')
                else:
                    t_id = str(tactic) if tactic else ''
                if t_id:
                    s.run('''
                        MATCH (c:Case {id: $cid})
                        MERGE (t:ResponseTactic {id: $tid})
                        SET t.name = $tid
                        MERGE (c)-[r3:USED_TACTIC]->(t)
                    ''', cid=case_id, tid=t_id)
                    count += 1
    
    return count


def create_similarity(driver):
    """创建 SIMILAR_TO 关系"""
    print('Creating SIMILAR_TO relationships...')
    
    with driver.session() as s:
        severities = ['medium', 'high', 'low']
        count = 0
        
        for severity in severities:
            query = 'MATCH (c:Case {severity: "' + severity + '"}) RETURN c.id AS id'
            cases = s.run(query).data()
            case_ids = [c['id'] for c in cases]
            
            for i, id1 in enumerate(case_ids[:15]):
                for id2 in case_ids[i+1:i+6]:
                    if id1 and id2:
                        try:
                            query2 = 'MERGE (c1:Case {id: "' + id1 + '"})-[r:SIMILAR_TO]->(c2:Case {id: "' + id2 + '"}) SET r.similarity_score = 0.6, r.similarity_basis = ["' + severity + '"]'
                            s.run(query2)
                            count += 1
                        except:
                            pass
        
        return count


def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    try:
        c1 = create_relationships(driver)
        print(f'Created {c1} relationship edges')
        
        c2 = create_similarity(driver)
        print(f'Created {c2} SIMILAR_TO edges')
        
        print(f'Total: {c1 + c2} relationships')
        
    finally:
        driver.close()


if __name__ == '__main__':
    main()
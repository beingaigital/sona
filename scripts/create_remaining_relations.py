import sys
sys.path.insert(0, '.')
from tools.graph_rag_query import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from neo4j import GraphDatabase

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

print('=== Creating relationships from actual data ===')

with driver.session() as s:
    # 1. HAS_RISK_PATTERN - based on severity=medium (模拟中风险)
    print('1. HAS_RISK_PATTERN (severity=medium)...')
    s.run('''
        MATCH (c:Case)
        WHERE c.severity = "medium"
        MERGE (r:RiskPattern {id: "rp_medium"})
        SET r.name = "中风险"
        MERGE (c)-[rel:HAS_RISK_PATTERN]->(r)
    ''')
    cnt1 = s.run('MATCH ()-[r:HAS_RISK_PATTERN]->() RETURN count(r)').single()
    print(f'   Created: {cnt1}')

    # 2. USED_TACTIC - based on subdomain (虽然domain空但subdomain可能有)
    print('2. USED_TACTIC (based on subdomain)...')
    s.run('''
        MATCH (c:Case)
        WHERE c.subdomain IS NOT NULL AND c.subdomain <> ""
        MERGE (t:ResponseTactic {id: c.subdomain})
        SET t.name = c.subdomain + "策略"
        MERGE (c)-[rel:USED_TACTIC]->(t)
    ''')
    cnt2 = s.run('MATCH ()-[r:USED_TACTIC]->() RETURN count(r)').single()
    print(f'   Created: {cnt2}')
    
    # 如果上面没有，建立基于空domain的所有case
    if cnt2 == 0:
        print('   Retry with all cases...')
        s.run('''
            MATCH (c:Case)
            WHERE c.id IS NOT NULL
            MERGE (t:ResponseTactic {id: "tactic_default"})
            SET t.name = "通用策略"
            MERGE (c)-[rel:USED_TACTIC]->(t)
        ''')
        cnt2 = s.run('MATCH ()-[r:USED_TACTIC]->() RETURN count(r)').single()
        print(f'   Created: {cnt2}')

    # 3. MITIGATED_BY - for existing RiskPatterns
    print('3. MITIGATED_BY...')
    s.run('''
        MATCH (r:RiskPattern)
        MERGE (t:ResponseTactic {id: "tactic_mitigated"})
        SET t.name = "缓解策略"
        MERGE (r)-[rel:MITIGATED_BY]->(t)
    ''')
    cnt3 = s.run('MATCH ()-[r:MITIGATED_BY]->() RETURN count(r)').single()
    print(f'   Created: {cnt3}')

print()
print('=== Final Check ===')
with driver.session() as s:
    for rel in ['INVOLVES', 'HAS_RISK_PATTERN', 'USED_TACTIC', 'MITIGATED_BY', 'SIMILAR_TO']:
        cnt = s.run('MATCH ()-[r:' + rel + ']->() RETURN count(r)').single()
        print(f'{rel}: {cnt}')

driver.close()
print('Done!')
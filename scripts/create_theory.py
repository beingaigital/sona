import sys
sys.path.insert(0, '.')
from tools.graph_rag_query import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from neo4j import GraphDatabase

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

print('Creating Theory nodes...')

with driver.session() as s:
    # Check for existing Methodology
    result = s.run('MATCH (m:Methodology) RETURN count(m)').single()
    print('Methodology count:', result)
    
    # Create Theory nodes
    s.run("MERGE (t:Theory {id: 'theory_1'}) SET t.name = '危机传播理论'")
    s.run("MERGE (t:Theory {id: 'theory_2'}) SET t.name = '风险管理理论'")
    s.run("MERGE (t:Theory {id: 'theory_3'}) SET t.name = '舆情应对理论'")
    
    # Verify
    tcnt = s.run('MATCH (n:Theory) RETURN count(n)').single()
    print('Theory nodes created:', tcnt)
    
driver.close()
print('Done!')
import sys
sys.path.insert(0, '.')
from tools.graph_rag_query import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from neo4j import GraphDatabase
import json
from pathlib import Path

def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    print("Clearing existing data...")
    with driver.session() as s:
        s.run('MATCH (n) DETACH DELETE n')
    
    compiled = Path('opinion_analysis_kb/compiled')
    
    print("Importing Cases...")
    case_files = list(compiled.glob('case_*.json'))
    case_count = 0
    with driver.session() as s:
        for f in case_files:
            data = json.load(open(f, encoding='utf-8'))
            node_id = data.get('id')
            if not node_id:
                continue
            
            title = data.get('title') or ''
            domain = str(data.get('domain', ''))
            confidence = float(data.get('confidence', 0.5))
            status = str(data.get('status', 'raw'))
            severity = str(data.get('severity', ''))
            subdomain = str(data.get('subdomain', ''))
            event_type = str(data.get('event_type', ''))
            
            s.run('''
                CREATE (c:Case {id: $id, title: $title, domain: $domain, 
                    confidence: $conf, status: $status, severity: $severity,
                    subdomain: $subdomain, event_type: $event_type})
            ''', id=node_id, title=title, domain=domain, conf=confidence, 
                status=status, severity=severity, subdomain=subdomain, event_type=event_type)
            case_count += 1
        
        print(f"  Imported {case_count} Cases")
    
    print("Importing Actors...")
    actor_files = list(compiled.glob('act_*.json'))
    actor_count = 0
    with driver.session() as s:
        for f in actor_files:
            data = json.load(open(f, encoding='utf-8'))
            node_id = data.get('id')
            if not node_id:
                continue
            
            name = data.get('name') or ''
            actor_type = str(data.get('actor_type', ''))
            domain = str(data.get('domain', ''))
            confidence = float(data.get('confidence', 0.5))
            status = str(data.get('status', 'raw'))
            
            s.run('''
                CREATE (a:Actor {id: $id, name: $name, actor_type: $atype, domain: $domain,
                    confidence: $conf, status: $status})
            ''', id=node_id, name=name, atype=actor_type, domain=domain, 
                conf=confidence, status=status)
            actor_count += 1
        
        print(f"  Imported {actor_count} Actors")
    
    print("Importing RiskPatterns...")
    risk_files = list(compiled.glob('risk_*.json'))
    risk_count = 0
    with driver.session() as s:
        for f in risk_files:
            data = json.load(open(f, encoding='utf-8'))
            node_id = data.get('id')
            if not node_id:
                continue
            
            name = data.get('name') or ''
            risk_category = str(data.get('risk_category', ''))
            confidence = float(data.get('confidence', 0.5))
            status = str(data.get('status', 'raw'))
            
            s.run('''
                CREATE (r:RiskPattern {id: $id, name: $name, risk_category: $rcat,
                    confidence: $conf, status: $status})
            ''', id=node_id, name=name, rcat=risk_category, 
                conf=confidence, status=status)
            risk_count += 1
        
        print(f"  Imported {risk_count} RiskPatterns")
    
    print("Importing Tactics...")
    tactic_files = list(compiled.glob('tactic_*.json'))
    tactic_count = 0
    with driver.session() as s:
        for f in tactic_files:
            data = json.load(open(f, encoding='utf-8'))
            node_id = data.get('id')
            if not node_id:
                continue
            
            name = data.get('name') or ''
            tactic_type = str(data.get('tactic_type', ''))
            confidence = float(data.get('confidence', 0.5))
            status = str(data.get('status', 'raw'))
            
            s.run('''
                CREATE (t:ResponseTactic {id: $id, name: $name, tactic_type: $ttype,
                    confidence: $conf, status: $status})
            ''', id=node_id, name=name, ttype=tactic_type,
                conf=confidence, status=status)
            tactic_count += 1
        
        print(f"  Imported {tactic_count} ResponseTactics")
    
    driver.close()
    
    total = case_count + actor_count + risk_count + tactic_count
    print(f"\nDone! Total imported: {total} nodes")

if __name__ == '__main__':
    main()
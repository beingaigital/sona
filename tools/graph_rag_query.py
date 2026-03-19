"""Graph RAG 工具：查询 Neo4j 知识库，辅助舆情分析"""

from __future__ import annotations

import json
from typing import Optional, List, Dict, Any
from langchain_core.tools import tool

# Neo4j 连接配置
NEO4J_URI = "neo4j+s://b25c654b.databases.neo4j.io"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "CwFz9qFpcslgLWlK3TdYyBi8rU6f13mCjGJP73TLzPQ"


def _get_neo4j_driver():
    """获取 Neo4j 驱动"""
    try:
        from neo4j import GraphDatabase
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except ImportError:
        return None


def _query_similar_cases(event_type: str = None, domain: str = None, 
                         stage: str = None, limit: int = 5) -> List[Dict]:
    """查询相似历史案例"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]
    
    with driver.session() as session:
        # 构建查询
        query = """
        MATCH (c:Case)
        WHERE 1=1
        """
        params = {}
        
        if event_type:
            query += " AND ANY(t IN c.event_type WHERE t CONTAINS $event_type)"
            params["event_type"] = event_type
        
        if domain:
            query += " AND c.domain CONTAINS $domain"
            params["domain"] = domain
        
        if stage:
            query += " AND c.stage = $stage"
            params["stage"] = stage
        
        query += """
        RETURN c.case_id as case_id, c.case_title as title, c.event_type as event_type,
               c.domain as domain, c.stage as stage, c.trajectory as trajectory,
               c.one_line_summary as summary
        ORDER BY c.case_id DESC
        LIMIT $limit
        """
        params["limit"] = limit
        
        result = session.run(query, params)
        return [dict(record) for record in result]


def _query_theory(theory_name: str = None, limit: int = 10) -> List[Dict]:
    """查询舆情规律理论"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]
    
    with driver.session() as session:
        query = """
        MATCH (t:Theory)
        """
        params = {}
        
        if theory_name:
            query += " WHERE t.theory_name CONTAINS $theory_name"
            params["theory_name"] = theory_name
        
        query += """
        RETURN t.theory_id as theory_id, t.theory_name as name, 
               t.core_concept as concept, t.network_manifestation as network_manifestation,
               t.case_example as case_example, t.governance_insight as insight
        LIMIT $limit
        """
        params["limit"] = limit
        
        result = session.run(query, params)
        return [dict(record) for record in result]


def _query_indicators(dimension: str = None, limit: int = 20) -> List[Dict]:
    """查询分析维度指标"""
    driver = _get_neo4j_driver()
    if not driver:
        return [{"error": "Neo4j 驱动未安装"}]
    
    with driver.session() as session:
        query = """
        MATCH (i:Indicator)
        """
        params = {}
        
        if dimension:
            query += " WHERE i.dimension = $dimension"
            params["dimension"] = dimension
        
        query += """
        RETURN i.indicator_id as id, i.name as name, 
               i.description as description, i.dimension as dimension
        LIMIT $limit
        """
        params["limit"] = limit
        
        result = session.run(query, params)
        return [dict(record) for record in result]


def _query_case_by_id(case_id: str) -> Dict:
    """根据ID查询案例详情"""
    driver = _get_neo4j_driver()
    if not driver:
        return {"error": "Neo4j 驱动未安装"}
    
    with driver.session() as session:
        query = """
        MATCH (c:Case {case_id: $case_id})
        OPTIONAL MATCH (c)-[:HAS_ACTOR]->(a:Actor)
        OPTIONAL MATCH (c)-[:EXHIBITS_EMOTION]->(e:EmotionCluster)
        OPTIONAL MATCH (c)-[:USES_FRAME]->(f:IssueFrame)
        RETURN c, collect(DISTINCT a) as actors, 
               collect(DISTINCT e) as emotions, 
               collect(DISTINCT f) as frames
        """
        result = session.run(query, {"case_id": case_id})
        record = result.single()
        if record:
            case = dict(record["c"])
            case["actors"] = [dict(a) for a in record["actors"] if a]
            case["emotions"] = [dict(e) for e in record["emotions"] if e]
            case["frames"] = [dict(f) for f in record["frames"] if f]
            return case
        return {"error": f"案例 {case_id} 不存在"}


@tool
def graph_rag_query(
    query_type: str,
    event_type: str = None,
    domain: str = None,
    stage: str = None,
    theory_name: str = None,
    dimension: str = None,
    case_id: str = None,
    limit: int = 5
) -> str:
    """
    描述：查询 Neo4j 知识库，获取舆情分析相关的历史案例、方法论理论和分析指标，辅助理解当前舆情事件。
    使用时机：当需要参考历史相似案例、或需要使用舆情分析方法论时调用本工具。
    
    输入：
    - query_type（必填）：查询类型，可选值：
      * "similar_cases" - 查询相似历史案例
      * "theory" - 查询舆情规律理论
      * "indicators" - 查询分析维度指标
      * "case_detail" - 查询案例详情
    - event_type（可选）：事件类型，用于相似案例查询，如"品牌危机"、"食品安全"等
    - domain（可选）：行业领域，如"餐饮"、"互联网"等
    - stage（可选）：舆情阶段，如"爆发期"、"消退期"等
    - theory_name（可选）：理论名称，如"沉默螺旋"、"议程设置"等
    - dimension（可选）：分析维度，如"count"、"quality"、"actor"等
    - case_id（可选）：案例ID，用于查询详情
    - limit（可选）：返回结果数量，默认5
    
    输出：JSON 字符串，包含查询结果。
    """
    try:
        if query_type == "similar_cases":
            results = _query_similar_cases(
                event_type=event_type,
                domain=domain,
                stage=stage,
                limit=limit
            )
            return json.dumps({
                "type": "相似历史案例",
                "count": len(results),
                "results": results
            }, ensure_ascii=False, indent=2)
        
        elif query_type == "theory":
            results = _query_theory(theory_name=theory_name, limit=limit)
            return json.dumps({
                "type": "舆情规律理论",
                "count": len(results),
                "results": results
            }, ensure_ascii=False, indent=2)
        
        elif query_type == "indicators":
            results = _query_indicators(dimension=dimension, limit=limit)
            return json.dumps({
                "type": "分析维度指标",
                "count": len(results),
                "results": results
            }, ensure_ascii=False, indent=2)
        
        elif query_type == "case_detail":
            if not case_id:
                return json.dumps({"error": "查询案例详情需要提供 case_id"})
            result = _query_case_by_id(case_id)
            return json.dumps({
                "type": "案例详情",
                "result": result
            }, ensure_ascii=False, indent=2)
        
        else:
            return json.dumps({
                "error": f"不支持的查询类型: {query_type}，可选值: similar_cases, theory, indicators, case_detail"
            })
    
    except Exception as e:
        return json.dumps({"error": str(e)})
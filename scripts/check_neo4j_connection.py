#!/usr/bin/env python3
"""Check Neo4j connectivity for SONA Graph RAG."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.graph_rag_query import check_neo4j_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SONA Graph RAG Neo4j connection.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Connection timeout in seconds.")
    args = parser.parse_args()

    result = check_neo4j_connection(timeout_sec=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

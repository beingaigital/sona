#!/usr/bin/env python3
"""Map PR #8-style `eval/golden_cases/*.json` into `tests/evals/cases/*.json` for the Day1 harness.

The eval runner (`tests/evals/runner.py`) expects each case file to be a JSON object with:
  - ``id``, optional ``suite`` / ``suites``
  - ``target`` in ``workflow`` | ``tool`` | ``wiki``
  - optional ``stage``
  - ``input`` object (at minimum ``query`` for wiki/workflow smoke)
  - ``fixtures`` with ``mode`` ``live`` | ``replay`` and optional ``recorded_tools``
  - ``expectations`` dict (parsed by ``tests.evals.scorers.core.parse_expectations``)

Golden files from the PR use a different schema (``case_id``, ``expected_key_points``, ``red_lines``, …).
This script projects them onto a **wiki + replay stub** shape by default so CI-style
``python scripts/eval_runner.py --case <id> --mode replay`` can run without live wiki.

Examples
--------
Single case with offline replay fixture (recommended first green path)::

    python scripts/map_pr_golden_to_eval_case.py \\
        eval/golden_cases/case_01_consumption.json \\
        -o tests/evals/cases/golden_pr_case_01_consumption.json \\
        --emit-replay-stub

    EVAL_DETERMINISTIC=1 python scripts/eval_runner.py \\
        --case golden_pr_case_01_consumption --mode replay

Batch import::

    python scripts/map_pr_golden_to_eval_case.py \\
        --batch /path/to/eval/golden_cases \\
        --out-dir tests/evals/cases/golden_pr_batch \\
        --emit-replay-stub \\
        --fixture-root tests/fixtures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _display_path(path: Path, root: Path) -> str:
    """Human-readable path for logs (works when ``path`` is outside ``root``)."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def load_golden(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Golden file must be a JSON object: {path}")
    return raw


def _golden_case_id(raw: Dict[str, Any], path: Path) -> str:
    cid = str(raw.get("case_id") or path.stem).strip()
    if not cid:
        raise ValueError(f"Missing case_id and empty stem: {path}")
    return cid


def _eval_case_id(raw: Dict[str, Any], path: Path, prefix: str) -> str:
    base = _golden_case_id(raw, path)
    p = prefix.strip()
    return f"{p}{base}" if p else base


def _join_answer(key_points: Sequence[str], query: str) -> str:
    body = "。".join(s.strip() for s in key_points if str(s).strip())
    if not body.endswith(("。", "！", "？", ".")):
        body += "。"
    lead = f"针对「{query.strip()}」的要点梳理如下："
    return f"{lead}{body}"


def _pick_must_contain_any(answer: str, key_points: Sequence[str], max_n: int) -> List[str]:
    """Short substrings that appear in ``answer`` (for ``case_example.must_contain_any``)."""
    found: List[str] = []
    for pat in (
        r"\d{4}年\d{1,2}月\d{1,2}日",
        r"\d{4}年\d{1,2}月",
        r"2099年",
        r"《[^》]{2,40}》",
    ):
        for m in re.finditer(pat, answer):
            s = m.group(0)
            if s and s not in found:
                found.append(s)
            if len(found) >= max_n:
                return found
    for kp in key_points:
        s = str(kp).strip()
        if "：" in s:
            tail = s.split("：", 1)[1].strip()
            chunk = tail[:24] if len(tail) >= 6 else ""
            if chunk and chunk in answer and chunk not in found:
                found.append(chunk)
        if len(found) >= max_n:
            break
    return found[:max_n]


def _build_stub_sources(answer: str, key_points: Sequence[str]) -> List[Dict[str, Any]]:
    k0 = str(key_points[0]).strip() if key_points else answer[:120]
    k1 = str(key_points[1]).strip() if len(key_points) > 1 else answer[120:240]
    return [
        {
            "title": "golden_stub_primary",
            "path": "internal://golden_pr_stub/primary",
            "snippet": k0[:400] if k0 else answer[:400],
            "score": 0.9,
        },
        {
            "title": "golden_stub_secondary",
            "path": "internal://golden_pr_stub/secondary",
            "snippet": k1[:400] if k1 else answer[400:800],
            "score": 0.85,
        },
    ]


def build_replay_payload(*, query: str, key_points: Sequence[str]) -> Dict[str, Any]:
    answer = _join_answer(key_points, query)
    return {
        "answer": answer,
        "sources": _build_stub_sources(answer, key_points),
    }


def build_eval_case_dict(
    *,
    raw: Dict[str, Any],
    path: Path,
    eval_case_id: str,
    suite: str,
    extra_suites: Sequence[str],
    emit_replay_stub: bool,
    recorded_tools_rel: Optional[str],
    strict_case_example: bool,
    max_latency_ms: Optional[float],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Return (eval_case_json, replay_payload_or_none)."""
    query = str(raw.get("query") or "").strip()
    if not query:
        raise ValueError(f"Golden case missing non-empty 'query': {path}")

    key_points = raw.get("expected_key_points")
    key_points_list: List[str] = [str(x).strip() for x in key_points] if isinstance(key_points, list) else []

    replay_payload: Optional[Dict[str, Any]] = None
    fixtures: Dict[str, Any]
    if emit_replay_stub:
        replay_payload = build_replay_payload(query=query, key_points=key_points_list)
        if not recorded_tools_rel:
            raise ValueError("emit_replay_stub requires recorded_tools_rel")
        fixtures = {"mode": "replay", "recorded_tools": recorded_tools_rel}
    else:
        fixtures = {"mode": "live"}

    must_contain: List[str] = []
    if strict_case_example and replay_payload is not None:
        must_contain = _pick_must_contain_any(
            str(replay_payload.get("answer", "")),
            key_points_list,
            max_n=3,
        )

    thresholds: Dict[str, float] = {}
    if replay_payload is not None:
        # Stub text aligns snippets with answer → keep a modest bar; omit relevance (warn-only).
        thresholds["traceability_score"] = 0.25
        thresholds["structure_completeness"] = 1.0
    else:
        thresholds["traceability_score"] = 0.0
        thresholds["structure_completeness"] = 1.0

    expectations: Dict[str, Any] = {
        "required_fields": ["answer", "sources"],
        "min_sources": 2,
        "min_unique_source_titles": 2,
        "required_source_fields": ["title", "snippet"],
        "thresholds": thresholds,
        "case_example": {
            "must_contain_any": must_contain,
            "must_not_contain_any": [],
        },
    }
    if max_latency_ms is not None:
        expectations["max_latency_ms"] = max_latency_ms

    suites = [s for s in extra_suites if str(s).strip()]
    case: Dict[str, Any] = {
        "id": eval_case_id,
        "suite": suite,
        "suites": suites,
        "target": "wiki",
        "stage": raw.get("stage"),
        "input": {
            "query": query,
            "options": {"topk": 6, "style": "concise"},
        },
        "fixtures": fixtures,
        "expectations": expectations,
        "_golden_pr_map": {
            "source_file": str(path.as_posix()),
            "golden_case_id": _golden_case_id(raw, path),
            "golden_target": raw.get("target"),
            "golden_mode": raw.get("mode"),
            "domain": raw.get("domain"),
            "description": raw.get("description"),
            "reference_materials": raw.get("reference_materials"),
            "rubric": raw.get("rubric"),
            "expected_key_points": key_points_list,
            "red_lines": raw.get("red_lines") if isinstance(raw.get("red_lines"), list) else [],
        },
    }
    return case, replay_payload


def _iter_batch_paths(directory: Path) -> Iterable[Path]:
    for path in sorted(directory.glob("*.json")):
        name = path.name
        if name.startswith("_"):
            continue
        if "template" in name.lower():
            continue
        if name == "case_demo_scoring.json":
            continue
        yield path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("golden", nargs="?", type=Path, help="Path to one golden JSON file.")
    parser.add_argument("-o", "--output", type=Path, help="Output eval case JSON path (single mode).")
    parser.add_argument("--batch", type=Path, help="Directory of golden JSON files to convert.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Output directory for batch mode (one <eval_case_id>.json per golden).",
    )
    parser.add_argument("--id-prefix", default="golden_pr_", help="Prefix for eval case id (default: golden_pr_).")
    parser.add_argument("--suite", default="golden-import", help="Primary suite label on the eval case.")
    parser.add_argument(
        "--extra-suites",
        nargs="*",
        default=[],
        help="Additional suite tags (e.g. ci-gate when you want this in CI).",
    )
    parser.add_argument(
        "--emit-replay-stub",
        action="store_true",
        help="Write wiki-shaped replay JSON next to fixtures and point fixtures.recorded_tools to it.",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=None,
        help="Directory under project root for replay stubs (default: <repo>/tests/fixtures).",
    )
    parser.add_argument(
        "--strict-case-example",
        action="store_true",
        help="Populate expectations.case_example.must_contain_any from golden text (stricter).",
    )
    parser.add_argument(
        "--max-latency-ms",
        type=float,
        default=None,
        help="Optional expectations.max_latency_ms (omit for replay+EVAL_DETERMINISTIC runs).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = _project_root()
    fixture_root = (args.fixture_root or (root / "tests" / "fixtures")).resolve()

    if args.batch:
        if not args.out_dir:
            print("--out-dir is required with --batch", file=sys.stderr)
            return 2
        out_dir = args.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        n_ok = 0
        for gpath in _iter_batch_paths(args.batch.resolve()):
            try:
                raw = load_golden(gpath)
                eid = _eval_case_id(raw, gpath, args.id_prefix)
                rel_tools: Optional[str] = None
                if args.emit_replay_stub:
                    stub_dir = fixture_root / eid
                    stub_dir.mkdir(parents=True, exist_ok=True)
                    rel_tools = str((stub_dir / "tools.json").relative_to(root))
                case, payload = build_eval_case_dict(
                    raw=raw,
                    path=gpath,
                    eval_case_id=eid,
                    suite=args.suite,
                    extra_suites=args.extra_suites,
                    emit_replay_stub=bool(args.emit_replay_stub),
                    recorded_tools_rel=rel_tools,
                    strict_case_example=bool(args.strict_case_example),
                    max_latency_ms=args.max_latency_ms,
                )
                out_path = out_dir / f"{eid}.json"
                out_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                if payload is not None and rel_tools:
                    (root / rel_tools).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel_tools).write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                n_ok += 1
                print(f"OK {gpath.name} -> {_display_path(out_path, root)}")
            except Exception as exc:
                print(f"SKIP {gpath.name}: {exc}", file=sys.stderr)
        print(f"Converted {n_ok} case(s) into {_display_path(out_dir, root)}")
        return 0

    if not args.golden or not args.output:
        print("Single mode requires GOLDEN path and -o/--output.", file=sys.stderr)
        return 2

    gpath = args.golden.resolve()
    raw = load_golden(gpath)
    eid = _eval_case_id(raw, gpath, args.id_prefix)
    rel_tools: Optional[str] = None
    if args.emit_replay_stub:
        stub_dir = fixture_root / eid
        stub_dir.mkdir(parents=True, exist_ok=True)
        rel_tools = str((stub_dir / "tools.json").relative_to(root))

    case, payload = build_eval_case_dict(
        raw=raw,
        path=gpath,
        eval_case_id=eid,
        suite=args.suite,
        extra_suites=args.extra_suites,
        emit_replay_stub=bool(args.emit_replay_stub),
        recorded_tools_rel=rel_tools,
        strict_case_example=bool(args.strict_case_example),
        max_latency_ms=args.max_latency_ms,
    )

    out_path = args.output.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if payload is not None and rel_tools:
        tools_path = root / rel_tools
        tools_path.parent.mkdir(parents=True, exist_ok=True)
        tools_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote replay stub: {_display_path(tools_path, root)}")

    print(f"Wrote eval case: {_display_path(out_path, root)} (id={eid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

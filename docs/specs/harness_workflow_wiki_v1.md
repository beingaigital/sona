# Harness + Workflow + Wiki V1 Implementation Spec

## 1. Background And Intent

This project already has a capable end-to-end public opinion analysis workflow, but it is still optimized mainly for "can run" instead of "can be verified, replayed, compared, and governed."

This spec upgrades the original lightweight spec into an implementation-facing v1 spec. It turns the plan into concrete engineering contracts for:

- workflow modularization
- `/wiki` commandization
- harness-based evaluation and replay
- budget governance
- schema contracts
- CI quality gates

Harness Engineering is treated as the controlling system outside the model. Models define the ceiling; harness defines the stability floor.

## 2. Goals

V1 goals:

- Make the workflow more modular without breaking current behavior.
- Make execution more reasonable for the public-opinion-analysis domain.
- Make data collection more stable and measurable.
- Make report generation deeper and less "prompt stuffing" driven.
- Make token usage, retries, and budget decisions observable.
- Make `/wiki` a high-frequency knowledge query and learning interface.
- Make improvements regression-testable with replay and CI gates.

## 3. Scope And Non-Goals

### In Scope

- Incremental refactor of `cli/event_analysis_workflow.py`
- Harness MVP: runner, cases, fixtures, replay, scorer, results artifacts
- `/wiki` MVP with answer and source citation contract
- Tool and output schema validation for key paths
- Cost and latency breakdown per run/stage
- Initial CI-ready quality gate design

### Out Of Scope

- Large-scale model strategy rewrite
- Full historical data migration
- Heavy visual regression infrastructure
- Complex online experiment platform

## 4. Target Project Structure

The project should evolve toward the following structure while keeping current files usable during transition:

```text
cli/
  event_analysis_workflow.py      # legacy entry, gradually reduced to thin orchestration shell
  main.py                         # interactive commands, later adds /wiki

workflow/
  runner.py                       # stage orchestration only
  collab.py                       # human-in-the-loop interactions
  collect_resilience.py           # collection retry, fallback, quality gates
  graph_enrichment.py             # graph/wiki/reference enrichment logic
  reuse.py                        # plan/data reuse and LTM matching
  telemetry.py                    # trace, metrics, budget events
  contracts.py                    # WorkflowContext / StageResult / shared contracts

tools/
  *.py                            # domain tools remain here

tests/
  evals/
    runner.py
    replay.py
    cases/
    scorers/
    schema/
  fixtures/

docs/
  specs/
    harness_workflow_wiki_v1.md

eval_results/
  <run_id>/
```

## 5. Module Boundaries

### 5.1 `cli/event_analysis_workflow.py`

Responsibilities:
- legacy-compatible entrypoint
- parameter intake
- invoke top-level workflow runner
- fatal exception handling

Must not keep growing with:
- large prompt assembly
- reusable stage business logic
- rich telemetry logic
- replay/eval logic

### 5.2 `workflow/runner.py`

Responsibilities:
- execute ordered stages
- manage stage transitions
- write stage-level status into shared context

Must not:
- implement tool internals
- own UI rendering logic
- contain large prompt strings

### 5.3 `workflow/telemetry.py`

Responsibilities:
- trace events
- metrics snapshots
- token/cost/budget event logging

Must not:
- perform business decisions
- own workflow branching

### 5.4 `tools/*.py`

Responsibilities:
- single tool capability
- structured input/output
- explicit error object

Must not:
- orchestrate multi-stage workflow policy
- silently change output schema without versioning/adapter

### 5.5 `tests/evals/*`

Responsibilities:
- deterministic evaluation execution
- fixture replay
- scoring
- result artifact generation

Must not:
- mutate production state
- depend on interactive input

## 6. Harness Module Mapping

This spec uses eight module lenses:

- Prompt
- Context
- Tool
- RAG
- Memory
- Eval
- Obs
- UI

### Prompt
- prompt assets should become versionable and traceable
- key prompt version should be emitted in run artifacts

### Context
- report context must become budgeted and layered
- final generation should move toward distilled canonical inputs instead of full raw dumps

### Tool
- tool outputs should be schema-checked
- critical tools should emit structured quality metadata

### RAG
- retrieval outputs must be explainable: `title/path/snippet/score`
- `/wiki` retrieval should become replayable and evaluable

### Memory
- workflow state and reusable memory should be separated
- `WorkflowContext` should become the primary runtime state contract

### Eval
- every major improvement must be testable in fixed suites

### Obs
- every major run should produce cost and latency breakdowns

### UI
- CLI and HTML should have structural acceptability checks

## 7. Core Contracts

### 7.1 WorkflowContext

`WorkflowContext` is the shared runtime contract across workflow stages.

Minimum logical fields:

```python
{
  "run_id": str,
  "task_id": str | None,
  "query": str,
  "mode": "live" | "replay",
  "stage_outputs": dict,
  "artifacts": dict,
  "diagnostics": dict,
  "budget": dict,
  "policy": dict,
  "errors": list
}
```

Required guarantees:
- every stage reads from and writes to this contract
- stage output keys are stable and documented
- failures are structured, not free-form only

### 7.2 StageResult

Recommended stage result shape:

```python
{
  "status": "success" | "warning" | "failed" | "skipped",
  "stage": str,
  "metrics": dict,
  "artifacts": dict,
  "error": dict | None,
  "fallback_used": bool
}
```

### 7.3 Tool Error Object

All critical tools should gradually converge toward:

```python
{
  "error_code": str,
  "error_message": str,
  "retryable": bool,
  "result_file_path": str | None
}
```

### 7.4 Wiki Answer Contract

Minimum output:

```json
{
  "answer": "string",
  "sources": [
    {
      "title": "string",
      "path": "string",
      "snippet": "string",
      "score": 0.0
    }
  ]
}
```

If retrieval evidence is insufficient:
- answer should explicitly say evidence is insufficient
- no unsupported factual certainty should be generated

### 7.5 Eval Case Schema

Current case protocol:

```json
{
  "id": "wiki_concept_001",
  "target": "wiki",
  "stage": null,
  "input": {},
  "fixtures": {
    "mode": "replay",
    "recorded_tools": "tests/fixtures/.../tools.json"
  },
  "expectations": {
    "required_fields": [],
    "thresholds": {}
  }
}
```

### 7.6 Eval Result Schema

Result protocol:

```json
{
  "run_id": "string",
  "case_id": "string",
  "target": "workflow|tool|wiki",
  "status": "pass|fail|warning",
  "metrics": {},
  "artifacts": {
    "trace": "path",
    "output": "path"
  },
  "fail_reasons": []
}
```

### 7.7 Trace Event Schema

Minimum event shape:

```json
{
  "ts": "ISO8601",
  "event": "case_start|case_end|tool_call|tool_result|budget_action|stage_start|stage_end",
  "run_id": "string",
  "case_id": "string|null",
  "stage": "string|null",
  "payload": {}
}
```

### 7.8 Prompt Budget Breakdown Schema

For report-heavy calls, emit:

```json
{
  "run_id": "string",
  "stage": "report",
  "sections": [
    {"name": "analysis_results", "chars": 0, "priority": 1, "trimmed": false},
    {"name": "references", "chars": 0, "priority": 2, "trimmed": true}
  ],
  "estimated_tokens": 0,
  "budget_triggered": false
}
```

## 8. Runtime Modes

### Live Mode

- real tools and external dependencies are allowed
- suitable for true quality validation
- results may vary within allowed bounds

### Replay Mode

- uses recorded fixture outputs
- must be deterministic for the same fixture set
- preferred for regression and local development loops

### Guarantees

- replay mode should never require live external access
- replay mode must preserve structure, artifact shape, and scoring stability
- live mode must still obey schema and budget rules

## 9. Evaluation Protocol

### 9.1 Targets

Supported targets:
- `workflow`
- `tool`
- `wiki`

### 9.2 Core Metrics

Initial metrics:
- `traceability_score`
- `relevance_score`
- `structure_completeness`
- `latency_ms`
- `fallback_rate`

Planned domain metrics:
- `stage_path_validity`
- `domain_coverage_score`
- `collect_success_rate`
- `unique_ratio`
- `missing_rate`
- `retry_effectiveness`
- `report_section_coverage`
- `evidence_density`
- `argument_chain_score`
- `actionability_score`
- `html_parse_success`
- `layout_sanity_checks`

### 9.3 Pass/Fail Rules

- hard metric failures produce `fail`
- soft metric misses may produce `warning`
- suite pass requires pass-rate threshold and no blocker failures

### 9.4 Vertical Domain Suites

The harness should evolve toward fixed public-opinion suites, including:
- sudden incident
- reversal
- rumor clarification
- public safety
- controversy escalation

## 10. Budget Governance

Budgets are mandatory control surfaces, not optional diagnostics.

Governed dimensions:
- prompt tokens
- completion tokens
- latency
- retry count
- optional enrichment count

Degrade order:
1. trim optional context
2. skip optional enrichment
3. model downgrade if explicitly allowed

Governance artifacts:
- budget trigger count
- cost breakdown by stage
- degrade reason and action

## 11. Data Quality Contracts

For collection-stage outputs, quality should become testable.

Planned metrics:
- minimum row count
- duplicate ratio
- missing key-field ratio
- time window coverage
- retry effectiveness

Collection outputs should gradually include explicit metadata that supports these checks.

## 12. Report Quality Contracts

Report quality should not be judged only by subjective reading.

Planned report contracts:
- required sections exist
- HTML is parseable
- evidence appears in report
- argument chain is not empty
- recommendations are present when expected

Long-term direction:
- move from raw JSON stuffing to distilled canonical report input

## 13. UI Acceptance

### CLI

CLI output should be:
- stage-clear
- concise
- traceable to current command path

### HTML

Minimum structural checks:
- required anchors/sections
- valid parseable HTML
- no missing critical cards/modules

Visual regression is optional and deferred; structural regression is required in V1.

## 14. Observability

Every meaningful run should be inspectable through artifacts.

Required artifact categories:
- `summary.json`
- `metrics.json`
- `trace.jsonl`
- `output.json`
- future `cost_breakdown.json`
- future `prompt_budget_breakdown.json`

Observation should support:
- per-stage latency
- per-stage usage
- fallback visibility
- degrade visibility
- before/after run comparison

## 15. CI Gates

Minimum CI gate for V1:
- smoke suite
- key wiki cases
- key workflow/report contract checks
- schema contract checks

Blocking conditions:
- missing required output fields
- hard metric threshold failure
- blocker-level replay inconsistency
- invalid schema for critical outputs

## 16. Milestones And Acceptance

Milestones follow the Day1-Day10 checklist in the plan.

V1 is accepted when:
- eval runner emits standard artifacts
- at least 5 seed cases exist
- replay mode is usable for key paths
- critical outputs satisfy schema checks
- CI can fail on blocker-level regressions
- workflow modularization has begun with clear boundaries

## 17. Relationship To The Plan

This spec defines the implementation contracts.

The plan defines:
- sequencing
- priorities
- execution phases
- daily checklist

When there is ambiguity:
- use the plan for sequencing
- use this spec for contracts and acceptance criteria

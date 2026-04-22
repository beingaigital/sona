# Report Meta V1 Contract

This document defines the minimal `report_meta` contract that **real workflow/report generation** must produce so that evaluation harness (`workflow_report_005`) can verify report acceptability beyond "files exist".

The goal of `report_meta` is not to judge style; it is to expose **machine-checkable signals** for:
- structural completeness (sections exist)
- evidence / references presence
- depth signals (analogous cases, public-opinion patterns, theory integration)

## 1. Where It Lives

The report stage should output:
- an HTML report file (human-readable)
- a `report_meta.json` file (machine-readable)

Recommended artifact naming:
- `report_<timestamp>.html`
- `report_meta_<timestamp>.json`

## 2. JSON Schema (V1)

### 2.1 Top-level shape

```json
{
  "version": "v1",
  "generated_at": "ISO8601",
  "sections": ["summary", "timeline", "analysis", "recommendations"],
  "references_count": 0,
  "has_summary": true,
  "has_timeline": true,
  "has_recommendations": true,
  "has_analogous_cases": false,
  "analogous_cases_count": 0,
  "has_public_opinion_patterns": false,
  "pattern_points_count": 0,
  "has_theory_analysis": false,
  "theory_frameworks": []
}
```

### 2.2 Field definitions

- **version** (`string`, required): fixed to `"v1"`.
- **generated_at** (`string`, required): ISO8601 timestamp.

- **sections** (`string[]`, required): normalized section keys present in the report. V1 uses:
  - `summary`
  - `timeline`
  - `analysis`
  - `recommendations`
  Additional keys are allowed, but these four should be present for a "complete" report.

- **references_count** (`number`, required): count of references/citations detected (heuristic-based in v1).

#### Required section flags
- **has_summary** (`boolean`, required)
- **has_timeline** (`boolean`, required)
- **has_recommendations** (`boolean`, required)

#### Depth signals (domain-specific)
- **has_analogous_cases** (`boolean`, required): whether the report includes analogous/similar cases.
- **analogous_cases_count** (`number`, required): how many analogous cases are present (heuristic).

- **has_public_opinion_patterns** (`boolean`, required): whether the report includes synthesized public-opinion patterns /规律研判.
- **pattern_points_count** (`number`, required): number of explicit pattern points (heuristic).

- **has_theory_analysis** (`boolean`, required): whether the report integrates communication theory /传播理论.
- **theory_frameworks** (`string[]`, required): detected named frameworks (e.g., `"议程设置"`, `"沉默的螺旋"`).

## 3. How To Produce (V1 Heuristics)

V1 is allowed to use **heuristics** (string matches) to avoid heavy dependencies:

- **sections**:
  - detect by presence of known headings/anchors in HTML
  - normalize to the 4 keys above

- **references_count**:
  - count unique URLs in the HTML (`http://` or `https://`)
  - fallback: count occurrences of keywords like `来源:` / `参考` if URLs are absent

- **analogous cases**:
  - keyword match: `相似案例` / `同类事件` / `历史案例` / `案例对比`

- **public-opinion patterns**:
  - keyword match: `规律` / `综合研判` / `演化路径` / `舆情生命周期`

- **theory integration**:
  - keyword match against a dictionary (extensible):
    - `议程设置`, `沉默的螺旋`, `框架理论`, `两级传播`, `信息茧房`, `群体极化`

## 4. Backward Compatibility

Report generation tool output must remain backward compatible:
- existing keys must not be removed
- `report_meta_file_path` is added
- `report_meta` may be embedded as an optional convenience

## 5. Relationship To Harness

Harness (`tests/evals/scorers/core.py`) expects `report_meta` to exist in the report-stage output shape (or be loadable from the artifact path once integrated). V1 starts by embedding `report_meta` into the tool output for easy adoption; later versions may read it from file.


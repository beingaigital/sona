# Fixtures Guide

`tests/fixtures/<case_id>/` holds replay payloads for eval cases. Many cases use `tools.json`; some tools use `output.json` — the path must match `fixtures.recorded_tools` in the case JSON.

Guidelines:

- Keep payloads small but structurally correct.
- Prefer deterministic values for replay stability.
- Include only fields used by expectations/scorers.
- If schema evolves, update case expectations and fixture together.

Checklist when adding or changing a fixture:

1. Case `id` matches the fixture directory name (recommended).
2. `fixtures.recorded_tools` points to the file you edited (repo-relative).
3. `expectations` still match the payload (required fields, thresholds, report_meta paths).
4. Run `python scripts/eval_runner.py --case <id> --mode replay` (and `EVAL_DETERMINISTIC=1` if comparing diffs).

Current Day2 baseline includes:

- `wiki_concept_001`
- `wiki_case_002`
- `wiki_compare_003`
- `workflow_sentiment_004`
- `workflow_report_005`
- `workflow_report_006_warning_baseline`


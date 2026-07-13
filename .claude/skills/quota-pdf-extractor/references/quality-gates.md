# Quota PDF Extraction Quality Gates

## Mandatory Gates

1. **Source gate**
   - Source file is registered.
   - File hash matches.
   - Rights/source status is known.
   - Publish/import is blocked unless rights are cleared.

2. **Structure gate**
   - Expected volume/root chapters are present.
   - Chapter and section hierarchy is valid.
   - Parent/path references are not orphaned.
   - Rule scopes bind to dataset/chapter/section/quota item records.

3. **Quota item gate**
   - Quota codes match the expected volume pattern.
   - Item names are not empty.
   - Units are present when visible.
   - Base price raw and normalized values are separated.
   - Continuation pages inherit chapter only with a review warning.

4. **Table integrity gate**
   - Multiple tables on one page are split before cell assignment.
   - Cost summary rows do not become resource rows.
   - Resource rows do not become cost summary rows.
   - Child rows point to existing quota item records.
   - Repeated columns do not bleed across adjacent tables.

5. **Numeric gate**
   - Preserve `*_raw` exactly.
   - Normalize only with field-aware rules.
   - Amount repairs must add warnings.
   - Do not apply amount decimal repair to resource consumption.

6. **Review gate**
   - Candidate release contains review-required records.
   - Human review decisions are explicit.
   - Reviewed release is generated as a new immutable artifact.
   - Publish cache is produced only from reviewed/approved releases.

## OCR Risk Patterns

Track suspicious title/text drift as review findings, not silent corrections. Common Chinese OCR confusions include:

- `土` vs `士`;
- `混凝土` vs `混凝士`;
- simplified/traditional wall/board variants such as `墙/墻`, `板/闆`;
- dropped decimal points in cost amounts;
- decimal point recognized as comma;
- row labels split into single characters, such as `管` + `费`.

## Numeric Normalization Guidance

Apply numeric repair only after identifying the field semantic:

- `management` and `profit` cost amounts usually use two decimal places; candidates like `14482` may be normalized to `144.82` with warning.
- Values like `2217,57` in amount fields may be normalized to `2217.57` with warning.
- `1,234` should be treated as thousands separator, not `12.34`.
- Resource consumption can have three or more decimals and must not use amount repair rules.
- `12O.00` with letter `O` should remain invalid/review-required unless an OCR correction layer with evidence exists.

## Reporting

Each parse job should produce:

- JSON quality report for systems;
- Markdown quality report for humans;
- counts by record type/status;
- coverage metrics for chapters, rules, work contents, quota items, resources, cost lines;
- blocking reasons and non-blocking review warnings;
- sample evidence for severe classes of errors.

## Workbench Integration

Create review items for:

- structural failures;
- missing required fields;
- suspicious OCR drift;
- numeric repair candidates;
- orphan scopes;
- low-confidence source pages;
- records that cannot be confidently mapped to chapters/sections.

## Checksum Gate (Gate 7, added for cross-field validation)

Quota books carry a built-in checksum: printed base price ≈ Σ(resource consumption × appendix listed unit price), plus management/profit per the book's cost scope.

Run per quota item after resource alignment:

1. Compute `calc_base = Σ(quantity × appendix_price[resource])` (+ management/profit if `cost_scope = comprehensive`).
2. `|calc_base − base_price_printed| ≤ 0.05` (rounding tolerance) → checksum PASS.
3. On failure, localize before flagging: verify labor line first (fewest variables), then materials, then machinery. Emit a review finding with the suspected field, page, and bbox crop.

Uses of this gate:

- Upgrades numeric repair from "guess with warning" to "verifiable decision": if repairing `14482 → 144.82` makes the checksum balance, the repair is confirmed (still keep `*_raw` and the warning trail).
- Validates consumption, unit price, and resource alignment simultaneously — a checksum pass means all three are consistent.
- Book-level `checksum_pass_rate` is a release readiness metric; target ≥ 99% before review handoff.

Note: parenthesized consumption values (e.g. fuel shown as `(24.718)`) are reference-only and excluded from the checksum sum.

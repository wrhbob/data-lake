---
name: quota-pdf-extractor
description: Extract quota/定额 PDFs into auditable standard quota libraries. Use when working on raw quota PDF upload, OCR/table parsing, parser profiles, chapter/rule extraction, canonical JSONL releases, source/provenance checks, human review workbenches, quality reports, or standard_quota import preparation for construction cost quota books such as 四川 2025 定额 / 省级计价定额 / 消耗量定额. Also use when implementing modules of the quota data lake spec (lake/parser/assets/pricing/apply).
---

# Quota PDF Extractor

## Core Rule

Treat quota PDFs as layout-critical source documents. Do not rely on plain OCR text for quota tables. Use:

```text
source PDF -> page render -> OCR bbox/cell evidence -> profile-specific parsing -> canonical JSONL -> quality report -> human review -> reviewed release -> import cache -> standard library
```

Never write candidate or unreviewed parse results directly into formal `standard_quota_*` tables.

## First Reads

Load only the references needed for the current task:

- For end-to-end PDF import or parser design, read `references/workflow-contract.md`.
- For validation, readiness, audit, or human review tasks, read `references/quality-gates.md` (includes the cross-field checksum gate).
- For Sichuan 2025 or similar dense quota-table PDFs, read `references/sc2025-lessons.md`.

If working inside the Xiaojiang repo, also inspect the local SPEC files before changing behavior:

- `cost_price/specs/10-assets/SPEC-011-source-file-registry.md`
- `cost_price/specs/10-assets/SPEC-012-parsed-quota-schema.md`
- `docs/03-module-specs/system-settings/11-standard-library-import-standard.md`
- `docs/03-module-specs/system-settings/12-standard-quota-schema.md`

If the repo contains 定额数据湖全链路技术规格 (quota data lake spec), treat this skill and the spec as two layers of one system:

- This skill governs the parse artifact discipline (evidence, immutable releases, review gates).
- The spec governs the target data model and runtime (量价分离, 工序桥, 省级 Profile, 套定额).
- Candidate/reviewed JSONL releases are the Layer 1 immutable artifacts; the spec's database tables are the serving layer loaded only from reviewed releases; the spec's `usable` state requires this skill's import gate to pass.
- Consumption quantities are the quota's essence (量价分离): printed prices go to `qa_printed_price`/checksum only, never into business price fields.

## Workflow

1. **Register source files**
   - Compute file hashes and record source metadata.
   - Preserve raw files as immutable input.
   - Track source rights/status separately from parse quality.

2. **Profile the layout**
   - Split frontmatter and quota-table parsing.
   - Frontmatter profile handles contents, chapter hierarchy, notes, section rules, calculation rules, and scope binding.
   - Quota-table profile handles quota codes, names, units, base price, cost summary rows, resource rows, conversion rows, and evidence.
   - For a new province/year/layout, build a small page POC before full-book parsing.
   - Province/year differences live only in declarative profile classes (patterns, cost_scope, cost row labels, unit position, watermark chars) — never in core parser code.

3. **Parse with bbox/cell evidence**
   - Prefer OCR/table engines that emit cell bbox, HTML, table JSON, or line bbox.
   - Reject pure text-only OCR as insufficient for dense quota tables.
   - Split multiple quota tables on a page before assigning cells to columns.
   - Preserve raw value and normalized value separately.

4. **Emit canonical release**
   - Write canonical JSONL files for chapters, quota_items, resource_lines, cost_lines, conversion records, rules, and corrections.
   - Mark every parser-derived record as candidate/review_required until reviewed.
   - Include manifest, provenance, parser profile version/hash, OCR engine, source pages, evidence, warnings, and validation report.

5. **Audit before review**
   - Run structural checks, field coverage checks, OCR-risk checks, scope orphan checks, and consistency checks.
   - Run the checksum gate (Σ consumption × appendix price vs printed base price) — see quality-gates Gate 7.
   - Produce machine-readable JSON and human-readable Markdown quality reports.
   - Feed findings into the review workbench; do not hide parser warnings.

6. **Human review and release**
   - Generate reviewed releases by applying accept/correct/reject decisions over the immutable candidate release.
   - Do not mutate the original candidate output.
   - Only reviewed/approved releases may generate publish-intended import cache.

7. **Import preparation**
   - Build import cache from reviewed release only.
   - Gate import on source rights, parsed release status, no blocking audit findings, and review completion.
   - Keep import cache distinct from the final standard library tables.

## Red Lines

- Do not silently correct OCR values without retaining `*_raw`, normalized value, warning, and evidence.
- Do not apply numeric repair rules globally. Amounts, quantities, rates, prices, and resource consumptions have different semantics.
- Never apply amount repair rules to resource consumption values; consumption is the quota's essence and a checksum input.
- Do not merge frontmatter and quota table logic into one parser profile.
- Do not add province/year-specific hacks to general parsing code without first isolating them in a parser profile.
- Do not make parser output look approved. Human review is a separate gate.
- Do not remove source coordinates, page numbers, bbox evidence, warnings, or provenance for compactness.
- Do not write printed base prices into business price fields; prices are computed from consumption × price period (量价分离).

## Implementation Bias

Prefer narrow, testable parser improvements:

- Add a failing fixture from a real page.
- Fix one parse rule.
- Keep raw evidence.
- Add a warning when confidence is not high enough for automatic normalization.
- Re-run targeted pages and then the full configured batch.

When the parser is wrong, prefer improving profile/table structure over adding downstream cleanup.

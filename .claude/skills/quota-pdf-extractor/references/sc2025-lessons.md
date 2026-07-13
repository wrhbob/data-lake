# Sichuan 2025 Quota PDF Lessons

## Main Lessons

Sichuan 2025 proved that quota extraction is a profile-driven table-structure problem, not a plain OCR problem.

## Frontmatter

The frontmatter needs its own profile:

- contents pages may be two-column;
- chapter entries need root/child hierarchy;
- chapter and section notes must become structured records;
- total notes and calculation rules need scoped rule records;
- source pages must be visible in the review UI.

Do not parse frontmatter with quota item logic.

## Quota Tables

Important table parser rules:

- Detect quota code rows first.
- Split multiple tables on the same page before assigning values.
- Use column bounds per table, not page-global guessing.
- Extract work content near the code/table block.
- Extract unit near the table block.
- Assign name/feature rows between base price row and code row.
- Cost summary rows appear before resource header rows.
- Resource mode starts only after a resource header.

This avoids known failures such as AA0005 and AA0007 values bleeding into each other on the same page.

## Numeric OCR

Observed OCR issues:

- `144.82` recognized as `14482`;
- decimal point recognized as comma, e.g. `2217,57`;
- row labels split across cells, e.g. `管` + `费`;
- aggregate rows misclassified as resources.

Fix numeric values by field semantic:

- management/profit amount candidates may use two-decimal repair with warnings;
- raw OCR values must remain in `amount_raw`;
- resource consumption must not use amount repair.

## Chapter and Rule Audit

Run frontmatter audit after full-book parsing:

- expected root chapters;
- duplicate/invalid chapter codes;
- missing parent/path;
- orphan rule scopes;
- suspicious OCR title patterns.

Audit findings should feed the asynchronous parse quality report and review workbench.

## UI Review Needs

A useful quota review UI should show:

- left chapter/section tree;
- top-right quota item list;
- bottom-right selected quota detail;
- chapter detail mode when a chapter is selected;
- chapter/section notes, calculation rules, source pages, and audit risks.

## What to Do for the Next Province/Year

1. Register raw PDF sources.
2. Render 10-30 representative pages.
3. Build frontmatter and quota-table profile separately.
4. Run a table-structure OCR POC with bbox/cell output.
5. Add fixtures for the first wrong page before changing parser logic.
6. Parse one volume, inspect quality report, then scale to all volumes.
7. Keep candidate output separate from reviewed release and formal import.

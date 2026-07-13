# Quota PDF Extraction Workflow Contract

## Scope

Use this contract when turning a raw construction quota PDF into an auditable parsed quota library.

## Pipeline

```text
Upload
  -> SourceFile registry (SPEC-011)
  -> OCR/render evidence
  -> parser profile
  -> candidate parsed release (SPEC-012)
  -> quality reports
  -> human review decisions
  -> reviewed parsed release
  -> publish-intended import cache
  -> formal standard_quota import
```

## Source Registration

Every source file needs:

- dataset/province/year/volume identity;
- original filename and storage path;
- SHA-256 or equivalent immutable digest;
- page count where available;
- rights/source verification status;
- ingestion timestamp and operator/system provenance.

Parsing may start before rights review, but publish/import must remain blocked until source rights are cleared.

## Profile Split

Maintain at least two profile classes:

- `frontmatter`: cover, contents, total notes, chapter notes, section notes, work contents, calculation rules, and scope binding.
- `quota_table`: quota code rows, names/features, units, base price rows, cost summary rows, resources, conversion records.

Do not force contents/notes/rules through the quota-table parser.

## OCR/Table Engine Contract

An OCR/table candidate is useful only if it can provide enough structure for cell assignment:

- line or cell text;
- bbox coordinates in page space;
- confidence where available;
- page number;
- stable mapping back to source page image.

Engines that only return plain text can help search or preview, but are not sufficient for formal quota item extraction.

## Candidate Release Rules

Candidate releases must be immutable and review-required by default. Store:

- `chapters.jsonl`;
- `quota_items.jsonl`;
- `resource_lines.jsonl`;
- `cost_lines.jsonl`;
- `conversion_groups.jsonl`;
- `conversion_options.jsonl`;
- `rules.jsonl`;
- `corrections.jsonl`;
- `manifest.json`;
- validation/audit reports.

Every business record should carry:

- `source_id`, `file_id`, source file name;
- page and row/source span when known;
- bbox/evidence lines or cells;
- raw values and normalized values;
- warnings;
- parse/review status.

## Reviewed Release Rules

Build reviewed releases by applying review decisions over the candidate release:

- `accepted`: keep candidate record with review metadata;
- `corrected`: shallow/controlled merge of correction payload, preserving original evidence;
- `rejected`: exclude record and write correction/rejection trail;
- child records inherit review state from rejected/accepted quota items when appropriate.

Do not overwrite the original candidate release.

## Standard Library Import

The standard library import should consume only a reviewed release or a publish-intended cache generated from it. The importer should reject:

- candidate/review_required records;
- missing source rights clearance;
- orphan child rows;
- blocking validation errors;
- unreviewed corrections.

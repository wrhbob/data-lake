---
name: cost-info-ingestion
description: Ingest construction cost information PDFs from government websites into the data lake. Use when downloading 造价信息/信息价 PDFs from住建局 websites, registering archive records, or troubleshooting missing year/month filters in the UI.
---

# Cost Info Ingestion

## Core Rule

Every cost_info PDF must survive the full ingestion pipeline:
download → MinIO → Blob → FileAsset → IngestEvent → Archive → ArchiveFile.

A file on disk is invisible to the UI. Only database-registered archives appear in the "信息价档案台".

## Region Code — The #1 Pitfall

**ALWAYS use 6-digit GB/T 2260 codes** matching the frontend `regionTree` city values.

| ❌ Wrong | ✅ Correct | City |
|---------|-----------|------|
| `"1501"` | `"150100"` | 呼和浩特市 |
| `"1502"` | `"150200"` | 包头市 |

The frontend `archiveMatchesCity()` checks `itemCode === cityCode` for exact string equality. The regionTree stores 6-digit codes (e.g., `"150100"`, `"150200"`). A 4-digit code like `"1501"` fails this check, excluding the entry from city-scoped dimension indexes. Result: year/month filter dropdowns are empty.

**Applies to:**
- `Archive.region_code`
- `Archive.coverage_region_code`
- `DataSource.region_code`
- `metadata.coverage_region_code`

## Mandatory Metadata Fields

For the year/month filters to work, these metadata fields MUST be populated:

| Field | Required For | Format | Example |
|-------|-------------|--------|---------|
| `period_raw` | "期次" column display | free text | `"2026年6月份"` |
| `period_start` | year/month filter extraction | `YYYY-MM` | `"2026-06"` |
| `period_year` | year filter fallback | `YYYY` | `"2026"` |
| `period_issue_no` | issue_based only | `"1"` | `"1"` |
| `coverage_region_code` | region scoping | 6-digit code | `"150200"` |
| `publisher` | "发布主体" column | org name | `"包头市住房和城乡建设局"` |

## Period Kind — Match the Existing Convention

The `period_kind` field affects how the frontend's `costInfoPeriodInfos()` parses year/month:

| period_kind | Parser Used | Required Metadata |
|-------------|-------------|-------------------|
| `monthly` (default) | `yearMonthParts()` | `period_start` in `YYYY-MM` |
| `issue_based` | `issueParts()` | `period_year` + `period_issue_no` |

**Do NOT use `bimonthly`.** The frontend has no code path for it. All ~900 existing cost_info entries use either `monthly` or `issue_based`. Map bimonthly publications to `monthly` and set `period_start` to the first month of the range (e.g., "2025年第3期(5-6月)" → `period_start: "2025-05"`).

## Database Columns — Don't Forget `coverage_period`

The `Archive.coverage_period` column (String(7), format `YYYY-MM`) must be backfilled after archive creation:

```python
archive.coverage_period = issue["period_start"]  # e.g., "2026-06"
```

This column is used by the coverage gap analysis UI. Leaving it NULL won't break the list view but will produce gaps in the coverage matrix.

## Ingestion Script Template

Use the existing `register_asset()` + `create_archive_from_ingest_event_with_flag()` pipeline. Do NOT reimplement MinIO upload or Blob/FileAsset creation manually.

```python
from app.assets import register_asset
from app.archive_rules import build_cost_info_business_key
from app.archive_service import create_archive_from_ingest_event_with_flag
from app.storage import get_object_store

store = get_object_store()
reg = register_asset(session, storage=store, ...)
archive, is_new = create_archive_from_ingest_event_with_flag(
    session,
    event_id=reg.ingest_event_id,
    domain_type="cost_info",
    period_kind="monthly",          # NOT bimonthly
    region_code="150200",           # 6-digit only
    metadata={
        "period_start": {"value": "2026-06"} | source,
        "period_year":  {"value": "2026"}    | source,
        ...
    },
    ...
)
archive.coverage_period = "2026-06"  # Backfill
session.commit()
```

## Verification Checklist

After ingestion, verify in the database:

- [ ] `Archive.region_code` is 6-digit and matches `regionTree` city code
- [ ] `Archive.period_kind` is `monthly` or `issue_based`
- [ ] `Archive.coverage_period` is populated (`YYYY-MM`)
- [ ] `metadata.period_start` exists and matches `YYYY-MM` format
- [ ] `metadata.period_year` exists
- [ ] `metadata.coverage_region_code` exists and uses 6-digit code
- [ ] `DataSource.region_code` matches the Archive region_code
- [ ] `ArchiveFile` records exist and link to `FileAsset`

Then refresh the browser (Ctrl+F5), select the correct province/city in the region filter, and verify year/month dropdowns show options.

## Common Government Website Patterns

### 呼和浩特 (zfcxjsj.huhhot.gov.cn)
- Bimonthly publications (6 issues/year)
- Text-based PDFs (not scanned)
- Material code + tax-included/excluded prices
- Published under 办事服务 → 下载中心 → 造价信息

### 包头 (116.114.161.151:200)
- Monthly publications
- Published under 政府信息公开 → 法定主动公开内容 → 数据开放
- IP-based internal access (may differ from public DNS)

### URL Pattern
```
{SITE}/.../{YEAR}{MONTH}/t{YYYYMMDD}_{DOCID}.html  → article page
{SITE}/.../{YEAR}{MONTH}/P{20-digit}.pdf            → PDF download
```

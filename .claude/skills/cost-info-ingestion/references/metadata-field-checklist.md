# Metadata Field Checklist for Cost Info Ingestion

## Root Cause of "年份/月份 Filter Empty"

The frontend builds a **dimension index** (`costInfoDimensionIndex`) from all cost_info archives. The index groups entries by `(publisherOrg, province, city)` scope keys. For each entry, `costInfoPeriodInfos()` extracts year/month info from metadata.

Two conditions must hold for year/month filters to show values:

1. **Region match**: `archiveMatchesCity(item, cityCode)` returns true. Requires `item.region_code === cityCode` OR `region_code` starts with the city prefix. The city codes in `regionTree` are 6-digit (e.g., `"150100"`, `"150200"`).

2. **Period extraction**: `costInfoPeriodInfos()` returns `{year, month}` or `{year, issueNo}`. For this, the format of `period_start` metadata must be parseable by `yearMonthParts()` (accepts `YYYY-MM` or `YYYY年MM月`).

### Debugging Recipe

```sql
-- Check if region_code matches regionTree
SELECT region_code, title FROM archive
WHERE domain_type = 'cost_info' AND region_code LIKE '150%';

-- Check if period metadata is extractable
SELECT title, period_kind,
    metadata->'period_raw'->>'value' as raw,
    metadata->'period_start'->>'value' as start,
    metadata->'period_year'->>'value' as year,
    coverage_period
FROM archive WHERE region_code = '150100';
```

### What `yearMonthParts()` Accepts

```javascript
// Matches:
"2026-06"          → {year: "2026", month: "06"}
"2026年6月"        → {year: "2026", month: "06"}
"2026年06月份"     → {year: "2026", month: "06"}

// Does NOT match:
"2026年第3期(5-6月)" → null (the "第3期" breaks the regex)
"2026年6月份包头工程造价信息" → null (too much text after 月)
```

### What `issueParts()` Accepts

```javascript
// Matches:
"2026年第1期" → {year: "2026", issueNo: 1}

// Does NOT match:
"2026年第1期建设工程造价信息" → null
```

## Complete Field Reference

### Archive.columns (database)

| Column | Type | Required | Notes |
|--------|------|----------|-------|
| `region_code` | String(32) | YES | 6-digit GB/T 2260 code |
| `period_kind` | String(32) | YES | `monthly` or `issue_based` only |
| `coverage_period` | String(7) | YES | `YYYY-MM` format |
| `publish_date` | Date | YES | Publication date |
| `price_kind` | String(32) | YES | Usually `guidance` for 信息价 |
| `domain_type` | String(64) | YES | Always `cost_info` |

### metadata_payload (JSONB)

| Key | Type | Required | Example |
|-----|------|----------|---------|
| `period_raw` | `{value, source_level, tagged_by, tagged_at}` | YES | `{"value": "2026年6月份"}` |
| `period_start` | same | YES | `{"value": "2026-06"}` |
| `period_year` | same | YES | `{"value": "2026"}` |
| `period_issue_no` | same | issue_based only | `{"value": "1"}` |
| `coverage_region_code` | same | YES | `{"value": "150200"}` |
| `publisher` | same | YES | `{"value": "包头市住房和城乡建设局"}` |
| `publisher_scope` | same | YES | `{"value": "city"}` |
| `publisher_region_code` | same | YES | `{"value": "150200"}` |
| `price_source_type` | same | YES | `{"value": "info_price"}` |

### DataSource (database)

| Column | Value |
|--------|-------|
| `region_code` | Same 6-digit as Archive |
| `source_type` | `info_price` |
| `data_domain` | `cost_info` |
| `connector_type` | `manual_upload` |

## Existing Registration Scripts

| Script | City | Region Code |
|--------|------|-------------|
| `app/huhehaote_cost_info_register.py` | 呼和浩特 | 150100 |
| `app/baotou_cost_info_register.py` | 包头 | 150200 |

These scripts serve as templates for adding new cities. Copy the latest one, update the ISSUES list and REGION_CODE.

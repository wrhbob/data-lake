"""Controlled vocabulary for the 清单定额档案台 (SPEC-QA-001 · domain_type=quota).

All *database* enum values use the repository's lowercase style; the uppercase
tokens in the SPEC are business semantics only (mandatory revision #9). Open
dictionaries (industry_sector / discipline) are stored in ``quota_dictionary``
and are NOT enforced via CHECK constraints, so they can grow from real data
without a migration; the fixed enums below back CHECK constraints.
"""

from __future__ import annotations

# --- Fixed enums (backed by CHECK constraints) -------------------------------

MATERIAL_TYPES = {
    "boq_standard",
    "quota_base",
    "quota_supplement",
    "quota_explanation",
    "amendment_errata",
    "related_notice",
}

QUOTA_SYSTEM_TYPES = {"construction_regional", "industry_specialty"}

JURISDICTION_LEVELS = {"national", "province", "city"}

LEGAL_STATUSES = {"unknown", "pending", "effective", "repealed"}

QUOTA_METADATA_STATUSES = {"missing", "partial", "complete", "verified"}

DOCUMENT_ROLES = {"main_volume", "explanation", "amendment", "errata", "notice", "other"}

PUBLICATION_RELATION_TYPES = {
    "supersedes",
    "supplements",
    "explains",
    "amends",
    "corrects",
    "related",
}

PROJECTION_STATUSES = {"pending", "linked", "duplicate", "invalid", "ignored"}

LINK_SOURCES = {"auto_exact", "manual", "import"}

# Field-source provenance types (SPEC §6.8); reused by field_sources payloads.
QUOTA_FIELD_SOURCE_TYPES = {
    "manual_verified",
    "official_source",
    "crawler_db",
    "ingest_manifest",
    "nas_path",
    "filename_rule",
    "legacy_import",
}

# --- Seed data for the open dictionaries -------------------------------------
# D1 ruling: preseed only the 12 industry sectors + GENERAL discipline; any other
# discipline is added from real Publication Set / Profile data (do not fabricate).

INDUSTRY_SECTOR_SEED: list[tuple[str, str]] = [
    ("water_resources", "水利工程定额"),
    ("electric_power", "电力工程定额"),
    ("power_grid", "电网工程定额"),
    ("railway", "铁路工程定额"),
    ("highway", "公路工程定额"),
    ("petroleum", "石油工程定额"),
    ("petrochemical", "石化工程定额"),
    ("coal", "煤炭工程定额"),
    ("photovoltaic", "光伏发电工程定额"),
    ("water_transport_port", "水运港口工程定额"),
    ("nonferrous_metals", "有色金属工业定额"),
    ("information_communication", "信息通信工程定额"),
]

DISCIPLINE_SEED: list[tuple[str, str]] = [
    ("general", "单一通册"),
]

DICT_TYPE_INDUSTRY_SECTOR = "industry_sector"
DICT_TYPE_DISCIPLINE = "discipline"


def sql_in_clause(column: str, values: set[str]) -> str:
    """Return a deterministic ``column in ('a', 'b', ...)`` SQL fragment."""
    joined = ", ".join(f"'{value}'" for value in sorted(values))
    return f"{column} in ({joined})"

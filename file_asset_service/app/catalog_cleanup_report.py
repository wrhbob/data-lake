"""Generate a read-only cleanup plan for duplicate and orphaned cost-info archives."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.archive_rules import metadata_value
from app.database import get_engine
from app.models import Archive, ArchiveFile, FileAsset, IngestEvent
from app.normalization import normalize_key_text, normalize_source_url


def normalize_archive_period(archive: Archive) -> str:
    metadata = archive.metadata_payload or {}
    values = [archive.coverage_period]
    values.extend(metadata_value(metadata.get(key)) for key in ("period_start", "period", "period_raw"))
    for value in values:
        if value is None or not str(value).strip():
            continue
        raw = str(value).strip()
        match = re.search(r"(20\d{2})[-年/.](1[0-2]|0?[1-9])", raw)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
        return normalize_key_text(raw)
    return ""


def _manifest_rebuild_archive(archive: Archive) -> bool:
    if archive.collection_method == "legacy_batch_import":
        return True
    return any(
        isinstance(value, dict) and value.get("tagged_by") == "manifest_catalog_rebuild"
        for value in (archive.metadata_payload or {}).values()
    )


def _canonical_score(record: dict[str, object]) -> tuple[int, int, int, float]:
    archive: Archive = record["archive"]  # type: ignore[assignment]
    status_score = {"archived": 4, "ready_for_governance": 3, "collected": 2}.get(archive.status, 1)
    created = archive.created_at.timestamp() if archive.created_at else 0
    return (
        1 if record["valid_mounts"] else 0,
        0 if _manifest_rebuild_archive(archive) else 1,
        status_score,
        -created,
    )


def build_cleanup_report(session: Session) -> dict[str, object]:
    archives = session.scalars(
        select(Archive).where(Archive.domain_type == "cost_info", Archive.is_withdrawn.is_(False))
    ).all()
    archive_ids = [archive.archive_id for archive in archives]
    mounts_by_archive: dict[str, list[tuple[ArchiveFile, FileAsset | None]]] = defaultdict(list)
    if archive_ids:
        for mounted, asset in session.execute(
            select(ArchiveFile, FileAsset)
            .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(ArchiveFile.archive_id.in_(archive_ids))
        ).all():
            mounts_by_archive[mounted.archive_id].append((mounted, asset))

    assets = session.scalars(select(FileAsset)).all()
    asset_by_id = {asset.file_id: asset for asset in assets}
    assets_by_prefix: dict[str, list[FileAsset]] = defaultdict(list)
    for asset in assets:
        assets_by_prefix[asset.sha256[:12].lower()].append(asset)

    events = session.scalars(select(IngestEvent)).all()
    event_by_id = {event.event_id: event for event in events}
    events_by_file_id: dict[str, list[IngestEvent]] = defaultdict(list)
    assets_by_url: dict[str, set[str]] = defaultdict(set)
    for event in events:
        events_by_file_id[event.file_id].append(event)
        normalized_url = normalize_source_url(event.source_url)
        if event.file_id in asset_by_id and normalized_url:
            assets_by_url[normalized_url].add(event.file_id)

    records: dict[str, dict[str, object]] = {}
    recoveries: dict[str, list[dict[str, object]]] = defaultdict(list)
    orphan_reference_count = 0
    recoverable_reference_count = 0
    for archive in archives:
        mounts = mounts_by_archive.get(archive.archive_id, [])
        valid_mounts = [(mounted, asset) for mounted, asset in mounts if asset is not None]
        orphan_mounts = [(mounted, asset) for mounted, asset in mounts if mounted.file_id and asset is None]
        orphan_reference_count += len(orphan_mounts)
        urls = {
            normalize_source_url(url)
            for url in [archive.source_url, *(mounted.source_url for mounted, _asset in mounts)]
            if normalize_source_url(url)
        }
        hashes = {asset.sha256 for _mounted, asset in valid_mounts}

        for mounted, _asset in orphan_mounts:
            related_events: list[IngestEvent] = []
            event_id = (mounted.metadata_payload or {}).get("source_event_id")
            if event_id and event_id in event_by_id:
                related_events.append(event_by_id[event_id])
            related_events.extend(events_by_file_id.get(mounted.file_id or "", []))
            prefixes = {
                match.group(1).lower()
                for event in related_events
                if event.batch_id and (match := re.match(r"manifest_rebuild:([0-9a-fA-F]{12})", event.batch_id))
            }
            candidate_ids = {
                asset.file_id
                for prefix in prefixes
                for asset in assets_by_prefix.get(prefix, [])
                if asset.tenant_code == archive.tenant_code
            }
            evidence = [f"sha256_prefix:{prefix}" for prefix in sorted(prefixes)]
            if not candidate_ids:
                orphan_urls = {
                    normalize_source_url(url)
                    for url in [mounted.source_url, archive.source_url, *(event.source_url for event in related_events)]
                    if normalize_source_url(url)
                }
                candidate_ids = {
                    file_id
                    for url in orphan_urls
                    for file_id in assets_by_url.get(url, set())
                    if asset_by_id[file_id].tenant_code == archive.tenant_code
                }
                evidence.extend(f"source_url:{url}" for url in sorted(orphan_urls))
            candidates = sorted(candidate_ids)
            if len(candidates) == 1:
                recoverable_reference_count += 1
                hashes.add(asset_by_id[candidates[0]].sha256)
            recoveries[archive.archive_id].append(
                {
                    "archive_file_id": mounted.archive_file_id,
                    "missing_file_id": mounted.file_id,
                    "candidate_file_ids": candidates,
                    "candidate_count": len(candidates),
                    "evidence": evidence,
                }
            )

        period = normalize_archive_period(archive)
        title_key = normalize_key_text(archive.title)
        region = archive.region_code or ""
        identity_keys: set[str] = set()
        if region and period and title_key:
            identity_keys.update(f"url|{region}|{period}|{title_key}|{url}" for url in urls)
            identity_keys.update(f"sha|{region}|{period}|{title_key}|{sha}" for sha in hashes)
        records[archive.archive_id] = {
            "archive": archive,
            "period": period,
            "valid_mounts": valid_mounts,
            "orphan_mounts": orphan_mounts,
            "identity_keys": identity_keys,
        }

    parent = {archive_id: archive_id for archive_id in records}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    key_owner: dict[str, str] = {}
    for archive_id, record in records.items():
        for key in record["identity_keys"]:  # type: ignore[union-attr]
            if key in key_owner:
                union(archive_id, key_owner[key])
            else:
                key_owner[key] = archive_id

    components: dict[str, list[str]] = defaultdict(list)
    for archive_id in records:
        components[find(archive_id)].append(archive_id)
    duplicate_components = [members for members in components.values() if len(members) > 1]

    actions: dict[str, dict[str, object]] = {}
    duplicate_groups: list[dict[str, object]] = []
    for index, members in enumerate(sorted(duplicate_components, key=lambda group: (-len(group), sorted(group)[0])), 1):
        canonical_id = max(members, key=lambda item: _canonical_score(records[item]))
        canonical_record = records[canonical_id]
        canonical_recoveries = recoveries.get(canonical_id, [])
        canonical_ready = bool(canonical_record["valid_mounts"]) or (
            bool(canonical_recoveries) and all(item["candidate_count"] == 1 for item in canonical_recoveries)
        )
        shared_keys = sorted(set.intersection(*(records[item]["identity_keys"] for item in members)))  # type: ignore[arg-type]
        duplicate_groups.append(
            {
                "group_id": f"DUP-{index:04d}",
                "canonical_archive_id": canonical_id,
                "archive_ids": sorted(members),
                "canonical_ready": canonical_ready,
                "evidence_keys": shared_keys,
            }
        )
        if canonical_ready:
            for archive_id in members:
                if archive_id == canonical_id:
                    continue
                archive: Archive = records[archive_id]["archive"]  # type: ignore[assignment]
                actions[archive_id] = {
                    "action": "withdraw_duplicate",
                    "archive_id": archive_id,
                    "archive_title": archive.title,
                    "region_code": archive.region_code,
                    "period": records[archive_id]["period"],
                    "source_id": archive.source_id,
                    "collection_method": archive.collection_method,
                    "canonical_archive_id": canonical_id,
                    "reason": "same region/period/title and same canonical source URL or SHA-256",
                    "orphan_references": recoveries.get(archive_id, []),
                }

    for archive_id, record in records.items():
        orphan_rows = recoveries.get(archive_id, [])
        if not orphan_rows or archive_id in actions:
            continue
        archive: Archive = record["archive"]  # type: ignore[assignment]
        action = "manual_review"
        reason = "missing file reference has zero or multiple recovery candidates"
        if record["valid_mounts"]:
            action = "remove_redundant_orphan_reference"
            reason = "archive already has a valid file mount"
        elif all(item["candidate_count"] == 1 for item in orphan_rows):
            action = "relink_orphan"
            reason = "every missing file_id has one unique existing FileAsset candidate"
        actions[archive_id] = {
            "action": action,
            "archive_id": archive_id,
            "archive_title": archive.title,
            "region_code": archive.region_code,
            "period": record["period"],
            "source_id": archive.source_id,
            "collection_method": archive.collection_method,
            "reason": reason,
            "orphan_references": orphan_rows,
        }

    ordered_actions = sorted(
        actions.values(),
        key=lambda item: (item["action"], item.get("region_code") or "", item.get("period") or "", item["archive_id"]),
    )
    action_counts = Counter(str(item["action"]) for item in ordered_actions)
    summary = {
        "active_cost_info_archive_count": len(archives),
        "file_asset_count": len(assets),
        "orphan_reference_count": orphan_reference_count,
        "orphan_archive_count": sum(1 for record in records.values() if record["orphan_mounts"]),
        "recoverable_orphan_reference_count": recoverable_reference_count,
        "safe_duplicate_group_count": len(duplicate_components),
        "duplicate_archive_excess_count": sum(len(group) - 1 for group in duplicate_components),
        "action_counts": dict(sorted(action_counts.items())),
    }
    focus_rows = []
    for archive_id, record in records.items():
        archive: Archive = record["archive"]  # type: ignore[assignment]
        if archive.region_code != "110000" or record["period"] != "2025-12":
            continue
        action = actions.get(archive_id, {})
        focus_rows.append(
            {
                "archive_id": archive_id,
                "source_id": archive.source_id,
                "collection_method": archive.collection_method,
                "valid_file_count": len(record["valid_mounts"]),  # type: ignore[arg-type]
                "orphan_file_count": len(record["orphan_mounts"]),  # type: ignore[arg-type]
                "action": action.get("action", "keep_canonical"),
                "canonical_archive_id": action.get("canonical_archive_id"),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "read_only_dry_run",
        "database_mutations": 0,
        "summary": summary,
        "focus_beijing_2025_12": sorted(focus_rows, key=lambda item: item["archive_id"]),
        "duplicate_groups": duplicate_groups,
        "actions": ordered_actions,
    }


def render_markdown(report: dict[str, object], json_name: str) -> str:
    summary: dict[str, object] = report["summary"]  # type: ignore[assignment]
    lines = [
        "# 信息价目录只读清理报告",
        "",
        f'- 生成时间：{report["generated_at"]}',
        "- 模式：只读 dry-run（数据库写入 0）",
        f"- 机器可读明细：`{json_name}`",
        "",
        "## 汇总",
        "",
        f'- 当前有效信息价档案：{summary["active_cost_info_archive_count"]}',
        f'- FileAsset：{summary["file_asset_count"]}',
        f'- 孤儿附件引用：{summary["orphan_reference_count"]} 条，涉及 {summary["orphan_archive_count"]} 份档案',
        f'- 可唯一匹配并恢复的孤儿引用：{summary["recoverable_orphan_reference_count"]} 条',
        f'- 高置信重复组：{summary["safe_duplicate_group_count"]} 组，多余档案 {summary["duplicate_archive_excess_count"]} 份',
        "",
        "### 建议动作计数",
        "",
    ]
    for action, count in sorted(summary["action_counts"].items()):  # type: ignore[union-attr]
        lines.append(f"- `{action}`：{count}")
    lines.extend(
        [
            "",
            "## 北京 2025-12 核对",
            "",
            "| archive_id | source_id | 导入方式 | 有效附件 | 孤儿附件 | 建议动作 | 保留档案 |",
            "|---|---|---:|---:|---:|---|---|",
        ]
    )
    for row in report["focus_beijing_2025_12"]:  # type: ignore[union-attr]
        lines.append(
            f'| `{row["archive_id"]}` | `{row["source_id"]}` | {row["collection_method"]} | '
            f'{row["valid_file_count"]} | {row["orphan_file_count"]} | `{row["action"]}` | '
            f'`{row.get("canonical_archive_id") or ""}` |'
        )
    lines.extend(
        [
            "",
            "## 执行边界",
            "",
            "- 本报告没有撤下、重连、删除或更新任何数据库记录。",
            "- `withdraw_duplicate` 仅在同地区、同期次、同规范化标题，并共享规范化来源 URL 或 SHA-256 时提出。",
            "- `relink_orphan` 仅在每个缺失 file_id 能唯一匹配到现存 FileAsset 时提出。",
            "- `manual_review` 不应自动处理。",
            "",
            "## 动作样例（前 100 条）",
            "",
            "| 动作 | 地区 | 期次 | 档案标题 | archive_id | 目标/保留档案 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in report["actions"][:100]:  # type: ignore[index]
        target = item.get("canonical_archive_id") or ",".join(
            ref["candidate_file_ids"][0]
            for ref in item.get("orphan_references", [])
            if ref.get("candidate_count") == 1
        )
        title = str(item["archive_title"]).replace("|", "\\|")
        lines.append(
            f'| `{item["action"]}` | {item.get("region_code") or ""} | {item.get("period") or ""} | '
            f'{title} | `{item["archive_id"]}` | `{target}` |'
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only cost-info catalog cleanup report.")
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    engine = get_engine()
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        session = Session(bind=connection, future=True, expire_on_commit=False)
        try:
            report = build_cleanup_report(session)
        finally:
            session.close()
            transaction.rollback()

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    args.markdown.write_text(render_markdown(report, args.json.name), encoding="utf-8")
    print(json.dumps({"json": str(args.json), "markdown": str(args.markdown), "summary": report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

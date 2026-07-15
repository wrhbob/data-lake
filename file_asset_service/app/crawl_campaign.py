"""Historical crawl campaigns built from durable source tasks and item inventory."""

from __future__ import annotations

import hashlib
import json
import re
import argparse
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.cost_info_scheduler import active_parser_version, adapter_kind, config_digest, source_site_id
from app.models import CollectionTask, CrawlCampaign, CrawlItem, DataSource

DATA_DOMAIN = "cost_info"
CAMPAIGN_TASK_TYPE = "crawl_campaign_collect"
CAMPAIGN_MODES = {"history_backfill", "reconcile"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def campaign_context(task: CollectionTask) -> dict | None:
    override = task.config_override if isinstance(task.config_override, dict) else {}
    value = override.get("crawl_campaign")
    return value if isinstance(value, dict) else None


def create_campaign(
    session: Session,
    *,
    source_id: str,
    name: str,
    start_period: str | None = None,
    end_period: str | None = None,
    item_limit: int | None = None,
    mode: str = "history_backfill",
    created_by: str = "crawler_campaign",
    now: datetime | None = None,
) -> CrawlCampaign:
    source = session.get(DataSource, source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
    if source.data_domain != DATA_DOMAIN:
        raise ValueError(f"CAMPAIGN_SOURCE_DOMAIN_UNSUPPORTED: {source.data_domain}")
    if source.status != "active":
        raise ValueError(f"CAMPAIGN_SOURCE_NOT_ACTIVE: {source.source_id}")
    if adapter_kind(source) is None:
        raise ValueError(f"CAMPAIGN_SOURCE_ADAPTER_REQUIRED: {source.source_id}")
    if mode not in CAMPAIGN_MODES:
        raise ValueError(f"CAMPAIGN_MODE_UNSUPPORTED: {mode}")
    _validate_period_range(start_period, end_period)

    current = now or utcnow()
    campaign = CrawlCampaign(
        source_id=source.source_id,
        data_domain=DATA_DOMAIN,
        name=name.strip() or f"{source_site_id(source)} history backfill",
        mode=mode,
        status="pending",
        start_period=start_period,
        end_period=end_period,
        as_of_at=current,
        parser_version=active_parser_version(source),
        config_digest=config_digest(source.config or {}),
        item_limit=max(1, int(item_limit)) if item_limit is not None else None,
        config={
            "site_id": source_site_id(source),
            "adapter_kind": adapter_kind(source),
            "source_config_digest": config_digest(source.config or {}),
        },
        created_by=created_by,
    )
    session.add(campaign)
    session.flush()
    enqueue_campaign_task(session, campaign=campaign, source=source, now=current, trigger="campaign_create")
    session.commit()
    return campaign


def enqueue_campaign_task(
    session: Session,
    *,
    campaign: CrawlCampaign,
    source: DataSource | None = None,
    now: datetime | None = None,
    trigger: str = "campaign_resume",
) -> CollectionTask:
    source = source or session.get(DataSource, campaign.source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {campaign.source_id}")
    batch_id = f"cost_info:{source_site_id(source)}:campaign:{campaign.campaign_id}"
    existing = session.scalar(
        select(CollectionTask).where(
            CollectionTask.source_id == source.source_id,
            CollectionTask.batch_id == batch_id,
        )
    )
    if existing is not None:
        return existing

    current = now or utcnow()
    task = CollectionTask(
        source_id=source.source_id,
        asset_tenant_code=source.asset_tenant_code,
        operator_type="system",
        task_type=CAMPAIGN_TASK_TYPE,
        trigger_type=trigger,
        batch_id=batch_id,
        data_domain=DATA_DOMAIN,
        status="pending",
        priority=20,
        scheduled_at=current,
        attempt=0,
        max_attempts=int((source.schedule_policy or {}).get("max_attempts", 3)),
        config_override={
            "site_id": source_site_id(source),
            "adapter_kind": adapter_kind(source),
            "active_parser_version": active_parser_version(source),
            "config_digest": campaign.config_digest,
            "crawl_campaign": {
                "campaign_id": campaign.campaign_id,
                "mode": campaign.mode,
                "start_period": campaign.start_period,
                "end_period": campaign.end_period,
                "item_limit": campaign.item_limit,
                "as_of_at": campaign.as_of_at.isoformat() if campaign.as_of_at else None,
            },
        },
        created_by="crawler_campaign",
    )
    session.add(task)
    return task


def get_campaign(session: Session, campaign_id: str) -> CrawlCampaign:
    campaign = session.get(CrawlCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"CRAWL_CAMPAIGN_NOT_FOUND: {campaign_id}")
    return campaign


def list_campaigns(session: Session, *, source_id: str | None = None, limit: int = 100) -> list[dict]:
    statement = select(CrawlCampaign).order_by(CrawlCampaign.created_at.desc()).limit(max(1, min(limit, 500)))
    if source_id:
        statement = statement.where(CrawlCampaign.source_id == source_id)
    return [campaign_to_dict(campaign) for campaign in session.scalars(statement).all()]


def list_crawlable_sources(
    session: Session,
    *,
    include_policy_disabled: bool = False,
    dedupe_site: bool = True,
) -> tuple[list[DataSource], list[dict]]:
    """Return active adapter-backed sources and record any logical-site duplicates.

    A source's database id is deliberately part of the archive business key.  A
    duplicate source record for the same stable site id would therefore create a
    second archive lineage for the exact same publication.  Batch backfill uses
    one preferred record per stable site id unless explicitly asked otherwise.
    """

    candidates = []
    for source in session.scalars(
        select(DataSource)
        .where(DataSource.data_domain == DATA_DOMAIN, DataSource.status == "active")
        .order_by(DataSource.created_at.asc())
    ).all():
        policy = source.schedule_policy or {}
        if not include_policy_disabled and policy.get("enabled") is not True:
            continue
        if adapter_kind(source) is None:
            continue
        candidates.append(source)

    if not dedupe_site:
        return candidates, []

    grouped: dict[str, list[DataSource]] = {}
    for source in candidates:
        grouped.setdefault(source_site_id(source), []).append(source)
    selected: list[DataSource] = []
    skipped: list[dict] = []
    for site_id, sources in grouped.items():
        preferred = max(sources, key=_source_preference_key)
        selected.append(preferred)
        for source in sources:
            if source.source_id == preferred.source_id:
                continue
            skipped.append(
                {
                    "site_id": site_id,
                    "source_id": source.source_id,
                    "preferred_source_id": preferred.source_id,
                    "reason": "duplicate_site_id",
                }
            )
    return sorted(selected, key=lambda source: source_site_id(source)), skipped


def create_campaigns_for_crawlable_sources(
    session: Session,
    *,
    name_prefix: str,
    start_period: str | None = None,
    end_period: str | None = None,
    include_policy_disabled: bool = False,
    dedupe_site: bool = True,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Create one idempotent historical campaign per currently crawlable site."""

    _validate_period_range(start_period, end_period)
    sources, duplicate_skips = list_crawlable_sources(
        session,
        include_policy_disabled=include_policy_disabled,
        dedupe_site=dedupe_site,
    )
    current = now or utcnow()
    report = {
        "source_seen": len(sources),
        "duplicate_site_skipped": duplicate_skips,
        "created_count": 0,
        "existing_count": 0,
        "dry_run": dry_run,
        "campaigns": [],
    }
    for source in sources:
        name = f"{name_prefix}:{source_site_id(source)}"
        existing = session.scalar(
            select(CrawlCampaign).where(
                CrawlCampaign.source_id == source.source_id,
                CrawlCampaign.name == name,
                CrawlCampaign.mode == "history_backfill",
            )
        )
        row = {"source_id": source.source_id, "site_id": source_site_id(source), "name": name}
        if existing is not None:
            row.update({"action": "existing", "campaign_id": existing.campaign_id, "status": existing.status})
            report["existing_count"] += 1
            report["campaigns"].append(row)
            continue
        if dry_run:
            row["action"] = "dry_run"
            report["campaigns"].append(row)
            continue
        campaign = create_campaign(
            session,
            source_id=source.source_id,
            name=name,
            start_period=start_period,
            end_period=end_period,
            created_by="crawl_campaign_batch",
            now=current,
        )
        row.update({"action": "created", "campaign_id": campaign.campaign_id, "status": campaign.status})
        report["created_count"] += 1
        report["campaigns"].append(row)
    return report


def campaign_to_dict(campaign: CrawlCampaign) -> dict:
    return {
        "campaign_id": campaign.campaign_id,
        "source_id": campaign.source_id,
        "data_domain": campaign.data_domain,
        "name": campaign.name,
        "mode": campaign.mode,
        "status": campaign.status,
        "start_period": campaign.start_period,
        "end_period": campaign.end_period,
        "as_of_at": campaign.as_of_at.isoformat() if campaign.as_of_at else None,
        "parser_version": campaign.parser_version,
        "config_digest": campaign.config_digest,
        "item_limit": campaign.item_limit,
        "discovered_count": campaign.discovered_count,
        "completed_count": campaign.completed_count,
        "duplicate_count": campaign.duplicate_count,
        "failed_count": campaign.failed_count,
        "created_at": campaign.created_at.isoformat(),
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
    }


def record_discovered_items(
    session: Session,
    *,
    task: CollectionTask,
    source: DataSource,
    issues: list[DiscoveredIssue],
    now: datetime,
) -> dict[str, CrawlItem]:
    context = campaign_context(task)
    if context is None:
        return {}
    campaign = get_campaign(session, str(context.get("campaign_id") or ""))
    if campaign.status == "pending":
        campaign.status = "running"
        campaign.started_at = now

    selected = [issue for issue in issues if _issue_in_campaign_period(issue, campaign)]
    if campaign.item_limit is not None:
        selected = selected[: campaign.item_limit]

    items: dict[str, CrawlItem] = {}
    for issue in selected:
        item = session.scalar(
            select(CrawlItem).where(
                CrawlItem.campaign_id == campaign.campaign_id,
                CrawlItem.source_item_key == issue.source_item_key,
            )
        )
        payload = _issue_payload(issue)
        fingerprint = _attachment_fingerprint(issue)
        if item is None:
            item = CrawlItem(
                campaign_id=campaign.campaign_id,
                source_id=source.source_id,
                source_item_key=issue.source_item_key,
                title=issue.title,
                detail_url=issue.detail_url,
                publish_date=issue.publish_date,
                period_start=_period_start(issue.period_raw or issue.title),
                attachment_fingerprint=fingerprint,
                payload=payload,
                status="discovered",
                task_id=task.task_id,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(item)
        else:
            item.title = issue.title
            item.detail_url = issue.detail_url
            item.publish_date = issue.publish_date
            item.period_start = _period_start(issue.period_raw or issue.title)
            item.payload = payload
            item.task_id = task.task_id
            item.last_seen_at = now
            if item.attachment_fingerprint != fingerprint and item.status in {"done", "duplicate"}:
                # This is deliberately surfaced, not automatically re-ingested:
                # archive version promotion is domain-specific and must remain
                # explicit until every adapter supports revised attachments.
                item.status = "changed"
            item.attachment_fingerprint = fingerprint
        items[issue.source_item_key] = item
    session.flush()
    refresh_campaign(session, campaign=campaign, now=now)
    return items


def mark_item(
    session: Session,
    *,
    item: CrawlItem | None,
    status: str,
    now: datetime,
    error: str | None = None,
) -> None:
    if item is None:
        return
    item.status = status
    item.last_error = error[:1000] if error else None
    if status in {"done", "duplicate", "failed"}:
        item.completed_at = now
    session.flush()


def refresh_campaign(session: Session, *, campaign: CrawlCampaign, now: datetime) -> CrawlCampaign:
    rows = session.execute(
        select(CrawlItem.status, func.count(CrawlItem.item_id))
        .where(CrawlItem.campaign_id == campaign.campaign_id)
        .group_by(CrawlItem.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    campaign.discovered_count = sum(counts.values())
    campaign.completed_count = counts.get("done", 0)
    campaign.duplicate_count = counts.get("duplicate", 0)
    campaign.failed_count = counts.get("failed", 0)
    active = counts.get("discovered", 0) + counts.get("downloading", 0)
    if campaign.status not in {"failed", "cancelled"} and active == 0:
        campaign.status = "completed_with_errors" if campaign.failed_count else "completed"
        campaign.finished_at = now
    campaign.updated_at = now
    return campaign


def mark_campaign_task_failure(session: Session, *, task: CollectionTask, now: datetime, terminal: bool) -> None:
    context = campaign_context(task)
    if context is None:
        return
    campaign = session.get(CrawlCampaign, str(context.get("campaign_id") or ""))
    if campaign is None:
        return
    if terminal:
        campaign.status = "failed"
        campaign.finished_at = now
    elif campaign.status == "pending":
        campaign.status = "running"
        campaign.started_at = now
    campaign.updated_at = now
    session.flush()


def _issue_payload(issue: DiscoveredIssue) -> dict:
    return {
        "source_item_key": issue.source_item_key,
        "title": issue.title,
        "publish_date": issue.publish_date,
        "period_raw": issue.period_raw,
        "detail_url": issue.detail_url,
        "attachment_urls": list(issue.attachment_urls),
    }


def _attachment_fingerprint(issue: DiscoveredIssue) -> str:
    payload = json.dumps(
        {"detail_url": issue.detail_url, "attachment_urls": sorted(issue.attachment_urls)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _period_start(value: str | None) -> str | None:
    match = re.search(r"(?P<year>20\d{2})\s*(?:年|[-/.])\s*(?P<month>0?[1-9]|1[0-2])\s*(?:月)?", value or "")
    if not match:
        return None
    return f"{match.group('year')}-{int(match.group('month')):02d}"


def _issue_in_campaign_period(issue: DiscoveredIssue, campaign: CrawlCampaign) -> bool:
    period = _period_start(issue.period_raw or issue.title)
    if campaign.start_period and (period is None or period < campaign.start_period):
        return False
    if campaign.end_period and (period is None or period > campaign.end_period):
        return False
    return True


def _validate_period_range(start_period: str | None, end_period: str | None) -> None:
    for value in (start_period, end_period):
        if value is not None and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
            raise ValueError(f"CAMPAIGN_PERIOD_INVALID: {value}")
    if start_period and end_period and start_period > end_period:
        raise ValueError("CAMPAIGN_PERIOD_RANGE_INVALID")


def _source_preference_key(source: DataSource) -> tuple[int, int, datetime, datetime]:
    """Prefer a source with an explicit historical page range, then newest config."""

    config = source.config or {}
    parser_root = config.get("parser") if isinstance(config.get("parser"), dict) else {}
    version = parser_root.get("active_parser_version")
    parser = (parser_root.get("parsers") or {}).get(version) or {}
    pagination = parser.get("pagination") if isinstance(parser, dict) and isinstance(parser.get("pagination"), dict) else {}
    page_values = [
        pagination.get("max_pages"),
        pagination.get("max_pages_observed"),
        pagination.get("max_pages_per_year"),
        pagination.get("page_count_observed"),
    ]
    page_count = max((int(value) for value in page_values if str(value).isdigit()), default=0)
    historical = int(page_count > 1 or bool(parser.get("history")))
    return historical, page_count, source.updated_at, source.created_at


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and inspect source-scoped historical crawl campaigns.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--start-period")
    create.add_argument("--end-period")
    create.add_argument("--item-limit", type=int)
    create.add_argument("--mode", default="history_backfill")
    create_all = subparsers.add_parser("create-all")
    create_all.add_argument("--name-prefix", required=True)
    create_all.add_argument("--start-period")
    create_all.add_argument("--end-period")
    create_all.add_argument("--include-policy-disabled", action="store_true")
    create_all.add_argument("--no-dedupe-site", action="store_true")
    create_all.add_argument("--dry-run", action="store_true")
    listing = subparsers.add_parser("list")
    listing.add_argument("--source-id")
    listing.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from app.database import get_session_factory, init_db

    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[crawl_campaign] could not load .env: {exc}", flush=True)
    init_db()
    SessionFactory = get_session_factory()
    with SessionFactory() as session:
        if args.command == "create":
            campaign = create_campaign(
                session,
                source_id=args.source_id,
                name=args.name,
                start_period=args.start_period,
                end_period=args.end_period,
                item_limit=args.item_limit,
                mode=args.mode,
                created_by="crawl_campaign_cli",
            )
            payload: object = campaign_to_dict(campaign)
        elif args.command == "create-all":
            payload = create_campaigns_for_crawlable_sources(
                session,
                name_prefix=args.name_prefix,
                start_period=args.start_period,
                end_period=args.end_period,
                include_policy_disabled=args.include_policy_disabled,
                dedupe_site=not args.no_dedupe_site,
                dry_run=args.dry_run,
            )
        elif args.command == "list":
            payload = list_campaigns(session, source_id=args.source_id, limit=args.limit)
        else:
            raise ValueError(f"unsupported command: {args.command}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

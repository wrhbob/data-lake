"""Source-registry helpers for public-resource trading announcement sources.

Trading sources use the same ``data_source`` table as information-price
sources, but they are deliberately introduced as ``pending_verify``.  A small
task-and-loop verification run promotes a source to ``active`` only after its
list/detail chain has completed successfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import create_data_source
from app.models import DataSource, utcnow

DATA_DOMAIN = "trading"
SOURCE_TYPE = "public_resource_exchange"
CONNECTOR_TYPE = "source_registry"


@dataclass(frozen=True)
class ImportTradingSourceResult:
    action: str
    site_id: str
    source_id: str

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "site_id": self.site_id, "source_id": self.source_id}


def _stable(config: dict) -> dict:
    stable = config.get("stable")
    if not isinstance(stable, dict):
        raise ValueError("SOURCE_REGISTRY_STABLE_REQUIRED")
    return stable


def _site_id(config: dict) -> str:
    site_id = _stable(config).get("site_id")
    if not site_id:
        raise ValueError("SOURCE_REGISTRY_SITE_ID_REQUIRED")
    return str(site_id)


def _entry_url(config: dict) -> str:
    entry_url = _stable(config).get("entry_url")
    if not entry_url:
        raise ValueError("SOURCE_REGISTRY_ENTRY_URL_REQUIRED")
    return str(entry_url)


def _publisher_name(config: dict) -> str:
    stable = _stable(config)
    return str(stable.get("publisher_name") or stable.get("site_name") or stable["site_id"])


def _schedule_policy(config: dict) -> dict:
    """Build the mutable-loop policy from a trading source's static config."""

    explicit = config.get("schedule_policy")
    if isinstance(explicit, dict):
        return dict(explicit)

    ops = config.get("ops") if isinstance(config.get("ops"), dict) else {}
    schedule = ops.get("schedule") if isinstance(ops.get("schedule"), dict) else {}
    queue = ops.get("queue") if isinstance(ops.get("queue"), dict) else {}
    return {
        "enabled": ops.get("enabled") is True,
        # Announcements are event-driven.  The loop runs often, while the
        # source itself asks to be scanned twice on each local calendar day.
        "frequency": str(schedule.get("frequency") or "daily"),
        "timezone": str(schedule.get("timezone") or "Asia/Shanghai"),
        "scan_times": list(schedule.get("scan_times") or ["09:10", "15:10"]),
        "max_attempts": int(queue.get("max_attempts") or ops.get("max_attempts") or 3),
        "rate_limit": {
            "host": urlsplit(_entry_url(config)).netloc,
            "max_concurrent": int(queue.get("max_concurrent_per_host") or 1),
            "min_delay_seconds": float(queue.get("min_delay_seconds") or 2),
            "jitter_seconds": float(queue.get("jitter_seconds") or 1),
        },
        "max_pages_per_run": int(queue.get("max_pages_per_run") or 2),
        "page_size": int(queue.get("page_size") or 10),
        "max_items_per_channel": int(queue.get("max_items_per_channel") or 20),
        "verification": {
            "channel_ids": list((ops.get("verification") or {}).get("channel_ids") or ["tender_notice"]),
            "max_pages": int((ops.get("verification") or {}).get("max_pages") or 1),
            "page_size": int((ops.get("verification") or {}).get("page_size") or 1),
            "max_items_per_channel": int(
                (ops.get("verification") or {}).get("max_items_per_channel") or 1
            ),
        },
    }


def find_trading_data_source_by_site_id(session: Session, site_id: str) -> DataSource | None:
    statement = (
        select(DataSource)
        .where(
            DataSource.data_domain == DATA_DOMAIN,
            DataSource.source_type == SOURCE_TYPE,
            DataSource.connector_type == CONNECTOR_TYPE,
        )
        .order_by(DataSource.created_at.asc())
    )
    for source in session.scalars(statement).all():
        stable = (source.config or {}).get("stable") or {}
        if stable.get("site_id") == site_id:
            return source
    return None


def _apply_registry_fields(source: DataSource, config: dict) -> None:
    stable = _stable(config)
    entry_url = _entry_url(config)
    source.source_type = SOURCE_TYPE
    source.connector_type = CONNECTOR_TYPE
    source.name = _publisher_name(config)
    source.data_domain = DATA_DOMAIN
    source.base_url = entry_url
    source.url = entry_url
    source.province = stable.get("province")
    source.city = stable.get("city")
    source.region_code = stable.get("region_code")
    source.frequency = "daily"
    source.config = config
    source.schedule_policy = _schedule_policy(config)
    source.updated_at = utcnow()


def import_trading_source_config(config: dict, session: Session) -> ImportTradingSourceResult:
    stable = _stable(config)
    if stable.get("domain_type") not in {None, DATA_DOMAIN}:
        raise ValueError(f"SOURCE_DOMAIN_MISMATCH: {stable.get('domain_type')}")

    site_id = _site_id(config)
    source = find_trading_data_source_by_site_id(session, site_id)
    if source is None:
        source = create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type=SOURCE_TYPE,
            connector_type=CONNECTOR_TYPE,
            name=_publisher_name(config),
            data_domain=DATA_DOMAIN,
            base_url=_entry_url(config),
            url=_entry_url(config),
            province=stable.get("province"),
            city=stable.get("city"),
            region_code=stable.get("region_code"),
            frequency="daily",
            config=config,
            schedule_policy=_schedule_policy(config),
            status="pending_verify",
            created_by="trading_source_registry",
        )
        action = "created"
    else:
        _apply_registry_fields(source, config)
        session.commit()
        action = "updated"
    return ImportTradingSourceResult(action=action, site_id=site_id, source_id=source.source_id)


def list_trading_sources(session: Session) -> list[DataSource]:
    statement = (
        select(DataSource)
        .where(
            DataSource.data_domain == DATA_DOMAIN,
            DataSource.source_type == SOURCE_TYPE,
            DataSource.connector_type == CONNECTOR_TYPE,
        )
        .order_by(DataSource.region_code.asc(), DataSource.created_at.asc())
    )
    return list(session.scalars(statement).all())


def set_trading_source_active(session: Session, source: DataSource) -> DataSource:
    source.status = "active"
    source.updated_at = utcnow()
    session.commit()
    return source

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collection import create_data_source
from app.database import get_session_factory, init_db
from app.models import DataSource, utcnow


DATA_DOMAIN = "cost_info"
SOURCE_TYPE = "info_price"
CONNECTOR_TYPE = "source_registry"


@dataclass(frozen=True)
class ImportSourceResult:
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


def _adapter_kind(config: dict) -> str | None:
    parser = config.get("parser") if isinstance(config.get("parser"), dict) else {}
    active_version = parser.get("active_parser_version")
    parsers = parser.get("parsers") if isinstance(parser.get("parsers"), dict) else {}
    if not active_version:
        return None
    adapter = (parsers.get(active_version) or {}).get("adapter_kind")
    return str(adapter) if adapter else None


def _schedule_policy(config: dict) -> dict | None:
    explicit = config.get("schedule_policy")
    if explicit is not None:
        return explicit

    ops = config.get("ops") if isinstance(config.get("ops"), dict) else {}
    if not ops:
        return None

    schedule = ops.get("schedule") if isinstance(ops.get("schedule"), dict) else {}
    queue = ops.get("queue") if isinstance(ops.get("queue"), dict) else {}
    host = urlsplit(_entry_url(config)).netloc
    rate_limit = {
        "host": host,
        "max_concurrent": int(queue.get("max_concurrent_per_host") or 1),
        "min_delay_seconds": int(queue.get("min_delay_seconds") or 5),
        "jitter_seconds": int(queue.get("jitter_seconds") or 2),
    }
    policy = {
        "enabled": ops.get("enabled") is True,
        "frequency": str(schedule.get("frequency") or "daily"),
        "timezone": str(schedule.get("timezone") or "Asia/Shanghai"),
        "max_attempts": int(queue.get("max_attempts") or ops.get("max_attempts") or 3),
        "early_stop_duplicate": bool(ops.get("early_stop_duplicate", True)),
        "rate_limit": rate_limit,
    }
    if queue.get("max_items_per_run") is not None:
        policy["max_items_per_run"] = int(queue["max_items_per_run"])
    return policy


def _source_to_row(source: DataSource) -> dict:
    return {
        "source_id": source.source_id,
        "site_id": ((source.config or {}).get("stable") or {}).get("site_id"),
        "name": source.name,
        "status": source.status,
        "province": source.province,
        "city": source.city,
        "region_code": source.region_code,
        "adapter_kind": _adapter_kind(source.config or {}),
    }


def _source_to_detail(source: DataSource) -> dict:
    row = _source_to_row(source)
    row.update(
        {
            "source_type": source.source_type,
            "connector_type": source.connector_type,
            "data_domain": source.data_domain,
            "base_url": source.base_url,
            "url": source.url,
            "schedule_policy": source.schedule_policy,
            "config": source.config,
        }
    )
    return row


def find_data_source_by_site_id(session: Session, site_id: str) -> DataSource | None:
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
        if ((source.config or {}).get("stable") or {}).get("site_id") == site_id:
            return source
    return None


def _find_source(session: Session, *, source_id: str | None = None, site_id: str | None = None) -> DataSource:
    if source_id:
        source = session.get(DataSource, source_id)
        if source is None:
            raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
        return source
    if site_id:
        source = find_data_source_by_site_id(session, site_id)
        if source is None:
            raise ValueError(f"DATA_SOURCE_SITE_ID_NOT_FOUND: {site_id}")
        return source
    raise ValueError("SOURCE_SELECTOR_REQUIRED")


def _apply_source_registry_fields(source: DataSource, config: dict) -> None:
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
    source.config = config
    source.schedule_policy = _schedule_policy(config)
    source.updated_at = utcnow()


def import_source_config(config: dict, session: Session) -> ImportSourceResult:
    stable = _stable(config)
    if stable.get("domain_type") not in {None, DATA_DOMAIN}:
        raise ValueError(f"SOURCE_DOMAIN_MISMATCH: {stable.get('domain_type')}")
    site_id = _site_id(config)
    existing = find_data_source_by_site_id(session, site_id)

    if existing is not None:
        _apply_source_registry_fields(existing, config)
        action = "updated"
        source = existing
    else:
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
            config=config,
            schedule_policy=_schedule_policy(config),
            status="pending_verify",
            created_by="cost_info_registry",
        )
        action = "created"

    session.commit()
    return ImportSourceResult(action=action, site_id=site_id, source_id=source.source_id)


def import_source_config_file(path: str | Path, session: Session) -> ImportSourceResult:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    return import_source_config(config, session)


def list_cost_info_sources(session: Session) -> list[dict]:
    statement = (
        select(DataSource)
        .where(
            DataSource.data_domain == DATA_DOMAIN,
            DataSource.source_type == SOURCE_TYPE,
            DataSource.connector_type == CONNECTOR_TYPE,
        )
        .order_by(DataSource.created_at.asc())
    )
    return [_source_to_row(source) for source in session.scalars(statement).all()]


def show_source(session: Session, *, source_id: str | None = None, site_id: str | None = None) -> dict:
    return _source_to_detail(_find_source(session, source_id=source_id, site_id=site_id))


def set_active_source(session: Session, *, source_id: str | None = None, site_id: str | None = None) -> dict:
    source = _find_source(session, source_id=source_id, site_id=site_id)
    source.status = "active"
    source.updated_at = utcnow()
    session.commit()
    return _source_to_row(source)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import and inspect cost_info Source Registry configs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("config_path", type=Path)

    subparsers.add_parser("list")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--source-id")
    show_parser.add_argument("--site-id")

    active_parser = subparsers.add_parser("set-active")
    active_parser.add_argument("--source-id")
    active_parser.add_argument("--site-id")

    subparsers.add_parser("list-factories")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--site-id", required=True)
    restore_parser.add_argument("--activate", action="store_true")

    restore_all_parser = subparsers.add_parser("restore-all")
    restore_all_parser.add_argument("--activate", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_db()
    SessionFactory = get_session_factory()
    with SessionFactory() as session:
        if args.command == "import":
            payload = import_source_config_file(args.config_path, session).to_dict()
        elif args.command == "list":
            payload = list_cost_info_sources(session)
        elif args.command == "show":
            payload = show_source(session, source_id=args.source_id, site_id=args.site_id)
        elif args.command == "set-active":
            payload = set_active_source(session, source_id=args.source_id, site_id=args.site_id)
        elif args.command == "list-factories":
            from app.cost_info_config_factories import list_factory_rows

            payload = list_factory_rows()
        elif args.command == "restore":
            from app.cost_info_config_factories import restore_source_config

            payload = restore_source_config(session, args.site_id, activate=args.activate).to_dict()
        elif args.command == "restore-all":
            from app.cost_info_config_factories import restore_all_source_configs

            payload = [result.to_dict() for result in restore_all_source_configs(session, activate=args.activate)]
        else:
            raise ValueError(f"unsupported command: {args.command}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

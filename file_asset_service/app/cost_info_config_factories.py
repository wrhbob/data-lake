"""Bridge between per-adapter source_config factories and the source registry.

Each crawlable cost_info adapter module already ships a factory that returns the
*full* source registry config (endpoints, list URLs, selectors, period regex,
ops/queue). The CSV ledger import in ``info_price_site_import`` only stores a
summary (``adapter_kind`` plus the site root URL as ``list_url``), which is not
enough to crawl and even overrides the adapter's correct list page.

This module aggregates every crawlable factory keyed by its own
``stable.site_id`` and exposes helpers to restore the full config into an
existing/created ``data_source`` row via ``cost_info_registry.import_source_config``.

Building the registry is a pure, offline operation: it only calls the factory
functions and reads their returned dicts. No network access is performed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.adapters import ADAPTERS
from app.beijing_cost_info import beijing_cost_info_source_config
from app.chongqing_cost_info import chongqing_cost_info_source_config
from app.guangdong_pdf_cost_info import (
    GUANGDONG_PDF_CITY_SOURCE_DEFS,
    _guangdong_source_config,
)
from app.hefei_cost_info import hefei_cost_info_source_config
from app.hubei_cost_info import hubei_cost_info_source_config
from app.jinan_cost_info import jinan_cost_info_source_config
from app.jiuquan_cost_info import jiuquan_cost_info_source_config
from app.linxia_cost_info import linxia_cost_info_source_config
from app.nanjing_cost_info import nanjing_cost_info_source_config
from app.ningbo_cost_info import ningbo_cost_info_source_config
from app.pingliang_cost_info import pingliang_cost_info_source_config
from app.qingyang_cost_info import qingyang_cost_info_source_config
from app.shanghai_cost_info import shanghai_cost_info_source_config
from app.shangluo_cost_info import shangluo_cost_info_source_config
from app.sichuan_pdf_cost_info import (
    SICHUAN_PDF_CITY_SOURCE_DEFS,
    _source_config as _sichuan_pdf_source_config,
)
from app.wuhan_cost_info import wuhan_cost_info_source_config
from app.xinjiang_cost_info import (
    XINJIANG_AREA_SOURCE_DEFS,
    xinjiang_area_cost_info_source_config,
)
from app.zhangye_cost_info import zhangye_cost_info_source_config

ConfigFactory = Callable[[], dict]


@dataclass(frozen=True)
class RestoreResult:
    action: str
    site_id: str
    source_id: str
    adapter_kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "site_id": self.site_id,
            "source_id": self.source_id,
            "adapter_kind": self.adapter_kind,
        }


def _bind(factory: Callable[[object], dict], arg: object) -> ConfigFactory:
    return lambda: factory(arg)


_SINGLE_SITE_FACTORIES: list[ConfigFactory] = [
    beijing_cost_info_source_config,
    shanghai_cost_info_source_config,
    chongqing_cost_info_source_config,
    hubei_cost_info_source_config,
    wuhan_cost_info_source_config,
    nanjing_cost_info_source_config,
    ningbo_cost_info_source_config,
    pingliang_cost_info_source_config,
    qingyang_cost_info_source_config,
    shangluo_cost_info_source_config,
    hefei_cost_info_source_config,
    jinan_cost_info_source_config,
    jiuquan_cost_info_source_config,
    linxia_cost_info_source_config,
    zhangye_cost_info_source_config,
]

_MULTI_SITE_FACTORIES: list[ConfigFactory] = [
    *[_bind(_sichuan_pdf_source_config, source) for source in SICHUAN_PDF_CITY_SOURCE_DEFS.values()],
    *[_bind(_guangdong_source_config, source) for source in GUANGDONG_PDF_CITY_SOURCE_DEFS.values()],
    *[_bind(xinjiang_area_cost_info_source_config, key) for key in XINJIANG_AREA_SOURCE_DEFS],
]

_ALL_FACTORIES: list[ConfigFactory] = [*_SINGLE_SITE_FACTORIES, *_MULTI_SITE_FACTORIES]


def config_adapter_kind(config: dict) -> str | None:
    parser = config.get("parser") if isinstance(config.get("parser"), dict) else {}
    active_version = parser.get("active_parser_version")
    parsers = parser.get("parsers") if isinstance(parser.get("parsers"), dict) else {}
    if not active_version:
        return None
    adapter_kind = (parsers.get(active_version) or {}).get("adapter_kind")
    return str(adapter_kind) if adapter_kind else None


def _config_site_id(config: dict) -> str:
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    site_id = stable.get("site_id")
    if not site_id:
        raise ValueError("CONFIG_FACTORY_SITE_ID_REQUIRED")
    return str(site_id)


def _build_registry() -> dict[str, ConfigFactory]:
    registry: dict[str, ConfigFactory] = {}
    for factory in _ALL_FACTORIES:
        config = factory()
        site_id = _config_site_id(config)
        adapter_kind = config_adapter_kind(config)
        if not adapter_kind:
            raise ValueError(f"CONFIG_FACTORY_ADAPTER_KIND_REQUIRED: {site_id}")
        if adapter_kind not in ADAPTERS:
            raise ValueError(f"CONFIG_FACTORY_UNKNOWN_ADAPTER_KIND: {site_id} -> {adapter_kind}")
        stable_entry_url = (config.get("stable") or {}).get("entry_url")
        if not stable_entry_url:
            raise ValueError(f"CONFIG_FACTORY_ENTRY_URL_REQUIRED: {site_id}")
        if site_id in registry:
            raise ValueError(f"CONFIG_FACTORY_DUPLICATE_SITE_ID: {site_id}")
        registry[site_id] = factory
    return registry


SITE_CONFIG_FACTORIES: dict[str, ConfigFactory] = _build_registry()


def list_site_ids() -> list[str]:
    return sorted(SITE_CONFIG_FACTORIES)


def get_config_factory(site_id: str) -> ConfigFactory:
    factory = SITE_CONFIG_FACTORIES.get(site_id)
    if factory is None:
        raise ValueError(f"CONFIG_FACTORY_SITE_ID_NOT_REGISTERED: {site_id}")
    return factory


def build_source_config(site_id: str) -> dict:
    """Return the full source registry config for ``site_id`` from its factory."""
    return get_config_factory(site_id)()


def list_factory_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for site_id in list_site_ids():
        config = build_source_config(site_id)
        stable = config.get("stable") or {}
        rows.append(
            {
                "site_id": site_id,
                "adapter_kind": config_adapter_kind(config) or "",
                "region_code": str(stable.get("region_code") or ""),
                "publisher_name": str(stable.get("publisher_name") or stable.get("publisher") or ""),
            }
        )
    return rows


def restore_source_config(session, site_id: str, *, activate: bool = False) -> RestoreResult:
    """Overwrite the DB source for ``site_id`` with the full factory config.

    Matches/creates the ``data_source`` by ``site_id`` through
    ``cost_info_registry.import_source_config`` and, when ``activate`` is set,
    flips its status to ``active``.
    """
    from app.cost_info_registry import import_source_config, set_active_source

    config = build_source_config(site_id)
    result = import_source_config(config, session)
    if activate:
        set_active_source(session, site_id=site_id)
    return RestoreResult(
        action=result.action,
        site_id=result.site_id,
        source_id=result.source_id,
        adapter_kind=config_adapter_kind(config) or "",
    )


def restore_all_source_configs(session, *, activate: bool = False) -> list[RestoreResult]:
    return [restore_source_config(session, site_id, activate=activate) for site_id in list_site_ids()]

"""Factory registry for the trading announcement sources bundled with the service."""

from __future__ import annotations

from collections.abc import Callable

from app.changsha_trading import changsha_trading_source_config
from app.chengdu_trading import chengdu_trading_source_config
from app.chongqing_trading import chongqing_trading_source_config
from app.sichuan_trading import sichuan_trading_source_config
from app.trading_source_registry import ImportTradingSourceResult, import_trading_source_config

ConfigFactory = Callable[[], dict]

SITE_CONFIG_FACTORIES: dict[str, ConfigFactory] = {
    "trading.changsha.hnsggzy.jsgc": changsha_trading_source_config,
    "trading.cdggzy.jsgc": chengdu_trading_source_config,
    "trading.cqggzy.jsgc": chongqing_trading_source_config,
    "trading.scggzy.jsgc": sichuan_trading_source_config,
}


def list_site_ids() -> list[str]:
    return sorted(SITE_CONFIG_FACTORIES)


def build_source_config(site_id: str) -> dict:
    factory = SITE_CONFIG_FACTORIES.get(site_id)
    if factory is None:
        raise ValueError(f"TRADING_CONFIG_FACTORY_SITE_ID_NOT_REGISTERED: {site_id}")
    return factory()


def restore_source_config(session, site_id: str) -> ImportTradingSourceResult:
    return import_trading_source_config(build_source_config(site_id), session)


def restore_all_source_configs(session) -> list[ImportTradingSourceResult]:
    return [restore_source_config(session, site_id) for site_id in list_site_ids()]

from __future__ import annotations


# A circuit breaker for list APIs that do not advertise a total-page count.  The
# normal exit condition remains an empty page (or a page that repeats known
# items); this only prevents a faulty endpoint from causing an infinite crawl.
HISTORY_PAGE_CEILING = 10_000


def is_history_backfill(task) -> bool:
    """Whether ``task`` must enumerate the full published list, not just top-N."""

    override = task.config_override if isinstance(getattr(task, "config_override", None), dict) else {}
    campaign = override.get("crawl_campaign") if isinstance(override, dict) else None
    return isinstance(campaign, dict) and campaign.get("mode") == "history_backfill"


def history_page_limit(source, task, parser: dict, *, default: int = HISTORY_PAGE_CEILING) -> int:
    """Return an explicit but generous pagination ceiling for history tasks."""

    if not is_history_backfill(task):
        return default
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    configured = pagination.get("max_pages") or (source.schedule_policy or {}).get("max_pages_per_run") or default
    return max(1, min(int(configured), HISTORY_PAGE_CEILING))

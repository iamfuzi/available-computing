from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session
from database import engine
from models import Setting

scheduler = AsyncIOScheduler()

DEFAULTS = {
    "discovery_interval_hours": 6,
    "probe_interval_hours": 2,
}


def _get_setting(key: str) -> int:
    with Session(engine) as session:
        row = session.get(Setting, key)
        if row:
            try:
                return int(row.value)
            except (ValueError, TypeError):
                pass
    return DEFAULTS[key]


def init_scheduler(get_key_fn=None):
    from services.discovery import discover_all_channels
    from services.health import probe_all_stale_models
    from services.cleanup import cleanup_old_health_records

    discovery_hours = _get_setting("discovery_interval_hours")
    probe_hours = _get_setting("probe_interval_hours")

    scheduler.add_job(
        discover_all_channels,
        IntervalTrigger(hours=discovery_hours),
        id="discover_all",
        replace_existing=True,
    )

    scheduler.add_job(
        probe_all_stale_models,
        IntervalTrigger(hours=probe_hours),
        args=[get_key_fn],
        id="probe_stale",
        replace_existing=True,
    )

    scheduler.add_job(
        cleanup_old_health_records,
        CronTrigger(hour=0, minute=0),
        id="cleanup_health",
        replace_existing=True,
    )

    scheduler.start()


def refresh_scheduler_intervals():
    """Re-schedule jobs when settings change."""
    from services.discovery import discover_all_channels
    from services.health import probe_all_stale_models

    discovery_hours = _get_setting("discovery_interval_hours")
    probe_hours = _get_setting("probe_interval_hours")

    scheduler.reschedule_job("discover_all", trigger=IntervalTrigger(hours=discovery_hours))
    scheduler.reschedule_job("probe_stale", trigger=IntervalTrigger(hours=probe_hours))


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

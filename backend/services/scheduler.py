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
    from datetime import datetime
    from services.discovery import discover_all_channels
    from services.health import probe_all_stale_models, recover_expired_cooldowns
    from services.cleanup import cleanup_old_health_records
    from services.candidate_pool import refresh_candidate_pool

    discovery_hours = _get_setting("discovery_interval_hours")
    probe_hours = _get_setting("probe_interval_hours")

    scheduler.add_job(
        discover_all_channels,
        IntervalTrigger(hours=discovery_hours),
        id="discover_all",
        replace_existing=True,
    )

    # Community lists are discovery evidence only. The job updates the review
    # queue and reports; it never registers adapters or creates channels.
    scheduler.add_job(
        refresh_candidate_pool,
        IntervalTrigger(hours=24),
        id="refresh_candidate_pool",
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

    # Frequently restore models whose rate-limit cooldown has expired, so they
    # don't linger as "rate_limited" after the cooldown window passes. The
    # actual health re-evaluation happens on the next probe sweep; this just
    # un-freezes the status so it's eligible again.
    scheduler.add_job(
        recover_expired_cooldowns,
        IntervalTrigger(minutes=5),
        id="recover_cooldowns",
        replace_existing=True,
    )

    # Monthly: decommission models that SiliconFlow has officially retired, so
    # the pool doesn't keep entries that fail every call. Runs on day 1 at 04:00
    # to avoid colliding with the daily cleanup (00:00).
    from services.sf_release_sync import sync_sf_decommissioned_models
    scheduler.add_job(
        sync_sf_decommissioned_models,
        CronTrigger(day=1, hour=4, minute=0),
        id="sync_sf_release",
        replace_existing=True,
    )

    scheduler.start()

    # Run an initial probe shortly after startup. IntervalTrigger's first run
    # is interval-hours away; if the process restarts often (e.g. dev --reload)
    # probes are perpetually "missed" and stale models never recover. Scheduling
    # with next_run_time=now fires it immediately without blocking startup.
    from datetime import datetime as _dt, timezone as _tz
    scheduler.add_job(
        probe_all_stale_models,
        "date",
        args=[get_key_fn],
        id="probe_stale_startup",
        replace_existing=True,
        run_date=_dt.now(_tz.utc),
    )


def refresh_scheduler_intervals():
    """Re-schedule jobs when settings change."""
    from services.discovery import discover_all_channels
    from services.health import probe_all_stale_models, recover_expired_cooldowns

    discovery_hours = _get_setting("discovery_interval_hours")
    probe_hours = _get_setting("probe_interval_hours")

    scheduler.reschedule_job("discover_all", trigger=IntervalTrigger(hours=discovery_hours))
    scheduler.reschedule_job("probe_stale", trigger=IntervalTrigger(hours=probe_hours))


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

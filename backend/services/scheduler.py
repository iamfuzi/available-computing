from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()


def init_scheduler(get_key_fn):
    from services.discovery import discover_all_channels
    from services.health import probe_all_stale_models
    from services.cleanup import cleanup_old_health_records

    # Re-discover all channels every 6 hours
    scheduler.add_job(
        discover_all_channels,
        IntervalTrigger(hours=6),
        args=[get_key_fn],
        id="discover_all",
        replace_existing=True,
    )

    # Active-probe stale models every 2 hours
    scheduler.add_job(
        probe_all_stale_models,
        IntervalTrigger(hours=2),
        args=[get_key_fn],
        id="probe_stale",
        replace_existing=True,
    )

    # Clean up health records older than 7 days, runs at midnight
    scheduler.add_job(
        cleanup_old_health_records,
        CronTrigger(hour=0, minute=0),
        id="cleanup_health",
        replace_existing=True,
    )

    scheduler.start()


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)

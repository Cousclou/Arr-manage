"""Planificateur alternatif pour enfiler les tâches périodiques via Redis."""

import asyncio
import logging
import signal
import sys

from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

TASKS = [
    ("sonarr_monitor", "sonarr_monitor_interval"),
    ("radarr_monitor", "radarr_monitor_interval"),
    ("upgrade_check", "upgrade_check_interval"),
    ("import_monitor", "import_check_interval"),
    ("anime_handler", "anime_check_interval"),
]


async def run_scheduler() -> None:
    settings = get_settings()
    await init_db()

    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    intervals = {name: getattr(settings, attr) for name, attr in TASKS}
    last_run: dict[str, float] = {}

    logger.info("Scheduler MediaGuard démarré")

    for task_name, _ in TASKS:
        job = await redis.enqueue_job(task_name)
        logger.info("Tâche initiale %s enfilée (job=%s)", task_name, job.job_id if job else None)

    stop = asyncio.Event()

    def _handle_signal(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        now = asyncio.get_event_loop().time()
        for task_name, interval in intervals.items():
            prev = last_run.get(task_name, 0)
            if now - prev >= interval:
                job = await redis.enqueue_job(task_name)
                last_run[task_name] = now
                logger.info("Tâche %s enfilée (job=%s)", task_name, job.job_id if job else None)

        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

    await redis.aclose()
    logger.info("Scheduler arrêté")


if __name__ == "__main__":
    asyncio.run(run_scheduler())

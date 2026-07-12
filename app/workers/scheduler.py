"""Planificateur pour enfiler les tâches périodiques via Redis."""

import asyncio
import logging
import signal

from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import async_session, init_db
from app.services.runtime_config import RuntimeConfig, TASK_META

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

INTERVAL_KEYS = {
    "sonarr_monitor": "sonarr_monitor_interval",
    "radarr_monitor": "radarr_monitor_interval",
    "upgrade_check": "upgrade_check_interval",
    "import_monitor": "import_check_interval",
    "anime_handler": "anime_check_interval",
    "wanted_search": "wanted_search_interval",
    "indexer_health": "indexer_health_interval",
    "indexer_global_check": "indexer_global_check_interval",
}


async def run_scheduler() -> None:
    env = get_settings()
    await init_db()

    redis = await create_pool(RedisSettings.from_dsn(env.redis_url))
    last_run: dict[str, float] = {}

    logger.info("Scheduler MediaGuard démarré")

    for task in TASK_META:
        async with async_session() as db:
            cfg = RuntimeConfig(db)
            if await cfg.get_bool(task["enabled_key"]):
                job = await redis.enqueue_job(task["name"])
                logger.info("Tâche initiale %s enfilée (job=%s)", task["name"], job.job_id if job else None)

    stop = asyncio.Event()

    def _handle_signal(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    while not stop.is_set():
        now = loop.time()
        async with async_session() as db:
            cfg = RuntimeConfig(db)
            for task in TASK_META:
                name = task["name"]
                if not await cfg.get_bool(task["enabled_key"]):
                    continue
                interval = await cfg.get_int(INTERVAL_KEYS[name])
                prev = last_run.get(name, 0)
                if now - prev >= interval:
                    job = await redis.enqueue_job(name)
                    last_run[name] = now
                    logger.info("Tâche %s enfilée (job=%s)", name, job.job_id if job else None)

        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass

    await redis.aclose()
    logger.info("Scheduler arrêté")


if __name__ == "__main__":
    asyncio.run(run_scheduler())

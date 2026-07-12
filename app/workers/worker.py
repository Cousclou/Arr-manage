"""Worker ARQ pour l'exécution des tâches en arrière-plan."""

import logging

from arq.connections import RedisSettings

from app.config import get_settings
from app.db.session import async_session
from app.services.anime_service import AnimeService
from app.services.import_monitor import ImportMonitorService
from app.services.radarr_manager import RadarrMonitorService
from app.services.sonarr_manager import SonarrMonitorService
from app.services.indexer_health import IndexerHealthService
from app.services.upgrade_service import UpgradeService
from app.services.wanted_search import WantedSearchService

logger = logging.getLogger(__name__)
settings = get_settings()


async def startup(ctx: dict) -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Worker MediaGuard démarré")


async def shutdown(ctx: dict) -> None:
    logger.info("Worker MediaGuard arrêté")


async def sonarr_monitor(ctx: dict) -> dict:
    async with async_session() as db:
        service = SonarrMonitorService(db)
        return await service.process_all_series()


async def radarr_monitor(ctx: dict) -> dict:
    async with async_session() as db:
        service = RadarrMonitorService(db)
        return await service.process_all_movies()


async def upgrade_check(ctx: dict) -> dict:
    async with async_session() as db:
        service = UpgradeService(db)
        return await service.check_all()


async def import_monitor(ctx: dict) -> dict:
    async with async_session() as db:
        service = ImportMonitorService(db)
        return await service.check_imports()


async def anime_handler(ctx: dict) -> dict:
    async with async_session() as db:
        service = AnimeService(db)
        return await service.process()


async def wanted_search(
    ctx: dict,
    series_id: int | None = None,
    movie_id: int | None = None,
    only_service: str | None = None,
) -> dict:
    async with async_session() as db:
        service = WantedSearchService(db)
        return await service.run(
            series_id=series_id,
            movie_id=movie_id,
            only_service=only_service,
        )


async def indexer_health(ctx: dict) -> dict:
    async with async_session() as db:
        service = IndexerHealthService(db)
        return await service.check()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        sonarr_monitor, radarr_monitor, upgrade_check, import_monitor,
        anime_handler, wanted_search, indexer_health,
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 5
    job_timeout = 600

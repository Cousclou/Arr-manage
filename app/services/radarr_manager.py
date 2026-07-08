"""Gestion du monitoring Radarr : désactiver films téléchargés, exclusions par tag."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.radarr import RadarrClient
from app.db.models import ExcludedMedia, MediaUpgradeRule, TaskLog
from app.services.runtime_config import RuntimeConfig
from app.services.upgrade_service import UpgradeService

logger = logging.getLogger(__name__)


class RadarrMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def process_all_movies(self) -> dict:
        stats = {
            "processed": 0,
            "skipped": 0,
            "unmonitored": 0,
            "skipped_tags": 0,
            "kept_for_upgrade": 0,
            "errors": 0,
            "dry_run": False,
        }

        cfg = self.config
        if not await cfg.get_bool("task_radarr_monitor_enabled"):
            await self._log_task("radarr_monitor", "skipped", "Tâche désactivée")
            return stats

        dry_run = await cfg.get_bool("dry_run")
        stats["dry_run"] = dry_run
        unmonitor_downloaded = await cfg.get_bool("radarr_unmonitor_downloaded")
        keep_if_upgrade = await cfg.get_bool("radarr_keep_monitored_if_upgrade")
        exclude_tags = set(await cfg.get_int_list("radarr_exclude_tag_ids"))

        client = RadarrClient(
            base_url=await cfg.get("radarr_url"),
            api_key=await cfg.get("radarr_api_key"),
        )

        try:
            movies = await client.get_movies()
        except Exception as e:
            logger.exception("Impossible de récupérer les films Radarr")
            await self._log_task("radarr_monitor", "error", str(e))
            await client.close()
            return stats

        excluded_ids = await self._get_excluded_ids()
        upgrade_checker = UpgradeService(self.db) if keep_if_upgrade else None

        for movie in movies:
            movie_id = movie["id"]
            if movie_id in excluded_ids:
                stats["skipped"] += 1
                continue

            movie_tags = set(movie.get("tags") or [])
            if exclude_tags and movie_tags & exclude_tags:
                stats["skipped_tags"] += 1
                continue

            try:
                result = await self._process_movie(
                    client,
                    movie,
                    unmonitor_downloaded,
                    keep_if_upgrade,
                    upgrade_checker,
                    dry_run,
                )
                stats["processed"] += 1
                stats["unmonitored"] += result["unmonitored"]
                stats["kept_for_upgrade"] += result["kept_for_upgrade"]
            except Exception:
                logger.exception("Erreur traitement film %s", movie.get("title"))
                stats["errors"] += 1

        mode = " [DRY-RUN]" if dry_run else ""
        await self._log_task(
            "radarr_monitor",
            "success",
            f"{stats['processed']} films{mode}, {stats['unmonitored']} désactivés, "
            f"{stats['kept_for_upgrade']} gardés (upgrade dispo), {stats['skipped_tags']} exclus par tag",
        )
        await client.close()
        return stats

    async def _get_excluded_ids(self) -> set[int]:
        result = await self.db.execute(
            select(ExcludedMedia.external_id).where(ExcludedMedia.service == "radarr")
        )
        return set(result.scalars().all())

    async def _process_movie(
        self,
        client: RadarrClient,
        movie: dict,
        unmonitor_downloaded: bool,
        keep_if_upgrade: bool,
        upgrade_checker: UpgradeService | None,
        dry_run: bool,
    ) -> dict:
        unmonitored = 0
        kept_for_upgrade = 0

        if not unmonitor_downloaded or not movie.get("hasFile") or not movie.get("monitored"):
            return {"unmonitored": 0, "kept_for_upgrade": 0}

        if keep_if_upgrade and upgrade_checker:
            has_upgrade = await upgrade_checker.has_radarr_upgrade(movie, client)
            if has_upgrade:
                kept_for_upgrade = 1
                return {"unmonitored": 0, "kept_for_upgrade": kept_for_upgrade}

        if not dry_run:
            movie["monitored"] = False
            await client.update_movie(movie)
        unmonitored = 1

        return {"unmonitored": unmonitored, "kept_for_upgrade": kept_for_upgrade}

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

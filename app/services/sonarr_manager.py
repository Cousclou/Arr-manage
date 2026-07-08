"""Gestion du monitoring Sonarr : désactiver épisodes téléchargés, monitor new only."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sonarr import SonarrClient
from app.db.models import TaskLog

logger = logging.getLogger(__name__)


class SonarrMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.client = SonarrClient()

    async def process_all_series(self) -> dict:
        stats = {"series_processed": 0, "episodes_unmonitored": 0, "series_new_only": 0, "errors": 0}

        try:
            series_list = await self.client.get_series()
        except Exception as e:
            logger.exception("Impossible de récupérer les séries Sonarr")
            await self._log_task("sonarr_monitor", "error", str(e))
            return stats

        for series in series_list:
            try:
                result = await self._process_series(series)
                stats["series_processed"] += 1
                stats["episodes_unmonitored"] += result["unmonitored"]
                if result["set_new_only"]:
                    stats["series_new_only"] += 1
            except Exception as e:
                logger.exception("Erreur traitement série %s", series.get("title"))
                stats["errors"] += 1

        await self._log_task(
            "sonarr_monitor",
            "success",
            f"{stats['series_processed']} séries, {stats['episodes_unmonitored']} épisodes désactivés, "
            f"{stats['series_new_only']} en nouveaux épisodes uniquement",
        )
        await self.client.close()
        return stats

    async def _process_series(self, series: dict) -> dict:
        series_id = series["id"]
        episodes = await self.client.get_episodes(series_id)

        downloaded_ids = [ep["id"] for ep in episodes if ep.get("hasFile") and ep.get("monitored")]
        unmonitored_count = 0

        if downloaded_ids:
            await self.client.set_episode_monitored(downloaded_ids, monitored=False)
            unmonitored_count = len(downloaded_ids)

        set_new_only = False
        if series.get("monitorNewItems") != "newItems":
            series["monitorNewItems"] = "newItems"
            await self.client.update_series(series)
            set_new_only = True

        return {"unmonitored": unmonitored_count, "set_new_only": set_new_only}

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

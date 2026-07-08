"""Gestion anime Sonarr : bascule standard si année précédente, retour anime après 1h."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sonarr import SonarrClient
from app.db.models import AnimeWatch, TaskLog

logger = logging.getLogger(__name__)

ANIME_WAIT_HOURS = 1


class AnimeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.client = SonarrClient()

    async def process(self) -> dict:
        stats = {"new_watches": 0, "reverted_to_anime": 0, "kept_standard": 0, "errors": 0}
        current_year = datetime.now(timezone.utc).year

        try:
            series_list = await self.client.get_series()
        except Exception as e:
            logger.exception("Erreur récupération séries pour anime")
            await self._log_task("anime_handler", "error", str(e))
            return stats

        existing = await self._get_active_watches()
        existing_ids = {w.sonarr_series_id for w in existing}

        for series in series_list:
            if series.get("seriesType") != "anime":
                continue

            try:
                year = series.get("year", current_year)
                if year >= current_year:
                    continue

                if series["id"] in existing_ids:
                    continue

                has_any_file = await self._series_has_files(series["id"])
                if has_any_file:
                    continue

                await self._switch_to_standard(series)
                self.db.add(
                    AnimeWatch(
                        sonarr_series_id=series["id"],
                        title=series["title"],
                    )
                )
                stats["new_watches"] += 1
            except Exception:
                logger.exception("Erreur anime série %s", series.get("title"))
                stats["errors"] += 1

        await self.db.commit()

        cutoff = datetime.now(timezone.utc) - timedelta(hours=ANIME_WAIT_HOURS)
        for watch in existing:
            if watch.switched_at > cutoff:
                continue

            try:
                series = await self.client.get_series_by_id(watch.sonarr_series_id)
                has_file = await self._series_has_files(watch.sonarr_series_id)

                if has_file:
                    series["seriesType"] = "anime"
                    await self.client.update_series(series)
                    watch.resolved = True
                    watch.resolved_at = datetime.now(timezone.utc)
                    stats["reverted_to_anime"] += 1
                else:
                    watch.resolved = True
                    watch.resolved_at = datetime.now(timezone.utc)
                    stats["kept_standard"] += 1
            except Exception:
                logger.exception("Erreur résolution watch anime %s", watch.title)
                stats["errors"] += 1

        await self.db.commit()
        await self._log_task("anime_handler", "success", str(stats))
        await self.client.close()
        return stats

    async def _get_active_watches(self) -> list[AnimeWatch]:
        result = await self.db.execute(
            select(AnimeWatch).where(AnimeWatch.resolved.is_(False))
        )
        return list(result.scalars().all())

    async def _series_has_files(self, series_id: int) -> bool:
        episodes = await self.client.get_episodes(series_id)
        return any(ep.get("hasFile") for ep in episodes)

    async def _switch_to_standard(self, series: dict) -> None:
        series["seriesType"] = "standard"
        await self.client.update_series(series)
        logger.info("Série %s basculée en standard (année %s)", series["title"], series.get("year"))

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

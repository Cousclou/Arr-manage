"""Gestion anime Sonarr : bascule standard si année précédente, retour anime après délai."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sonarr import SonarrClient
from app.db.models import AnimeWatch
from app.services.runtime_config import RuntimeConfig
from app.services.task_logger import TaskLogger

logger = logging.getLogger(__name__)


class AnimeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def process(self) -> dict:
        tlog = TaskLogger(self.db, "anime_handler")
        cfg = self.config
        if not await cfg.get_bool("task_anime_handler_enabled") or not await cfg.get_bool("anime_enabled"):
            await tlog.finish("skipped", "Tâche désactivée")
            return {"skipped": True}

        stats = {"new_watches": 0, "reverted_to_anime": 0, "kept_standard": 0, "errors": 0, "dry_run": False}
        dry_run = await cfg.get_bool("dry_run")
        stats["dry_run"] = dry_run
        wait_hours = await cfg.get_int("anime_wait_hours")
        current_year = datetime.now(timezone.utc).year

        client = SonarrClient(
            base_url=await cfg.get("sonarr_url"),
            api_key=await cfg.get("sonarr_api_key"),
        )

        try:
            series_list = await client.get_series()
        except Exception as e:
            logger.exception("Erreur récupération séries pour anime")
            tlog.detail("error", "Connexion Sonarr", info=str(e))
            await tlog.finish("error", str(e))
            await client.close()
            return stats

        existing = await self._get_active_watches()
        existing_ids = {w.sonarr_series_id for w in existing}

        for series in series_list:
            if series.get("seriesType") != "anime":
                continue

            title = series.get("title", "?")
            try:
                year = series.get("year", current_year)
                if year >= current_year:
                    continue
                if series["id"] in existing_ids:
                    continue

                has_any_file = await self._series_has_files(client, series["id"])
                if has_any_file:
                    continue

                if not dry_run:
                    await self._switch_to_standard(client, series)
                self.db.add(AnimeWatch(sonarr_series_id=series["id"], title=title))
                tlog.detail("anime_switch", title, series["id"], f"Année {year} → standard")
                stats["new_watches"] += 1
            except Exception as e:
                logger.exception("Erreur anime série %s", title)
                tlog.detail("error", title, series.get("id"), str(e))
                stats["errors"] += 1

        await self.db.commit()

        cutoff = datetime.now(timezone.utc) - timedelta(hours=wait_hours)
        for watch in existing:
            if watch.switched_at > cutoff:
                continue

            try:
                series = await client.get_series_by_id(watch.sonarr_series_id)
                has_file = await self._series_has_files(client, watch.sonarr_series_id)

                if has_file and not dry_run:
                    series["seriesType"] = "anime"
                    await client.update_series(series)
                    watch.resolved = True
                    watch.resolved_at = datetime.now(timezone.utc)
                    tlog.detail("anime_revert", watch.title, watch.sonarr_series_id, "Fichier trouvé → anime")
                    stats["reverted_to_anime"] += 1
                else:
                    watch.resolved = True
                    watch.resolved_at = datetime.now(timezone.utc)
                    tlog.detail("anime_keep", watch.title, watch.sonarr_series_id, "Reste en standard")
                    stats["kept_standard"] += 1
            except Exception as e:
                logger.exception("Erreur résolution watch anime %s", watch.title)
                tlog.detail("error", watch.title, watch.sonarr_series_id, str(e))
                stats["errors"] += 1

        await self.db.commit()
        tlog.set_stats(stats)
        await tlog.finish("success")
        await client.close()
        return stats

    async def _get_active_watches(self) -> list[AnimeWatch]:
        result = await self.db.execute(select(AnimeWatch).where(AnimeWatch.resolved.is_(False)))
        return list(result.scalars().all())

    async def _series_has_files(self, client: SonarrClient, series_id: int) -> bool:
        episodes = await client.get_episodes(series_id)
        return any(ep.get("hasFile") for ep in episodes)

    async def _switch_to_standard(self, client: SonarrClient, series: dict) -> None:
        series["seriesType"] = "standard"
        await client.update_series(series)
        logger.info("Série %s basculée en standard (année %s)", series["title"], series.get("year"))

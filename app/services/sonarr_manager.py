"""Gestion du monitoring Sonarr : désactiver épisodes téléchargés, monitor new only."""

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sonarr import SonarrClient
from app.db.models import ExcludedMedia, TaskLog
from app.services.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)


class SonarrMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def process_all_series(self) -> dict:
        stats = {
            "series_processed": 0,
            "series_skipped": 0,
            "episodes_unmonitored": 0,
            "seasons_unmonitored": 0,
            "series_new_only": 0,
            "errors": 0,
            "dry_run": False,
        }

        cfg = self.config
        if not await cfg.get_bool("task_sonarr_monitor_enabled"):
            await self._log_task("sonarr_monitor", "skipped", "Tâche désactivée")
            return stats

        dry_run = await cfg.get_bool("dry_run")
        stats["dry_run"] = dry_run

        client = SonarrClient(
            base_url=await cfg.get("sonarr_url"),
            api_key=await cfg.get("sonarr_api_key"),
        )

        try:
            series_list = await client.get_series()
        except Exception as e:
            logger.exception("Impossible de récupérer les séries Sonarr")
            await self._log_task("sonarr_monitor", "error", str(e))
            await client.close()
            return stats

        exclude_tags = set(await cfg.get_int_list("sonarr_exclude_tag_ids"))
        excluded_ids = await self._get_excluded_ids()
        skip_continuing = await cfg.get_bool("sonarr_skip_continuing")
        skip_anime = await cfg.get_bool("sonarr_skip_anime")
        unmonitor_downloaded = await cfg.get_bool("sonarr_unmonitor_downloaded")
        set_new_only = await cfg.get_bool("sonarr_set_new_episodes_only")
        unmonitor_seasons = await cfg.get_bool("sonarr_unmonitor_complete_seasons")

        for series in series_list:
            series_id = series["id"]
            if series_id in excluded_ids:
                stats["series_skipped"] += 1
                continue

            series_tags = set(series.get("tags") or [])
            if exclude_tags and series_tags & exclude_tags:
                stats["series_skipped"] += 1
                continue

            if skip_continuing and series.get("status") == "continuing":
                stats["series_skipped"] += 1
                continue

            if skip_anime and series.get("seriesType") == "anime":
                stats["series_skipped"] += 1
                continue

            try:
                result = await self._process_series(
                    client,
                    series,
                    unmonitor_downloaded,
                    set_new_only,
                    unmonitor_seasons,
                    dry_run,
                )
                stats["series_processed"] += 1
                stats["episodes_unmonitored"] += result["unmonitored"]
                stats["seasons_unmonitored"] += result["seasons"]
                if result["set_new_only"]:
                    stats["series_new_only"] += 1
            except Exception:
                logger.exception("Erreur traitement série %s", series.get("title"))
                stats["errors"] += 1

        mode = " [DRY-RUN]" if dry_run else ""
        await self._log_task(
            "sonarr_monitor",
            "success",
            f"{stats['series_processed']} séries{mode}, {stats['episodes_unmonitored']} épisodes, "
            f"{stats['seasons_unmonitored']} saisons désactivés, {stats['series_skipped']} ignorées",
        )
        await client.close()
        return stats

    async def _get_excluded_ids(self) -> set[int]:
        result = await self.db.execute(
            select(ExcludedMedia.external_id).where(ExcludedMedia.service == "sonarr")
        )
        return set(result.scalars().all())

    async def _process_series(
        self,
        client: SonarrClient,
        series: dict,
        unmonitor_downloaded: bool,
        set_new_only_flag: bool,
        unmonitor_seasons: bool,
        dry_run: bool,
    ) -> dict:
        series_id = series["id"]
        episodes = await client.get_episodes(series_id)
        unmonitored_count = 0
        seasons_count = 0

        if unmonitor_downloaded:
            if unmonitor_seasons:
                by_season: dict[int, list[dict]] = defaultdict(list)
                for ep in episodes:
                    by_season[ep.get("seasonNumber", 0)].append(ep)

                ids_to_unmonitor: list[int] = []
                for season_eps in by_season.values():
                    if not season_eps:
                        continue
                    all_have_file = all(ep.get("hasFile") for ep in season_eps)
                    if all_have_file:
                        monitored = [ep["id"] for ep in season_eps if ep.get("monitored")]
                        ids_to_unmonitor.extend(monitored)
                        if monitored:
                            seasons_count += 1
                if ids_to_unmonitor and not dry_run:
                    await client.set_episode_monitored(ids_to_unmonitor, monitored=False)
                unmonitored_count = len(ids_to_unmonitor)
            else:
                downloaded_ids = [ep["id"] for ep in episodes if ep.get("hasFile") and ep.get("monitored")]
                if downloaded_ids and not dry_run:
                    await client.set_episode_monitored(downloaded_ids, monitored=False)
                unmonitored_count = len(downloaded_ids)

        set_new_only = False
        if set_new_only_flag and series.get("monitorNewItems") != "newItems":
            if not dry_run:
                series["monitorNewItems"] = "newItems"
                await client.update_series(series)
            set_new_only = True

        return {"unmonitored": unmonitored_count, "seasons": seasons_count, "set_new_only": set_new_only}

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

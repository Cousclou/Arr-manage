"""Gestion du monitoring Sonarr : désactiver épisodes téléchargés, monitor new only."""

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.sonarr import SonarrClient
from app.db.models import ExcludedMedia
from app.services.runtime_config import RuntimeConfig
from app.services.task_logger import TaskLogger

logger = logging.getLogger(__name__)


class SonarrMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def process_all_series(self) -> dict:
        tlog = TaskLogger(self.db, "sonarr_monitor")
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
            await tlog.finish("skipped", "Tâche désactivée")
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
            tlog.detail("error", "Connexion Sonarr", info=str(e))
            await tlog.finish("error", str(e))
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
            title = series.get("title", "?")

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
                    client, series, unmonitor_downloaded, set_new_only, unmonitor_seasons, dry_run, tlog
                )
                stats["series_processed"] += 1
                stats["episodes_unmonitored"] += result["unmonitored"]
                stats["seasons_unmonitored"] += result["seasons"]
                if result["set_new_only"]:
                    stats["series_new_only"] += 1
            except Exception as e:
                logger.exception("Erreur traitement série %s", title)
                tlog.detail("error", title, series_id, str(e))
                stats["errors"] += 1

        tlog.set_stats(stats)
        await tlog.finish("success")
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
        tlog: TaskLogger,
    ) -> dict:
        series_id = series["id"]
        title = series.get("title", "?")
        episodes = await client.get_episodes(series_id)
        unmonitored_count = 0
        seasons_count = 0

        if unmonitor_downloaded:
            if unmonitor_seasons:
                by_season: dict[int, list[dict]] = defaultdict(list)
                for ep in episodes:
                    by_season[ep.get("seasonNumber", 0)].append(ep)

                ids_to_unmonitor: list[int] = []
                for season_num, season_eps in by_season.items():
                    if not season_eps or season_num == 0:
                        continue
                    all_have_file = all(ep.get("hasFile") for ep in season_eps)
                    if all_have_file:
                        monitored = [ep["id"] for ep in season_eps if ep.get("monitored")]
                        ids_to_unmonitor.extend(monitored)
                        if monitored:
                            seasons_count += 1
                            tlog.detail(
                                "unmonitor_season",
                                title,
                                series_id,
                                f"S{season_num:02d} · {len(monitored)} épisodes",
                            )
                if ids_to_unmonitor and not dry_run:
                    await client.set_episode_monitored(ids_to_unmonitor, monitored=False)
                unmonitored_count = len(ids_to_unmonitor)
            else:
                downloaded = [ep for ep in episodes if ep.get("hasFile") and ep.get("monitored")]
                downloaded_ids = [ep["id"] for ep in downloaded]
                if downloaded_ids:
                    tlog.detail(
                        "unmonitor",
                        title,
                        series_id,
                        f"{len(downloaded_ids)} épisode(s)",
                    )
                if downloaded_ids and not dry_run:
                    await client.set_episode_monitored(downloaded_ids, monitored=False)
                unmonitored_count = len(downloaded_ids)

        set_new_only = False
        if set_new_only_flag and series.get("monitorNewItems") != "newItems":
            if not dry_run:
                series["monitorNewItems"] = "newItems"
                await client.update_series(series)
            tlog.detail("new_only", title, series_id, "Nouveaux épisodes uniquement")
            set_new_only = True

        return {"unmonitored": unmonitored_count, "seasons": seasons_count, "set_new_only": set_new_only}

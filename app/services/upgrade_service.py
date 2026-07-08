"""Vérification des versions plus légères (AV1 / H265 prioritaires)."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.config import get_settings
from app.db.models import MediaUpgradeRule, TaskLog
from app.services.codec_utils import bytes_to_gb, codec_score, detect_codec

logger = logging.getLogger(__name__)


class UpgradeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.sonarr = SonarrClient()
        self.radarr = RadarrClient()
        self.settings = get_settings()

    async def check_all(self) -> dict:
        threshold_gb = self.settings.upgrade_size_threshold_gb
        stats = {
            "sonarr_checked": 0,
            "radarr_checked": 0,
            "sonarr_upgrades": 0,
            "radarr_upgrades": 0,
            "errors": 0,
        }

        rules = await self._get_rules()

        try:
            sonarr_stats = await self._check_sonarr(threshold_gb, rules)
            stats.update(sonarr_stats)
        except Exception as e:
            logger.exception("Erreur vérification upgrades Sonarr")
            stats["errors"] += 1

        try:
            radarr_stats = await self._check_radarr(threshold_gb, rules)
            for k, v in radarr_stats.items():
                stats[k] = stats.get(k, 0) + v
        except Exception as e:
            logger.exception("Erreur vérification upgrades Radarr")
            stats["errors"] += 1

        await self._log_task("upgrade_check", "success", str(stats))
        await self.sonarr.close()
        await self.radarr.close()
        return stats

    async def _get_rules(self) -> dict[tuple[str, int], MediaUpgradeRule]:
        result = await self.db.execute(select(MediaUpgradeRule).where(MediaUpgradeRule.active.is_(True)))
        return {(r.service, r.external_id): r for r in result.scalars().all()}

    async def _check_sonarr(self, threshold_gb: float, rules: dict) -> dict:
        stats = {"sonarr_checked": 0, "sonarr_upgrades": 0}
        series_list = await self.sonarr.get_series()

        for series in series_list:
            episodes = await self.sonarr.get_episodes(series["id"])
            for ep in episodes:
                if not ep.get("hasFile") or not ep.get("episodeFileId"):
                    continue

                file_info = await self.sonarr.get_episode_file(ep["episodeFileId"])
                size_gb = bytes_to_gb(file_info.get("size", 0))
                rule = rules.get(("sonarr", series["id"]))
                min_size = rule.min_size_gb if rule and rule.min_size_gb else threshold_gb

                if size_gb < min_size:
                    continue

                stats["sonarr_checked"] += 1
                prefer = (rule.required_codec if rule else "av1") or "av1"

                releases = await self.sonarr.search_releases(ep["id"])
                better = self._find_better_release(releases, file_info.get("size", 0), prefer)
                if better:
                    stats["sonarr_upgrades"] += 1
                    logger.info(
                        "Upgrade Sonarr disponible: %s S%02dE%02d -> %s (%.2f Go vs %.2f Go)",
                        series["title"],
                        ep.get("seasonNumber", 0),
                        ep.get("episodeNumber", 0),
                        better.get("title"),
                        bytes_to_gb(better.get("size", 0)),
                        size_gb,
                    )

        return stats

    async def _check_radarr(self, threshold_gb: float, rules: dict) -> dict:
        stats = {"radarr_checked": 0, "radarr_upgrades": 0}
        movies = await self.radarr.get_movies()

        for movie in movies:
            if not movie.get("hasFile") or not movie.get("movieFile"):
                continue

            file_id = movie["movieFile"].get("id")
            if not file_id:
                continue

            file_info = await self.radarr.get_movie_file(file_id)
            size_gb = bytes_to_gb(file_info.get("size", 0))
            rule = rules.get(("radarr", movie["id"]))
            min_size = rule.min_size_gb if rule and rule.min_size_gb else threshold_gb

            if size_gb < min_size:
                continue

            stats["radarr_checked"] += 1
            prefer = (rule.required_codec if rule else "av1") or "av1"

            current_codec = None
            if file_info.get("mediaInfo", {}).get("videoCodec"):
                current_codec = detect_codec(file_info["mediaInfo"]["videoCodec"])

            releases = await self.radarr.search_releases(movie["id"])
            better = self._find_better_release(releases, file_info.get("size", 0), prefer, current_codec)
            if better:
                stats["radarr_upgrades"] += 1
                logger.info(
                    "Upgrade Radarr disponible: %s -> %s (%.2f Go vs %.2f Go)",
                    movie["title"],
                    better.get("title"),
                    bytes_to_gb(better.get("size", 0)),
                    size_gb,
                )

        return stats

    def _find_better_release(
        self,
        releases: list[dict],
        current_size: int,
        prefer: str,
        current_codec: str | None = None,
    ) -> dict | None:
        candidates = []
        for rel in releases:
            rel_size = rel.get("size", 0)
            if rel_size <= 0 or rel_size >= current_size:
                continue
            if rel.get("rejected"):
                continue

            title = rel.get("title", "")
            rel_codec = detect_codec(title)
            if current_codec and rel_codec == current_codec and rel_size >= current_size * 0.9:
                continue

            score = codec_score(title, prefer)
            savings = current_size - rel_size
            candidates.append((score, savings, rel))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

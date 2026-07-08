"""Gestion du monitoring Radarr : désactiver films téléchargés, exclusions par tag."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.radarr import RadarrClient
from app.config import get_settings
from app.db.models import MediaUpgradeRule, TaskLog

logger = logging.getLogger(__name__)


class RadarrMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.client = RadarrClient()
        self.settings = get_settings()

    async def process_all_movies(self) -> dict:
        stats = {"processed": 0, "unmonitored": 0, "skipped_tags": 0, "skipped_rules": 0, "errors": 0}
        exclude_tags = set(self.settings.radarr_exclude_tags)

        try:
            movies = await self.client.get_movies()
        except Exception as e:
            logger.exception("Impossible de récupérer les films Radarr")
            await self._log_task("radarr_monitor", "error", str(e))
            return stats

        upgrade_rules = await self._get_active_rules()

        for movie in movies:
            try:
                result = await self._process_movie(movie, exclude_tags, upgrade_rules)
                stats["processed"] += 1
                stats["unmonitored"] += result["unmonitored"]
                stats["skipped_tags"] += result["skipped_tag"]
                stats["skipped_rules"] += result["skipped_rule"]
            except Exception as e:
                logger.exception("Erreur traitement film %s", movie.get("title"))
                stats["errors"] += 1

        await self._log_task(
            "radarr_monitor",
            "success",
            f"{stats['processed']} films, {stats['unmonitored']} désactivés, "
            f"{stats['skipped_tags']} exclus par tag",
        )
        await self.client.close()
        return stats

    async def _get_active_rules(self) -> dict[int, MediaUpgradeRule]:
        result = await self.db.execute(
            select(MediaUpgradeRule).where(
                MediaUpgradeRule.service == "radarr",
                MediaUpgradeRule.active.is_(True),
            )
        )
        return {r.external_id: r for r in result.scalars().all()}

    async def _process_movie(
        self,
        movie: dict,
        exclude_tags: set[int],
        upgrade_rules: dict[int, MediaUpgradeRule],
    ) -> dict:
        movie_id = movie["id"]
        movie_tags = set(movie.get("tags") or [])
        skipped_tag = 0
        skipped_rule = 0
        unmonitored = 0

        if exclude_tags and movie_tags & exclude_tags:
            skipped_tag = 1
            return {"unmonitored": 0, "skipped_tag": skipped_tag, "skipped_rule": 0}

        rule = upgrade_rules.get(movie_id)
        if rule and rule.required_codec != "any":
            if not movie.get("hasFile"):
                skipped_rule = 1
                return {"unmonitored": 0, "skipped_tag": 0, "skipped_rule": skipped_rule}

        if movie.get("hasFile") and movie.get("monitored"):
            movie["monitored"] = False
            await self.client.update_movie(movie)
            unmonitored = 1

        return {"unmonitored": unmonitored, "skipped_tag": skipped_tag, "skipped_rule": skipped_rule}

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

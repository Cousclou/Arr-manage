"""Journalisation structurée des tâches avec détails média optimisés."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TaskLog, TaskLogDetail

logger = logging.getLogger(__name__)

MAX_DETAILS_PER_RUN = 150
LOG_RETENTION_DAYS = 30

SERVICE_MAP = {
    "sonarr_monitor": "sonarr",
    "radarr_monitor": "radarr",
    "upgrade_check": "upgrade",
    "import_monitor": "import",
    "anime_handler": "anime",
    "wanted_search": "search",
}

ACTION_LABELS = {
    "unmonitor": "Suivi désactivé",
    "unmonitor_season": "Saison désactivée",
    "new_only": "Nouveaux épisodes",
    "upgrade": "Upgrade disponible",
    "kept_upgrade": "Gardé (upgrade)",
    "skipped": "Ignoré",
    "anime_switch": "Bascule standard",
    "anime_revert": "Retour anime",
    "anime_keep": "Reste standard",
    "alert": "Alerte import",
    "error": "Erreur",
    "progress": "En cours",
    "found": "Trouvé",
    "grab": "Téléchargé",
}


class TaskLogger:
    """Accumule stats + détails média, flush en une seule transaction."""

    def __init__(self, db: AsyncSession, task_name: str, service: str | None = None) -> None:
        self.db = db
        self.task_name = task_name
        self.service = service or SERVICE_MAP.get(task_name, "system")
        self.stats: dict = {}
        self._details: list[dict] = []
        self._truncated = False
        self._log_id: int | None = None
        self._live = False

    async def begin(self, summary: str = "En cours…") -> int:
        """Crée une entrée de journal en statut running pour suivi en direct."""
        log = TaskLog(
            task_name=self.task_name,
            service=self.service,
            status="running",
            message=summary,
            details_count=0,
            details_truncated=False,
        )
        self.db.add(log)
        await self.db.flush()
        self._log_id = log.id
        self._live = True
        return log.id

    async def resume(self, log_id: int) -> None:
        """Reprend un journal existant en mode live."""
        self._log_id = log_id
        self._live = True

    def set_stats(self, stats: dict) -> None:
        self.stats = stats

    def detail(
        self,
        action: str,
        media_title: str,
        external_id: int | None = None,
        info: str | None = None,
    ) -> None:
        if len(self._details) >= MAX_DETAILS_PER_RUN:
            self._truncated = True
            return
        entry = {
            "action": action,
            "media_title": media_title[:500],
            "external_id": external_id,
            "detail": (info or "")[:500] or None,
        }
        self._details.append(entry)
        if self._live and self._log_id:
            import asyncio
            try:
                asyncio.get_running_loop().create_task(self._flush_detail(entry))
            except RuntimeError:
                pass

    async def _flush_detail(self, entry: dict) -> None:
        self.db.add(TaskLogDetail(
            log_id=self._log_id,
            action=entry["action"],
            media_title=entry["media_title"],
            external_id=entry["external_id"],
            detail=entry["detail"],
        ))
        log = await self.db.get(TaskLog, self._log_id)
        if log:
            log.details_count = len(self._details)
            log.details_truncated = self._truncated
        await self.db.commit()

    async def finish(self, status: str, summary: str | None = None) -> int:
        if not summary:
            summary = _build_summary(self.task_name, self.stats, self._truncated)

        if self._log_id:
            log = await self.db.get(TaskLog, self._log_id)
            if log:
                log.status = status
                log.message = summary
                log.stats_json = json.dumps(self.stats, ensure_ascii=False) if self.stats else None
                log.details_count = len(self._details)
                log.details_truncated = self._truncated
                await self.db.commit()
                await _cleanup_old_logs(self.db)
                return log.id

        log = TaskLog(
            task_name=self.task_name,
            service=self.service,
            status=status,
            message=summary,
            stats_json=json.dumps(self.stats, ensure_ascii=False) if self.stats else None,
            details_count=len(self._details),
            details_truncated=self._truncated,
        )
        self.db.add(log)
        await self.db.flush()

        if self._details:
            self.db.add_all([
                TaskLogDetail(
                    log_id=log.id,
                    action=d["action"],
                    media_title=d["media_title"],
                    external_id=d["external_id"],
                    detail=d["detail"],
                )
                for d in self._details
            ])

        await self.db.commit()
        await _cleanup_old_logs(self.db)
        return log.id


def _build_summary(task_name: str, stats: dict, truncated: bool) -> str:
    parts: list[str] = []
    if task_name == "sonarr_monitor":
        parts.append(f"{stats.get('series_processed', 0)} séries traitées")
        if stats.get("episodes_unmonitored"):
            parts.append(f"{stats['episodes_unmonitored']} épisodes désactivés")
        if stats.get("seasons_unmonitored"):
            parts.append(f"{stats['seasons_unmonitored']} saisons")
        if stats.get("series_new_only"):
            parts.append(f"{stats['series_new_only']} en nouveaux ép. uniquement")
        if stats.get("series_skipped"):
            parts.append(f"{stats['series_skipped']} ignorées")
    elif task_name == "radarr_monitor":
        parts.append(f"{stats.get('processed', 0)} films traités")
        if stats.get("unmonitored"):
            parts.append(f"{stats['unmonitored']} désactivés")
        if stats.get("kept_for_upgrade"):
            parts.append(f"{stats['kept_for_upgrade']} gardés (upgrade)")
    elif task_name == "upgrade_check":
        parts.append(f"{stats.get('sonarr_upgrades', 0)} upgrades Sonarr")
        parts.append(f"{stats.get('radarr_upgrades', 0)} upgrades Radarr")
    elif task_name == "import_monitor":
        parts.append(f"{stats.get('sonarr_alerts', 0)} alertes Sonarr")
        parts.append(f"{stats.get('radarr_alerts', 0)} alertes Radarr")
    elif task_name == "anime_handler":
        parts.append(f"{stats.get('new_watches', 0)} bascules")
        parts.append(f"{stats.get('reverted_to_anime', 0)} retours anime")
    elif task_name == "wanted_search":
        parts.append(f"{stats.get('sonarr_series', 0)} séries Sonarr")
        parts.append(f"{stats.get('radarr_movies', 0)} films Radarr")
        if stats.get("sonarr_grabbed") or stats.get("radarr_grabbed"):
            parts.append(f"{stats.get('sonarr_grabbed', 0) + stats.get('radarr_grabbed', 0)} grabs")
        found = stats.get("sonarr_found", 0) + stats.get("radarr_found", 0)
        if found:
            parts.append(f"{found} releases trouvées")
        if stats.get("sonarr_notified") or stats.get("radarr_notified"):
            parts.append(f"{stats.get('sonarr_notified', 0) + stats.get('radarr_notified', 0)} alertes seeders")
    else:
        parts.append(str(stats))

    if stats.get("dry_run"):
        parts.append("simulation")
    if truncated:
        parts.append(f"détails limités à {MAX_DETAILS_PER_RUN}")
    if stats.get("errors"):
        parts.append(f"{stats['errors']} erreurs")

    return " · ".join(parts) if parts else "Exécution terminée"


async def _cleanup_old_logs(db: AsyncSession) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
    try:
        old_ids = await db.execute(
            select(TaskLog.id).where(TaskLog.created_at < cutoff).limit(500)
        )
        ids = list(old_ids.scalars().all())
        if ids:
            await db.execute(delete(TaskLogDetail).where(TaskLogDetail.log_id.in_(ids)))
            await db.execute(delete(TaskLog).where(TaskLog.id.in_(ids)))
            await db.commit()
    except Exception:
        logger.exception("Erreur nettoyage anciens logs")


async def get_log_details(db: AsyncSession, log_id: int) -> tuple[TaskLog | None, list[TaskLogDetail]]:
    log = await db.get(TaskLog, log_id)
    if not log:
        return None, []
    result = await db.execute(
        select(TaskLogDetail)
        .where(TaskLogDetail.log_id == log_id)
        .order_by(TaskLogDetail.id)
        .limit(MAX_DETAILS_PER_RUN + 1)
    )
    return log, list(result.scalars().all())

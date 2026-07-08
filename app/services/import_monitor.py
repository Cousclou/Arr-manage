"""Surveillance des échecs d'import et notifications Pushover."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.db.models import IgnoredImport, ImportAlert, TaskLog

logger = logging.getLogger(__name__)

IMPORT_ERROR_STATUSES = {"warning", "error", "failed"}
IMPORT_TRACKED = "downloadFailed"


class ImportMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.sonarr = SonarrClient()
        self.radarr = RadarrClient()
        self.pushover = PushoverClient()

    async def check_imports(self) -> dict:
        stats = {"sonarr_alerts": 0, "radarr_alerts": 0, "ignored": 0, "errors": 0}
        ignored = await self._get_ignored_set()

        try:
            sonarr_count = await self._check_sonarr_queue(ignored)
            stats["sonarr_alerts"] = sonarr_count
        except Exception as e:
            logger.exception("Erreur surveillance imports Sonarr")
            stats["errors"] += 1

        try:
            radarr_count = await self._check_radarr_queue(ignored)
            stats["radarr_alerts"] = radarr_count
        except Exception as e:
            logger.exception("Erreur surveillance imports Radarr")
            stats["errors"] += 1

        await self._log_task("import_monitor", "success", str(stats))
        await self.sonarr.close()
        await self.radarr.close()
        return stats

    async def _get_ignored_set(self) -> set[tuple[str, int]]:
        result = await self.db.execute(select(IgnoredImport))
        return {(i.service, i.external_id) for i in result.scalars().all()}

    async def _get_notified_keys(self) -> set[str]:
        result = await self.db.execute(select(ImportAlert.alert_key))
        return set(result.scalars().all())

    async def _mark_notified(self, service: str, external_id: int, alert_key: str) -> None:
        self.db.add(ImportAlert(service=service, external_id=external_id, alert_key=alert_key))
        await self.db.commit()

    async def _notify_if_new(
        self,
        service: str,
        external_id: int,
        alert_key: str,
        title: str,
        message: str,
        ignored: set[tuple[str, int]],
        notified: set[str],
    ) -> bool:
        if (service, external_id) in ignored:
            return False
        if alert_key in notified:
            return False

        sent = await self.pushover.send(title, message, priority=1)
        if sent:
            await self._mark_notified(service, external_id, alert_key)
            notified.add(alert_key)
            return True
        return False

    async def _check_sonarr_queue(self, ignored: set[tuple[str, int]]) -> int:
        alerts = 0
        notified = await self._get_notified_keys()
        queue = await self.sonarr.get_queue()
        records = queue.get("records", [])

        for item in records:
            status = (item.get("status") or "").lower()
            tracked_status = (item.get("trackedDownloadStatus") or "").lower()
            item_id = item.get("id", 0)

            if ("import" in status and status in IMPORT_ERROR_STATUSES) or tracked_status == IMPORT_TRACKED:
                title = item.get("title") or item.get("series", {}).get("title", "Inconnu")
                path = item.get("outputPath") or item.get("downloadId", "")
                alert_key = f"sonarr:queue:{item_id}"

                if await self._notify_if_new(
                    "sonarr",
                    item_id,
                    alert_key,
                    "Sonarr - Import manuel requis",
                    f"{title}\nChemin: {path}\nStatut: {status or tracked_status}",
                    ignored,
                    notified,
                ):
                    alerts += 1

        history = await self.sonarr.get_history(page=1, page_size=20)
        for record in history.get("records", []):
            if record.get("eventType") != "downloadFailed":
                continue
            item_id = record.get("id", 0)
            source_title = record.get("sourceTitle", "Inconnu")
            alert_key = f"sonarr:history:{item_id}"

            if await self._notify_if_new(
                "sonarr",
                item_id,
                alert_key,
                "Sonarr - Téléchargement échoué",
                f"{source_title}\nAction manuelle requise",
                ignored,
                notified,
            ):
                alerts += 1

        return alerts

    async def _check_radarr_queue(self, ignored: set[tuple[str, int]]) -> int:
        alerts = 0
        notified = await self._get_notified_keys()
        queue = await self.radarr.get_queue()
        records = queue.get("records", [])

        for item in records:
            status = (item.get("status") or "").lower()
            tracked_status = (item.get("trackedDownloadStatus") or "").lower()
            item_id = item.get("id", 0)

            if ("import" in status and status in IMPORT_ERROR_STATUSES) or tracked_status == IMPORT_TRACKED:
                title = item.get("title") or item.get("movie", {}).get("title", "Inconnu")
                path = item.get("outputPath") or item.get("downloadId", "")
                alert_key = f"radarr:queue:{item_id}"

                if await self._notify_if_new(
                    "radarr",
                    item_id,
                    alert_key,
                    "Radarr - Import manuel requis",
                    f"{title}\nChemin: {path}\nStatut: {status or tracked_status}",
                    ignored,
                    notified,
                ):
                    alerts += 1

        history = await self.radarr.get_history(page=1, page_size=20)
        for record in history.get("records", []):
            if record.get("eventType") not in ("downloadFailed", "downloadImportFailed"):
                continue
            item_id = record.get("id", 0)
            source_title = record.get("sourceTitle", "Inconnu")
            alert_key = f"radarr:history:{item_id}"

            if await self._notify_if_new(
                "radarr",
                item_id,
                alert_key,
                "Radarr - Import/Téléchargement échoué",
                f"{source_title}\nAction manuelle requise",
                ignored,
                notified,
            ):
                alerts += 1

        return alerts

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

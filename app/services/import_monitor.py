"""Surveillance des échecs d'import et notifications Pushover."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.db.models import IgnoredImport, ImportAlert, TaskLog
from app.services.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

IMPORT_ERROR_STATUSES = {"warning", "error", "failed"}
IMPORT_TRACKED = "downloadfailed"


class ImportMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def check_imports(self) -> dict:
        cfg = self.config
        if not await cfg.get_bool("task_import_monitor_enabled"):
            await self._log_task("import_monitor", "skipped", "Tâche désactivée")
            return {"skipped": True}

        stats = {"sonarr_alerts": 0, "radarr_alerts": 0, "ignored": 0, "errors": 0}
        notify_enabled = await cfg.get_bool("import_notify_enabled")
        check_queue = await cfg.get_bool("import_check_queue")
        check_history = await cfg.get_bool("import_check_history")

        sonarr = SonarrClient(
            base_url=await cfg.get("sonarr_url"),
            api_key=await cfg.get("sonarr_api_key"),
        )
        radarr = RadarrClient(
            base_url=await cfg.get("radarr_url"),
            api_key=await cfg.get("radarr_api_key"),
        )
        pushover = PushoverClient(
            user_key=await cfg.get("pushover_user_key"),
            api_token=await cfg.get("pushover_api_token"),
        )

        ignored = await self._get_ignored_set()

        try:
            if check_queue or check_history:
                stats["sonarr_alerts"] = await self._check_sonarr(
                    sonarr, pushover, ignored, notify_enabled, check_queue, check_history
                )
        except Exception:
            logger.exception("Erreur surveillance imports Sonarr")
            stats["errors"] += 1

        try:
            if check_queue or check_history:
                stats["radarr_alerts"] = await self._check_radarr(
                    radarr, pushover, ignored, notify_enabled, check_queue, check_history
                )
        except Exception:
            logger.exception("Erreur surveillance imports Radarr")
            stats["errors"] += 1

        await self._log_task("import_monitor", "success", str(stats))
        await sonarr.close()
        await radarr.close()
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
        pushover: PushoverClient,
        service: str,
        external_id: int,
        alert_key: str,
        title: str,
        message: str,
        ignored: set[tuple[str, int]],
        notified: set[str],
        notify_enabled: bool,
    ) -> bool:
        if not notify_enabled:
            return False
        if (service, external_id) in ignored:
            return False
        if alert_key in notified:
            return False

        sent = await pushover.send(title, message, priority=1)
        if sent:
            await self._mark_notified(service, external_id, alert_key)
            notified.add(alert_key)
            return True
        return False

    async def _check_sonarr(
        self,
        client: SonarrClient,
        pushover: PushoverClient,
        ignored: set[tuple[str, int]],
        notify_enabled: bool,
        check_queue: bool,
        check_history: bool,
    ) -> int:
        alerts = 0
        notified = await self._get_notified_keys()

        if check_queue:
            queue = await client.get_queue()
            for item in queue.get("records", []):
                status = (item.get("status") or "").lower()
                tracked_status = (item.get("trackedDownloadStatus") or "").lower()
                item_id = item.get("id", 0)

                if ("import" in status and status in IMPORT_ERROR_STATUSES) or tracked_status == IMPORT_TRACKED:
                    title = item.get("title") or item.get("series", {}).get("title", "Inconnu")
                    path = item.get("outputPath") or item.get("downloadId", "")
                    if await self._notify_if_new(
                        pushover, "sonarr", item_id, f"sonarr:queue:{item_id}",
                        "Sonarr - Import manuel requis",
                        f"{title}\nChemin: {path}\nStatut: {status or tracked_status}",
                        ignored, notified, notify_enabled,
                    ):
                        alerts += 1

        if check_history:
            history = await client.get_history(page=1, page_size=20)
            for record in history.get("records", []):
                if record.get("eventType") != "downloadFailed":
                    continue
                item_id = record.get("id", 0)
                source_title = record.get("sourceTitle", "Inconnu")
                if await self._notify_if_new(
                    pushover, "sonarr", item_id, f"sonarr:history:{item_id}",
                    "Sonarr - Téléchargement échoué",
                    f"{source_title}\nAction manuelle requise",
                    ignored, notified, notify_enabled,
                ):
                    alerts += 1

        return alerts

    async def _check_radarr(
        self,
        client: RadarrClient,
        pushover: PushoverClient,
        ignored: set[tuple[str, int]],
        notify_enabled: bool,
        check_queue: bool,
        check_history: bool,
    ) -> int:
        alerts = 0
        notified = await self._get_notified_keys()

        if check_queue:
            queue = await client.get_queue()
            for item in queue.get("records", []):
                status = (item.get("status") or "").lower()
                tracked_status = (item.get("trackedDownloadStatus") or "").lower()
                item_id = item.get("id", 0)

                if ("import" in status and status in IMPORT_ERROR_STATUSES) or tracked_status == IMPORT_TRACKED:
                    title = item.get("title") or item.get("movie", {}).get("title", "Inconnu")
                    path = item.get("outputPath") or item.get("downloadId", "")
                    if await self._notify_if_new(
                        pushover, "radarr", item_id, f"radarr:queue:{item_id}",
                        "Radarr - Import manuel requis",
                        f"{title}\nChemin: {path}\nStatut: {status or tracked_status}",
                        ignored, notified, notify_enabled,
                    ):
                        alerts += 1

        if check_history:
            history = await client.get_history(page=1, page_size=20)
            for record in history.get("records", []):
                if record.get("eventType") not in ("downloadFailed", "downloadImportFailed"):
                    continue
                item_id = record.get("id", 0)
                source_title = record.get("sourceTitle", "Inconnu")
                if await self._notify_if_new(
                    pushover, "radarr", item_id, f"radarr:history:{item_id}",
                    "Radarr - Import/Téléchargement échoué",
                    f"{source_title}\nAction manuelle requise",
                    ignored, notified, notify_enabled,
                ):
                    alerts += 1

        return alerts

    async def _log_task(self, name: str, status: str, message: str) -> None:
        self.db.add(TaskLog(task_name=name, status=status, message=message))
        await self.db.commit()

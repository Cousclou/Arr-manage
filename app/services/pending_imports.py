"""Récupération des médias en attente d'import manuel depuis Sonarr/Radarr."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient

logger = logging.getLogger(__name__)

IMPORT_ERROR_STATUSES = {"warning", "error", "failed"}
IMPORT_TRACKED = "downloadfailed"


@dataclass
class PendingImport:
    service: str
    queue_id: int
    title: str
    path: str
    status: str
    error_message: str
    size_bytes: int | None = None


def _is_failed_item(item: dict) -> bool:
    status = (item.get("status") or "").lower()
    tracked = (item.get("trackedDownloadStatus") or "").lower()
    if tracked == IMPORT_TRACKED:
        return True
    return "import" in status and status in IMPORT_ERROR_STATUSES


def _extract_error(item: dict) -> str:
    messages = item.get("statusMessages") or []
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, dict):
            title = msg.get("title") or ""
            messages_list = msg.get("messages") or []
            if title:
                parts.append(title)
            parts.extend(str(m) for m in messages_list)
        elif isinstance(msg, str):
            parts.append(msg)
    if parts:
        return " · ".join(parts[:3])
    status = item.get("status") or item.get("trackedDownloadStatus") or "inconnu"
    return str(status)


def _parse_sonarr_item(item: dict) -> PendingImport:
    title = item.get("title") or item.get("series", {}).get("title", "Inconnu")
    path = item.get("outputPath") or item.get("downloadId") or ""
    return PendingImport(
        service="sonarr",
        queue_id=item.get("id", 0),
        title=title,
        path=path,
        status=(item.get("status") or item.get("trackedDownloadStatus") or "").lower(),
        error_message=_extract_error(item),
        size_bytes=item.get("size"),
    )


def _parse_radarr_item(item: dict) -> PendingImport:
    title = item.get("title") or item.get("movie", {}).get("title", "Inconnu")
    path = item.get("outputPath") or item.get("downloadId") or ""
    return PendingImport(
        service="radarr",
        queue_id=item.get("id", 0),
        title=title,
        path=path,
        status=(item.get("status") or item.get("trackedDownloadStatus") or "").lower(),
        error_message=_extract_error(item),
        size_bytes=item.get("size"),
    )


async def fetch_pending_imports(
    sonarr: SonarrClient | None = None,
    radarr: RadarrClient | None = None,
) -> tuple[list[PendingImport], dict[str, str]]:
    """Retourne les imports en échec et les erreurs de connexion éventuelles."""
    results: list[PendingImport] = []
    errors: dict[str, str] = {}

    if sonarr:
        try:
            queue = await sonarr.get_queue()
            for item in queue.get("records", []):
                if _is_failed_item(item):
                    results.append(_parse_sonarr_item(item))
        except Exception as e:
            logger.exception("Erreur lecture queue Sonarr")
            errors["sonarr"] = str(e)

    if radarr:
        try:
            queue = await radarr.get_queue()
            for item in queue.get("records", []):
                if _is_failed_item(item):
                    results.append(_parse_radarr_item(item))
        except Exception as e:
            logger.exception("Erreur lecture queue Radarr")
            errors["radarr"] = str(e)

    return results, errors

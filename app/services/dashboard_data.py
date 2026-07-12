"""Données agrégées pour le tableau de bord."""

from __future__ import annotations

import logging
from datetime import datetime

from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient

logger = logging.getLogger(__name__)


def _parse_history_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _quality_label(record: dict) -> str | None:
    quality = record.get("quality") or {}
    if isinstance(quality.get("quality"), dict):
        return quality["quality"].get("name")
    if isinstance(quality, dict):
        return quality.get("name")
    return None


async def fetch_recent_grabs(settings: dict, limit: int = 12) -> list[dict]:
    """Dernières prises (grab) Sonarr et Radarr via l'historique *arr."""
    grabs: list[dict] = []

    sonarr = SonarrClient(
        base_url=settings.get("sonarr_url"),
        api_key=settings.get("sonarr_api_key"),
    )
    if sonarr.configured:
        try:
            history = await sonarr.get_history(page=1, page_size=40, event_type="grabbed")
            for record in history.get("records", []):
                series = record.get("series") or {}
                episode = record.get("episode") or {}
                season = episode.get("seasonNumber")
                ep_num = episode.get("episodeNumber")
                ep_label = ""
                if season is not None and ep_num is not None:
                    ep_label = f" S{season:02d}E{ep_num:02d}"
                grabs.append({
                    "service": "sonarr",
                    "date": record.get("date"),
                    "title": series.get("title") or record.get("sourceTitle", "?"),
                    "detail": f"{record.get('sourceTitle', '')}{ep_label}".strip(),
                    "quality": _quality_label(record),
                    "client": (record.get("data") or {}).get("downloadClient"),
                })
        except Exception as e:
            logger.warning("Historique grabs Sonarr: %s", e)
        finally:
            await sonarr.close()

    radarr = RadarrClient(
        base_url=settings.get("radarr_url"),
        api_key=settings.get("radarr_api_key"),
    )
    if radarr.configured:
        try:
            history = await radarr.get_history(page=1, page_size=40, event_type="grabbed")
            for record in history.get("records", []):
                movie = record.get("movie") or {}
                grabs.append({
                    "service": "radarr",
                    "date": record.get("date"),
                    "title": movie.get("title") or record.get("sourceTitle", "?"),
                    "detail": record.get("sourceTitle", ""),
                    "quality": _quality_label(record),
                    "client": (record.get("data") or {}).get("downloadClient"),
                })
        except Exception as e:
            logger.warning("Historique grabs Radarr: %s", e)
        finally:
            await radarr.close()

    grabs.sort(
        key=lambda g: _parse_history_date(g.get("date")) or datetime.min.replace(tzinfo=None),
        reverse=True,
    )
    return grabs[:limit]

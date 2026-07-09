"""Vérification des versions plus légères (AV1 / H265 prioritaires)."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.db.models import MediaUpgradeRule
from app.services.codec_utils import bytes_to_gb, codec_score, detect_codec
from app.services.runtime_config import RuntimeConfig
from app.services.task_logger import TaskLogger

logger = logging.getLogger(__name__)


class UpgradeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def check_all(self) -> dict:
        tlog = TaskLogger(self.db, "upgrade_check")
        cfg = self.config
        if not await cfg.get_bool("task_upgrade_check_enabled"):
            await tlog.finish("skipped", "Tâche désactivée")
            return {"skipped": True}

        stats = {
            "sonarr_checked": 0,
            "radarr_checked": 0,
            "sonarr_upgrades": 0,
            "radarr_upgrades": 0,
            "searches_triggered": 0,
            "notifications": 0,
            "errors": 0,
            "dry_run": await cfg.get_bool("dry_run"),
        }

        rules = await self._get_rules()
        threshold_gb = await cfg.get_float("upgrade_size_threshold_gb")
        min_savings = await cfg.get_float("upgrade_min_savings_percent")
        prefer = await cfg.get("upgrade_preferred_codec")
        check_sonarr = await cfg.get_bool("upgrade_check_sonarr")
        check_radarr = await cfg.get_bool("upgrade_check_radarr")
        auto_search = await cfg.get_bool("upgrade_auto_search")
        notify = await cfg.get_bool("upgrade_notify_pushover")
        dry_run = stats["dry_run"]

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

        if check_sonarr:
            try:
                s = await self._check_sonarr(sonarr, threshold_gb, min_savings, prefer, rules, tlog)
                stats["sonarr_checked"] = s["checked"]
                stats["sonarr_upgrades"] = s["upgrades"]
                if auto_search and s["search_ids"] and not dry_run:
                    await sonarr.trigger_episode_search(s["search_ids"])
                    stats["searches_triggered"] += 1
                if notify and s["messages"] and pushover.configured:
                    for msg in s["messages"]:
                        if await pushover.send("MediaGuard - Upgrade Sonarr", msg):
                            stats["notifications"] += 1
            except Exception as e:
                logger.exception("Erreur vérification upgrades Sonarr")
                tlog.detail("error", "Sonarr upgrades", info=str(e))
                stats["errors"] += 1

        if check_radarr:
            try:
                r = await self._check_radarr(radarr, threshold_gb, min_savings, prefer, rules, tlog)
                stats["radarr_checked"] = r["checked"]
                stats["radarr_upgrades"] = r["upgrades"]
                if auto_search and r["search_ids"] and not dry_run:
                    await radarr.trigger_movie_search(r["search_ids"])
                    stats["searches_triggered"] += 1
                if notify and r["messages"] and pushover.configured:
                    for msg in r["messages"]:
                        if await pushover.send("MediaGuard - Upgrade Radarr", msg):
                            stats["notifications"] += 1
            except Exception as e:
                logger.exception("Erreur vérification upgrades Radarr")
                tlog.detail("error", "Radarr upgrades", info=str(e))
                stats["errors"] += 1

        tlog.set_stats(stats)
        await tlog.finish("success")
        await sonarr.close()
        await radarr.close()
        return stats

    async def has_radarr_upgrade(self, movie: dict, client: RadarrClient) -> bool:
        if not movie.get("hasFile") or not movie.get("movieFile"):
            return False
        file_id = movie["movieFile"].get("id")
        if not file_id:
            return False

        threshold_gb = await self.config.get_float("upgrade_size_threshold_gb")
        min_savings = await self.config.get_float("upgrade_min_savings_percent")
        prefer = await self.config.get("upgrade_preferred_codec")
        rules = await self._get_rules()

        file_info = await client.get_movie_file(file_id)
        size_gb = bytes_to_gb(file_info.get("size", 0))
        rule = rules.get(("radarr", movie["id"]))
        min_size = rule.min_size_gb if rule and rule.min_size_gb else threshold_gb
        if size_gb < min_size:
            return False

        codec_pref = (rule.required_codec if rule else prefer) or "av1"
        current_codec = None
        if file_info.get("mediaInfo", {}).get("videoCodec"):
            current_codec = detect_codec(file_info["mediaInfo"]["videoCodec"])

        releases = await client.search_releases(movie["id"])
        better = self._find_better_release(
            releases, file_info.get("size", 0), codec_pref, min_savings, current_codec
        )
        return better is not None

    async def _get_rules(self) -> dict[tuple[str, int], MediaUpgradeRule]:
        result = await self.db.execute(select(MediaUpgradeRule).where(MediaUpgradeRule.active.is_(True)))
        return {(r.service, r.external_id): r for r in result.scalars().all()}

    async def _check_sonarr(
        self,
        client: SonarrClient,
        threshold_gb: float,
        min_savings: float,
        prefer: str,
        rules: dict,
        tlog: TaskLogger,
    ) -> dict:
        result = {"checked": 0, "upgrades": 0, "search_ids": [], "messages": []}
        series_list = await client.get_series()

        for series in series_list:
            title = series.get("title", "?")
            episodes = await client.get_episodes(series["id"])
            for ep in episodes:
                if not ep.get("hasFile") or not ep.get("episodeFileId"):
                    continue

                file_info = await client.get_episode_file(ep["episodeFileId"])
                size_gb = bytes_to_gb(file_info.get("size", 0))
                rule = rules.get(("sonarr", series["id"]))
                min_size = rule.min_size_gb if rule and rule.min_size_gb else threshold_gb
                if size_gb < min_size:
                    continue

                result["checked"] += 1
                codec_pref = (rule.required_codec if rule else prefer) or "av1"
                releases = await client.search_releases(ep["id"])
                better = self._find_better_release(releases, file_info.get("size", 0), codec_pref, min_savings)
                if better:
                    result["upgrades"] += 1
                    result["search_ids"].append(ep["id"])
                    ep_label = f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
                    savings = size_gb - bytes_to_gb(better.get("size", 0))
                    info = f"{ep_label} · {bytes_to_gb(better.get('size', 0)):.1f} Go (-{savings:.1f} Go)"
                    tlog.detail("upgrade", title, series["id"], info)
                    msg = f"{title} {ep_label}\n{better.get('title')} ({bytes_to_gb(better.get('size', 0)):.2f} Go)"
                    result["messages"].append(msg)

        return result

    async def _check_radarr(
        self,
        client: RadarrClient,
        threshold_gb: float,
        min_savings: float,
        prefer: str,
        rules: dict,
        tlog: TaskLogger,
    ) -> dict:
        result = {"checked": 0, "upgrades": 0, "search_ids": [], "messages": []}
        movies = await client.get_movies()

        for movie in movies:
            title = movie.get("title", "?")
            if not movie.get("hasFile") or not movie.get("movieFile"):
                continue
            file_id = movie["movieFile"].get("id")
            if not file_id:
                continue

            file_info = await client.get_movie_file(file_id)
            size_gb = bytes_to_gb(file_info.get("size", 0))
            rule = rules.get(("radarr", movie["id"]))
            min_size = rule.min_size_gb if rule and rule.min_size_gb else threshold_gb
            if size_gb < min_size:
                continue

            result["checked"] += 1
            codec_pref = (rule.required_codec if rule else prefer) or "av1"
            current_codec = None
            if file_info.get("mediaInfo", {}).get("videoCodec"):
                current_codec = detect_codec(file_info["mediaInfo"]["videoCodec"])

            releases = await client.search_releases(movie["id"])
            better = self._find_better_release(
                releases, file_info.get("size", 0), codec_pref, min_savings, current_codec
            )
            if better:
                result["upgrades"] += 1
                result["search_ids"].append(movie["id"])
                savings = size_gb - bytes_to_gb(better.get("size", 0))
                info = f"{bytes_to_gb(better.get('size', 0)):.1f} Go (-{savings:.1f} Go)"
                tlog.detail("upgrade", title, movie["id"], info)
                msg = f"{title}\n{better.get('title')} ({bytes_to_gb(better.get('size', 0)):.2f} Go)"
                result["messages"].append(msg)

        return result

    def _find_better_release(
        self,
        releases: list[dict],
        current_size: int,
        prefer: str,
        min_savings_percent: float,
        current_codec: str | None = None,
    ) -> dict | None:
        min_size = current_size * (1 - min_savings_percent / 100)
        candidates = []
        for rel in releases:
            rel_size = rel.get("size", 0)
            if rel_size <= 0 or rel_size >= min_size:
                continue
            if rel.get("rejected"):
                continue

            title = rel.get("title", "")
            rel_codec = detect_codec(title)
            if current_codec and rel_codec == current_codec and rel_size >= current_size * 0.9:
                continue

            if prefer != "any":
                rel_codec_detected = detect_codec(title)
                if prefer == "av1" and rel_codec_detected not in ("av1", "h265", None):
                    continue

            score = codec_score(title, prefer if prefer != "any" else "av1")
            savings = current_size - rel_size
            candidates.append((score, savings, rel))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidates[0][2]

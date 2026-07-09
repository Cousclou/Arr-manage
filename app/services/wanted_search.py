"""Recherche intelligente des médias wanted Sonarr/Radarr."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.db.models import SearchAlert
from app.services.runtime_config import RuntimeConfig
from app.services.task_logger import TaskLogger
from app.utils.timezone import now_local

logger = logging.getLogger(__name__)

SEASON_PACK_RE = re.compile(
    r"\b(S\d{2}(?!E\d)|Season\s*\d+|Complete|Integral|Saison\s*\d+|Season Pack)\b",
    re.IGNORECASE,
)


def release_seeders(release: dict) -> int | None:
    """Retourne le nombre de seeders ou None si inconnu (-1 / absent)."""
    seeders = release.get("seeders")
    if seeders is None or seeders < 0:
        return None
    return int(seeders)


def seeders_meet_minimum(release: dict, min_seeders: int) -> bool:
    seeders = release_seeders(release)
    if seeders is None:
        return True
    return seeders >= min_seeders


@dataclass
class WantedEpisode:
    series_id: int
    series_title: str
    series_year: int
    episode_id: int
    season: int
    episode: int


@dataclass
class WantedMovie:
    movie_id: int
    title: str
    year: int


class WantedSearchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def run(
        self,
        series_id: int | None = None,
        movie_id: int | None = None,
        only_service: str | None = None,
        log_id: int | None = None,
    ) -> dict:
        tlog = TaskLogger(self.db, "wanted_search", service="search")
        if log_id:
            await tlog.resume(log_id)
        cfg = self.config

        manual = series_id is not None or movie_id is not None or only_service is not None
        if not await cfg.get_bool("task_wanted_search_enabled") and not manual:
            await tlog.finish("skipped", "Tâche désactivée")
            return {"skipped": True, "log_id": None}

        stats = {
            "sonarr_series": 0,
            "sonarr_grabbed": 0,
            "sonarr_notified": 0,
            "sonarr_found": 0,
            "radarr_movies": 0,
            "radarr_grabbed": 0,
            "radarr_notified": 0,
            "radarr_found": 0,
            "errors": 0,
            "dry_run": await cfg.get_bool("dry_run"),
        }

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

        min_seeders = await cfg.get_int("search_min_seeders")
        notify_low = await cfg.get_bool("search_notify_low_seeders")
        auto_grab = await cfg.get_bool("search_auto_grab")
        prefer_pack = await cfg.get_bool("search_prefer_season_pack")
        season_first = await cfg.get_bool("search_old_series_season_first")
        dry_run = stats["dry_run"]

        if only_service == "sonarr":
            sonarr_should = sonarr.configured
            radarr_should = False
        elif only_service == "radarr":
            sonarr_should = False
            radarr_should = radarr.configured
        else:
            sonarr_should = bool(series_id) or (
                await cfg.get_bool("search_sonarr_enabled") and sonarr.configured
            )
            radarr_should = bool(movie_id) or (
                await cfg.get_bool("search_radarr_enabled") and radarr.configured
            )

        if series_id is None and movie_id is None and not sonarr_should and not radarr_should:
            await tlog.finish("skipped", "Aucun service activé ou configuré")
            await sonarr.close()
            await radarr.close()
            return {"skipped": True, "log_id": None}

        if series_id and not sonarr.configured:
            tlog.detail("error", "Sonarr search", info="Sonarr non configuré")
            stats["errors"] += 1
        elif sonarr_should:
            try:
                s = await self._search_sonarr(
                    sonarr, pushover, series_id, min_seeders, notify_low, auto_grab,
                    prefer_pack, season_first, dry_run, tlog,
                )
                stats.update(s)
            except Exception as e:
                logger.exception("Erreur recherche Sonarr")
                tlog.detail("error", "Sonarr search", info=str(e))
                stats["errors"] += 1

        if movie_id and not radarr.configured:
            tlog.detail("error", "Radarr search", info="Radarr non configuré")
            stats["errors"] += 1
        elif radarr_should:
            try:
                r = await self._search_radarr(
                    radarr, pushover, movie_id, min_seeders, notify_low, auto_grab, dry_run, tlog,
                )
                for k, v in r.items():
                    stats[k] = stats.get(k, 0) + v
            except Exception as e:
                logger.exception("Erreur recherche Radarr")
                tlog.detail("error", "Radarr search", info=str(e))
                stats["errors"] += 1

        tlog.set_stats(stats)
        log_id = await tlog.finish("success")
        await sonarr.close()
        await radarr.close()
        return {**stats, "log_id": log_id}

    async def _search_sonarr(
        self,
        client: SonarrClient,
        pushover: PushoverClient,
        series_id: int | None,
        min_seeders: int,
        notify_low: bool,
        auto_grab: bool,
        prefer_pack: bool,
        season_first: bool,
        dry_run: bool,
        tlog: TaskLogger,
    ) -> dict:
        stats = {"sonarr_series": 0, "sonarr_grabbed": 0, "sonarr_notified": 0, "sonarr_found": 0}
        tz = await self.config.get_timezone()
        current_year = now_local(tz).year

        if series_id:
            series_list = [await client.get_series_by_id(series_id)]
        else:
            series_list = await client.get_series()

        for series in series_list:
            if not series.get("monitored"):
                continue

            sid = series["id"]
            title = series.get("title", "?")
            year = series.get("year") or current_year
            episodes = await client.get_episodes(sid)
            missing = [
                WantedEpisode(sid, title, year, ep["id"], ep.get("seasonNumber", 0), ep.get("episodeNumber", 0))
                for ep in episodes
                if ep.get("monitored") and not ep.get("hasFile")
            ]
            if not missing:
                continue

            stats["sonarr_series"] += 1
            is_old = year < current_year
            use_season_first = is_old and season_first
            tlog.detail(
                "progress", title, sid,
                f"Analyse · {len(missing)} épisode(s) manquant(s)"
                + (" · stratégie season pack" if use_season_first else ""),
            )

            if use_season_first:
                by_season: dict[int, list[WantedEpisode]] = defaultdict(list)
                for ep in missing:
                    by_season[ep.season].append(ep)

                for season_num, season_eps in sorted(by_season.items()):
                    if season_num == 0:
                        for ep in season_eps:
                            g, n, f = await self._search_episode(
                                client, pushover, series, ep, min_seeders, notify_low,
                                auto_grab, prefer_pack, dry_run, tlog,
                            )
                            stats["sonarr_grabbed"] += g
                            stats["sonarr_notified"] += n
                            stats["sonarr_found"] += f
                        continue

                    tlog.detail("progress", title, sid, f"S{season_num:02d} · Recherche season pack…")
                    releases = await client.search_season_releases(sid, season_num)
                    pack = self._best_release(releases, min_seeders, prefer_season_pack=True, season_only=True)

                    if pack:
                        first_ep = season_eps[0]
                        g, n, f = await self._handle_release(
                            client, pushover, title, first_ep.episode_id, pack,
                            min_seeders, notify_low, auto_grab, dry_run, tlog,
                            f"S{season_num:02d} season pack",
                        )
                        stats["sonarr_grabbed"] += g
                        stats["sonarr_notified"] += n
                        stats["sonarr_found"] += f
                        if g:
                            continue

                    for ep in season_eps:
                        g, n, f = await self._search_episode(
                            client, pushover, series, ep, min_seeders, notify_low,
                            auto_grab, prefer_pack, dry_run, tlog,
                        )
                        stats["sonarr_grabbed"] += g
                        stats["sonarr_notified"] += n
                        stats["sonarr_found"] += f
                        await asyncio.sleep(0.5)
            else:
                by_season_eps: dict[int, list[WantedEpisode]] = defaultdict(list)
                for ep in missing:
                    by_season_eps[ep.season].append(ep)

                for season_eps in by_season_eps.values():
                    pack_done = False
                    if prefer_pack and season_eps:
                        first = season_eps[0]
                        releases = await client.search_season_releases(sid, first.season)
                        pack = self._best_release(releases, min_seeders, prefer_season_pack=True, season_only=True)
                        if pack:
                            g, n, f = await self._handle_release(
                                client, pushover, title, first.episode_id, pack,
                                min_seeders, notify_low, auto_grab, dry_run, tlog,
                                f"S{first.season:02d}E{first.episode:02d} via pack",
                            )
                            stats["sonarr_grabbed"] += g
                            stats["sonarr_notified"] += n
                            stats["sonarr_found"] += f
                            if g:
                                pack_done = True

                    if not pack_done:
                        for ep in season_eps:
                            g, n, f = await self._search_episode(
                                client, pushover, series, ep, min_seeders, notify_low,
                                auto_grab, prefer_pack, dry_run, tlog,
                            )
                            stats["sonarr_grabbed"] += g
                            stats["sonarr_notified"] += n
                            stats["sonarr_found"] += f
                            await asyncio.sleep(0.5)

        return stats

    async def _search_episode(
        self,
        client: SonarrClient,
        pushover: PushoverClient,
        series: dict,
        ep: WantedEpisode,
        min_seeders: int,
        notify_low: bool,
        auto_grab: bool,
        prefer_pack: bool,
        dry_run: bool,
        tlog: TaskLogger,
    ) -> tuple[int, int, int]:
        label = f"S{ep.season:02d}E{ep.episode:02d}"
        tlog.detail("progress", ep.series_title, ep.series_id, f"{label} · Recherche indexeurs…")
        releases = await client.search_releases(ep.episode_id)
        best = self._best_release(releases, min_seeders, prefer_pack, season_only=False)
        if not best:
            if releases:
                sample = releases[0].get("title", "?")[:80]
                tlog.detail(
                    "skipped", ep.series_title, ep.series_id,
                    f"{label} · {len(releases)} release(s) sans candidat exploitable ({sample})",
                )
            else:
                tlog.detail("skipped", ep.series_title, ep.series_id, f"{label} · Aucune release")
            return 0, 0, 0

        g, n, f = await self._handle_release(
            client, pushover, ep.series_title, ep.episode_id, best,
            min_seeders, notify_low, auto_grab, dry_run, tlog, label,
        )
        return g, n, f

    async def _search_radarr(
        self,
        client: RadarrClient,
        pushover: PushoverClient,
        movie_id: int | None,
        min_seeders: int,
        notify_low: bool,
        auto_grab: bool,
        dry_run: bool,
        tlog: TaskLogger,
    ) -> dict:
        stats = {"radarr_movies": 0, "radarr_grabbed": 0, "radarr_notified": 0}
        tz = await self.config.get_timezone()
        current_year = now_local(tz).year

        if movie_id:
            movies = [await client.get_movie(movie_id)]
        else:
            movies = await client.get_movies()

        for movie in movies:
            if not movie.get("monitored") or movie.get("hasFile"):
                continue

            mid = movie["id"]
            title = movie.get("title", "?")
            year = movie.get("year") or current_year
            stats["radarr_movies"] += 1

            releases = await client.search_releases(mid)
            best = self._best_release(releases, min_seeders, prefer_season_pack=False, season_only=False)

            if not best:
                if year < current_year:
                    tlog.detail("skipped", title, mid, "Aucune release trouvée (film ancien)")
                else:
                    tlog.detail("skipped", title, mid, "Aucune release trouvée")
                continue

            seeders = release_seeders(best)
            rel_title = best.get("title", "?")

            if not seeders_meet_minimum(best, min_seeders):
                seeders_label = seeders if seeders is not None else "?"
                note = f"{rel_title} ({seeders_label} seeders, min {min_seeders})"
                if best.get("rejections") or best.get("rejected"):
                    note += " · rejetée par Radarr"
                if notify_low and seeders is not None:
                    notified = await self._notify_low_seeders(
                        pushover, "radarr", mid, title,
                        f"{rel_title} ({seeders} seeders, min {min_seeders})",
                    )
                    if notified:
                        stats["radarr_notified"] += 1
                        tlog.detail("alert", title, mid, f"Peu de seeders: {seeders_label}")
                tlog.detail("found", title, mid, note)
                stats["radarr_found"] += 1
                await asyncio.sleep(0.5)
                continue

            if auto_grab and not dry_run:
                best["movieId"] = mid
                await client.grab_release(best)
                stats["radarr_grabbed"] += 1
                tlog.detail("grab", title, mid, rel_title)
            else:
                seeders_label = seeders if seeders is not None else "?"
                note = f"{rel_title} ({seeders_label} seeders)"
                if dry_run:
                    note += " · simulation"
                elif not auto_grab:
                    note += " · auto-grab désactivé"
                tlog.detail("found", title, mid, note)
                stats["radarr_found"] += 1

            await asyncio.sleep(0.5)

        return stats

    async def _handle_release(
        self,
        client: SonarrClient,
        pushover: PushoverClient,
        series_title: str,
        episode_id: int,
        release: dict,
        min_seeders: int,
        notify_low: bool,
        auto_grab: bool,
        dry_run: bool,
        tlog: TaskLogger,
        label: str,
    ) -> tuple[int, int, int]:
        grabbed = notified = 0
        seeders = release_seeders(release)
        rel_title = release.get("title", "?")
        series_id = release.get("seriesId") or 0
        seeders_label = seeders if seeders is not None else "?"

        if not seeders_meet_minimum(release, min_seeders):
            note = f"{label} · {rel_title} ({seeders_label} seeders, min {min_seeders})"
            if release.get("rejections") or release.get("rejected"):
                note += " · rejetée par Sonarr"
            if notify_low and seeders is not None:
                ok = await self._notify_low_seeders(
                    pushover, "sonarr", series_id, series_title,
                    f"{label}: {rel_title} ({seeders} seeders, min {min_seeders})",
                    release.get("guid", ""),
                )
                if ok:
                    notified = 1
                    tlog.detail("alert", series_title, series_id, f"{label} · {seeders} seeders")
            tlog.detail("found", series_title, series_id, note)
            return grabbed, notified, 1

        if auto_grab and not dry_run:
            release["episodeId"] = episode_id
            await client.grab_release(release)
            grabbed = 1
            tlog.detail("grab", series_title, series_id, f"{label} · {rel_title}")
            return grabbed, notified, 0

        note = f"{label} · {rel_title} ({seeders_label} seeders)"
        if release.get("rejections") or release.get("rejected"):
            note += " · rejetée par Sonarr"
        if dry_run:
            note += " · simulation"
        elif not auto_grab:
            note += " · auto-grab désactivé"
        tlog.detail("found", series_title, series_id, note)
        return grabbed, notified, 1

    def _best_release(
        self,
        releases: list[dict],
        min_seeders: int,
        prefer_season_pack: bool,
        season_only: bool,
    ) -> dict | None:
        accepted: list[tuple[int, dict]] = []
        fallback: list[tuple[int, dict]] = []

        for rel in releases:
            seeders = release_seeders(rel)
            is_pack = self._is_season_pack(rel)
            if season_only and not is_pack:
                continue

            score = seeders if seeders is not None else 0
            if prefer_season_pack and is_pack:
                score += 10000

            entry = (score, rel)
            if rel.get("rejections") or rel.get("rejected"):
                fallback.append(entry)
            else:
                accepted.append(entry)

        pool = accepted or fallback
        if not pool:
            return None

        pool.sort(key=lambda x: x[0], reverse=True)
        return pool[0][1]

    def _is_season_pack(self, release: dict) -> bool:
        ep_nums = release.get("episodeNumbers") or []
        if len(ep_nums) > 1:
            return True
        title = release.get("title", "")
        return bool(SEASON_PACK_RE.search(title))

    async def _notify_low_seeders(
        self,
        pushover: PushoverClient,
        service: str,
        external_id: int,
        title: str,
        message: str,
        guid: str = "",
    ) -> bool:
        alert_key = f"search:{service}:{external_id}:{guid or title}"
        result = await self.db.execute(
            select(SearchAlert).where(SearchAlert.alert_key == alert_key)
        )
        if result.scalar_one_or_none():
            return False

        sent = await pushover.send(
            f"MediaGuard - Peu de seeders ({service})",
            f"{title}\n{message}",
            priority=0,
        )
        if sent:
            self.db.add(SearchAlert(
                service=service,
                external_id=external_id,
                alert_key=alert_key,
            ))
            await self.db.commit()
        return sent


async def list_wanted_preview(
    sonarr: SonarrClient | None,
    radarr: RadarrClient | None,
) -> dict:
    """Aperçu des médias wanted pour l'interface (via API wanted/missing)."""
    preview: dict = {
        "sonarr": [],
        "radarr": [],
        "sonarr_count": 0,
        "sonarr_episodes": 0,
        "radarr_count": 0,
        "errors": {},
    }

    if sonarr and sonarr.configured:
        try:
            by_series: dict[int, dict] = {}
            page = 1
            while True:
                data = await sonarr.get_wanted_missing(page=page, page_size=250)
                records = data.get("records", [])
                for ep in records:
                    series = ep.get("series") or {}
                    sid = series.get("id") or ep.get("seriesId")
                    if not sid:
                        continue
                    if sid not in by_series:
                        by_series[sid] = {
                            "id": sid,
                            "title": series.get("title") or f"Série #{sid}",
                            "year": series.get("year"),
                            "tvdb": series.get("tvdbId"),
                            "tmdb": series.get("tmdbId"),
                            "missing": 0,
                        }
                    by_series[sid]["missing"] += 1

                total = data.get("totalRecords", 0)
                page_size = data.get("pageSize", 250)
                if page * page_size >= total or not records:
                    break
                page += 1

            preview["sonarr"] = sorted(
                by_series.values(),
                key=lambda s: (s.get("title") or "").lower(),
            )
            preview["sonarr_count"] = len(preview["sonarr"])
            preview["sonarr_episodes"] = sum(s["missing"] for s in preview["sonarr"])
        except Exception as e:
            logger.exception("Preview Sonarr wanted")
            preview["errors"]["sonarr"] = str(e)

    if radarr and radarr.configured:
        try:
            movies: list[dict] = []
            page = 1
            while True:
                data = await radarr.get_wanted_missing(page=page, page_size=250)
                records = data.get("records", [])
                for record in records:
                    movie = record.get("movie") or record
                    movies.append({
                        "id": movie.get("id") or record.get("id"),
                        "title": movie.get("title") or "?",
                        "year": movie.get("year"),
                        "tmdb": movie.get("tmdbId"),
                        "tvdb": movie.get("tvdbId"),
                    })

                total = data.get("totalRecords", 0)
                page_size = data.get("pageSize", 250)
                if page * page_size >= total or not records:
                    break
                page += 1

            preview["radarr"] = sorted(
                movies,
                key=lambda m: (m.get("title") or "").lower(),
            )
            preview["radarr_count"] = len(preview["radarr"])
        except Exception as e:
            logger.exception("Preview Radarr wanted")
            preview["errors"]["radarr"] = str(e)

    return preview

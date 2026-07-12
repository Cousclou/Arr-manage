"""Surveillance santé indexeurs via Sonarr/Radarr + Prowlarr."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.arr_http import friendly_connection_error, normalize_arr_url
from app.clients.prowlarr import ProwlarrClient
from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.db.models import ExcludedIndexer, IndexerAlert, IndexerHealthState
from app.services.runtime_config import RuntimeConfig
from app.services.task_logger import TaskLogger

logger = logging.getLogger(__name__)

INDEXER_HEALTH_SOURCES = {
    "IndexerStatusCheck",
    "IndexerSearchCheck",
    "IndexerRssCheck",
}

NAME_FROM_MESSAGE_RE = re.compile(r":\s*(.+?)(?:\s*$|\s*\(|\.|$)", re.IGNORECASE)


def normalize_indexer_name(name: str) -> str:
    return (name or "").strip().lower()


def extract_indexer_name_from_health(message: str) -> str | None:
    """Extrait le nom d'indexeur depuis un message health Sonarr/Radarr."""
    if not message:
        return None
    match = NAME_FROM_MESSAGE_RE.search(message)
    if match:
        return match.group(1).strip()
    if len(message) < 80 and ":" not in message:
        return message.strip()
    return None


class IndexerHealthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.config = RuntimeConfig(db)

    async def check(self) -> dict:
        """Remédiation ciblée : ne teste que les indexeurs signalés KO par Sonarr/Radarr."""
        tlog = TaskLogger(self.db, "indexer_health", service="prowlarr")
        cfg = self.config

        if not await cfg.get_bool("task_indexer_health_enabled"):
            await tlog.finish("skipped", "Remédiation désactivée")
            return {"skipped": True}

        if not await cfg.get_bool("prowlarr_enabled"):
            await tlog.finish("skipped", "Prowlarr désactivé")
            return {"skipped": True}

        stats = {
            "mode": "remediation",
            "checked": 0,
            "down_arr": 0,
            "down_prowlarr": 0,
            "retested": 0,
            "recovered": 0,
            "alerts": 0,
            "skipped": 0,
            "errors": 0,
        }

        prowlarr = ProwlarrClient(
            base_url=await cfg.get("prowlarr_url"),
            api_key=await cfg.get("prowlarr_api_key"),
        )
        if not prowlarr.configured:
            await tlog.finish("skipped", "Prowlarr non configuré")
            return {"skipped": True}

        pushover = PushoverClient(
            user_key=await cfg.get("pushover_user_key"),
            api_token=await cfg.get("pushover_api_token"),
        )
        notify_fail = await cfg.get_bool("indexer_notify_on_failure")
        notify_recovery = await cfg.get_bool("indexer_notify_on_recovery")
        excluded = await self._get_excluded_names()

        try:
            prowlarr_indexers = await prowlarr.get_indexers()
            prowlarr_by_name = {
                normalize_indexer_name(i.get("name", "")): i
                for i in prowlarr_indexers
                if i.get("name")
            }
            prowlarr_status = await self._prowlarr_blocked_map(prowlarr)

            down_targets: dict[str, dict] = {}

            if await cfg.get_bool("indexer_health_check_sonarr"):
                sonarr = SonarrClient(
                    base_url=await cfg.get("sonarr_url"),
                    api_key=await cfg.get("sonarr_api_key"),
                )
                if sonarr.configured:
                    try:
                        await self._collect_arr_down(
                            sonarr, "sonarr", excluded, down_targets, tlog,
                        )
                    except Exception as e:
                        logger.exception("Erreur collecte indexeurs Sonarr")
                        tlog.detail("error", "Sonarr indexers", info=str(e))
                        stats["errors"] += 1
                    finally:
                        await sonarr.close()

            if await cfg.get_bool("indexer_health_check_radarr"):
                radarr = RadarrClient(
                    base_url=await cfg.get("radarr_url"),
                    api_key=await cfg.get("radarr_api_key"),
                )
                if radarr.configured:
                    try:
                        await self._collect_arr_down(
                            radarr, "radarr", excluded, down_targets, tlog,
                        )
                    except Exception as e:
                        logger.exception("Erreur collecte indexeurs Radarr")
                        tlog.detail("error", "Radarr indexers", info=str(e))
                        stats["errors"] += 1
                    finally:
                        await radarr.close()

            if not down_targets:
                tlog.detail("found", "Remédiation", None, "Aucun indexeur KO — aucun test lancé")
                tlog.set_stats(stats)
                await tlog.finish("success", "Aucun indexeur KO")
                await prowlarr.close()
                return stats

            tlog.detail(
                "progress", "Remédiation", None,
                f"{len(down_targets)} indexeur(s) KO · tests ciblés uniquement",
            )

            for norm_name, target in down_targets.items():
                stats["checked"] += 1
                name = target["name"]
                if norm_name in excluded:
                    stats["skipped"] += 1
                    tlog.detail("skipped", name, None, "Exclu du monitoring")
                    continue

                prowlarr_idx = prowlarr_by_name.get(norm_name)
                if not prowlarr_idx:
                    stats["skipped"] += 1
                    tlog.detail("skipped", name, None, "Introuvable dans Prowlarr")
                    continue

                if not prowlarr_idx.get("enable", True):
                    stats["skipped"] += 1
                    tlog.detail("skipped", name, prowlarr_idx.get("id"), "Désactivé dans Prowlarr")
                    continue

                stats["down_arr"] += 1
                prowlarr_blocked = prowlarr_status.get(prowlarr_idx["id"], False)
                if prowlarr_blocked:
                    stats["down_prowlarr"] += 1

                prev = await self._get_state(name)
                recovered = False
                prowlarr_ok: bool | None = None
                sonarr_ok: bool | None = None
                radarr_ok: bool | None = None
                message_parts: list[str] = []

                for svc in target["services"]:
                    message_parts.append(f"{svc}: {target['services'][svc]}")

                prowlarr_id = prowlarr_idx.get("id")
                if prowlarr_blocked:
                    tlog.detail(
                        "progress", name, prowlarr_id,
                        f"KO Prowlarr · POST /indexer/test id={prowlarr_id}…",
                    )
                    prowlarr_ok = await prowlarr.test_indexer_by_id(
                        prowlarr_id, force=True,
                    )
                    stats["retested"] += 1
                    if prowlarr_ok:
                        message_parts.append("Prowlarr: récupéré après test")
                    else:
                        message_parts.append("Prowlarr: toujours KO")
                        await self._maybe_alert(
                            pushover, name, f"indexer:down:{name}",
                            f"Indexeur KO (Prowlarr)\n{name}\n" + " · ".join(message_parts),
                            notify_fail, stats,
                        )
                        await self._save_state(
                            name, prowlarr_idx.get("id"), "down",
                            target.get("sonarr_ok"), target.get("radarr_ok"), False,
                            " · ".join(message_parts),
                        )
                        tlog.detail("alert", name, prowlarr_idx.get("id"), " · ".join(message_parts))
                        continue

                affected = list(target["arr_indexers"].keys())
                tlog.detail(
                    "progress", name, prowlarr_idx.get("id"),
                    f"Re-test ciblé · {', '.join(affected)} uniquement",
                )

                if "sonarr" in target["arr_indexers"]:
                    sonarr_idx = target["arr_indexers"]["sonarr"]
                    sonarr_idx_id = sonarr_idx.get("id")
                    tlog.detail(
                        "progress", name, sonarr_idx_id,
                        f"Sonarr · POST /indexer/test id={sonarr_idx_id}…",
                    )
                    sonarr_ok = await self._test_arr_indexer_by_id(
                        SonarrClient(
                            base_url=await cfg.get("sonarr_url"),
                            api_key=await cfg.get("sonarr_api_key"),
                        ),
                        sonarr_idx_id,
                        service="sonarr",
                    )
                    stats["retested"] += 1
                    message_parts.append(
                        f"Sonarr: {'OK' if sonarr_ok else 'KO'} après test"
                    )

                if "radarr" in target["arr_indexers"]:
                    radarr_idx = target["arr_indexers"]["radarr"]
                    radarr_idx_id = radarr_idx.get("id")
                    radarr_ok = await self._test_arr_indexer_by_id(
                        RadarrClient(
                            base_url=await cfg.get("radarr_url"),
                            api_key=await cfg.get("radarr_api_key"),
                        ),
                        radarr_idx_id,
                        service="radarr",
                    )
                    stats["retested"] += 1
                    message_parts.append(
                        f"Radarr: {'OK' if radarr_ok else 'KO'} après test"
                    )

                all_ok = True
                if sonarr_ok is False or radarr_ok is False:
                    all_ok = False

                if all_ok and (sonarr_ok is True or radarr_ok is True):
                    recovered = True
                    stats["recovered"] += 1
                    status = "ok"
                    tlog.detail("found", name, prowlarr_idx.get("id"), " · ".join(message_parts))
                    if prev and prev.status == "down" and notify_recovery:
                        await self._notify_recovery(pushover, name, message_parts, stats)
                        await self._clear_alert(f"indexer:down:{name}")
                elif not all_ok:
                    status = "down"
                    tlog.detail("alert", name, prowlarr_idx.get("id"), " · ".join(message_parts))
                    await self._maybe_alert(
                        pushover, name, f"indexer:down:{name}",
                        f"Indexeur KO\n{name}\n" + " · ".join(message_parts),
                        notify_fail, stats,
                    )
                else:
                    status = "unknown"

                await self._save_state(
                    name, prowlarr_idx.get("id"), status,
                    sonarr_ok if sonarr_ok is not None else target.get("sonarr_ok"),
                    radarr_ok if radarr_ok is not None else target.get("radarr_ok"),
                    prowlarr_ok if prowlarr_ok is not None else (not prowlarr_blocked),
                    " · ".join(message_parts),
                )

        except Exception as e:
            logger.exception("Erreur surveillance indexeurs")
            tlog.detail("error", "Indexer health", info=str(e))
            stats["errors"] += 1
        finally:
            await prowlarr.close()

        tlog.set_stats(stats)
        await tlog.finish("success")
        return stats

    async def check_global(self) -> dict:
        """Check global : teste tous les indexeurs (testall) sur Prowlarr et *arr."""
        tlog = TaskLogger(self.db, "indexer_global_check", service="prowlarr")
        cfg = self.config

        if not await cfg.get_bool("task_indexer_global_check_enabled"):
            await tlog.finish("skipped", "Check global désactivé")
            return {"skipped": True}

        if not await cfg.get_bool("prowlarr_enabled"):
            await tlog.finish("skipped", "Prowlarr désactivé")
            return {"skipped": True}

        stats = {
            "mode": "global",
            "prowlarr_testall": 0,
            "sonarr_testall": 0,
            "radarr_testall": 0,
            "errors": 0,
        }

        prowlarr = ProwlarrClient(
            base_url=await cfg.get("prowlarr_url"),
            api_key=await cfg.get("prowlarr_api_key"),
        )
        if not prowlarr.configured:
            await tlog.finish("skipped", "Prowlarr non configuré")
            return {"skipped": True}

        try:
            if await cfg.get_bool("indexer_global_check_prowlarr"):
                tlog.detail("progress", "Prowlarr", None, "Test global de tous les indexeurs…")
                if await prowlarr.test_all_indexers():
                    stats["prowlarr_testall"] = 1
                    tlog.detail("found", "Prowlarr", None, "Test global terminé")
                else:
                    stats["errors"] += 1
                    tlog.detail("alert", "Prowlarr", None, "Test global échoué")

            if await cfg.get_bool("indexer_global_check_sonarr"):
                sonarr = SonarrClient(
                    base_url=await cfg.get("sonarr_url"),
                    api_key=await cfg.get("sonarr_api_key"),
                )
                if sonarr.configured:
                    try:
                        tlog.detail("progress", "Sonarr", None, "Test global de tous les indexeurs…")
                        if await sonarr.test_all_indexers():
                            stats["sonarr_testall"] = 1
                            tlog.detail("found", "Sonarr", None, "Test global terminé")
                        else:
                            stats["errors"] += 1
                            tlog.detail("alert", "Sonarr", None, "Test global échoué")
                    except Exception as e:
                        stats["errors"] += 1
                        tlog.detail("error", "Sonarr", info=str(e))
                    finally:
                        await sonarr.close()

            if await cfg.get_bool("indexer_global_check_radarr"):
                radarr = RadarrClient(
                    base_url=await cfg.get("radarr_url"),
                    api_key=await cfg.get("radarr_api_key"),
                )
                if radarr.configured:
                    try:
                        tlog.detail("progress", "Radarr", None, "Test global de tous les indexeurs…")
                        if await radarr.test_all_indexers():
                            stats["radarr_testall"] = 1
                            tlog.detail("found", "Radarr", None, "Test global terminé")
                        else:
                            stats["errors"] += 1
                            tlog.detail("alert", "Radarr", None, "Test global échoué")
                    except Exception as e:
                        stats["errors"] += 1
                        tlog.detail("error", "Radarr", info=str(e))
                    finally:
                        await radarr.close()

        except Exception as e:
            logger.exception("Erreur check global indexeurs")
            tlog.detail("error", "Check global", info=str(e))
            stats["errors"] += 1
        finally:
            await prowlarr.close()

        tlog.set_stats(stats)
        await tlog.finish("success")
        return stats

    async def _collect_arr_down(
        self,
        client: SonarrClient | RadarrClient,
        service: str,
        excluded: set[str],
        down_targets: dict[str, dict],
        tlog: TaskLogger,
    ) -> None:
        health = await client.get_health()
        indexers = await client.get_indexers()
        enabled_by_name = {
            normalize_indexer_name(i.get("name", "")): i
            for i in indexers
            if i.get("enable", False) and i.get("name")
        }

        issues_by_name: dict[str, str] = {}
        for issue in health:
            source = issue.get("source", "")
            if source not in INDEXER_HEALTH_SOURCES:
                continue
            msg = issue.get("message", "")
            name = extract_indexer_name_from_health(msg)
            if not name:
                continue
            norm = normalize_indexer_name(name)
            if norm in excluded or norm not in enabled_by_name:
                continue
            issues_by_name[norm] = msg

        for norm, indexer in enabled_by_name.items():
            if norm in excluded:
                continue
            if norm not in issues_by_name:
                continue

            name = indexer.get("name", norm)
            if norm not in down_targets:
                down_targets[norm] = {
                    "name": name,
                    "services": {},
                    "arr_indexers": {},
                }
            down_targets[norm]["services"][service] = issues_by_name[norm]
            down_targets[norm]["arr_indexers"][service] = indexer
            if service == "sonarr":
                down_targets[norm]["sonarr_ok"] = False
            else:
                down_targets[norm]["radarr_ok"] = False

            tlog.detail("progress", name, indexer.get("id"), f"{service} · indexeur signalé KO")

    async def _prowlarr_blocked_map(self, prowlarr: ProwlarrClient) -> dict[int, bool]:
        blocked: dict[int, bool] = {}
        try:
            statuses = await prowlarr.get_indexer_status()
            now = datetime.now(timezone.utc)
            for st in statuses:
                idx_id = st.get("indexerId") or st.get("indexerID")
                if idx_id is None:
                    continue
                disabled_till = st.get("disabledTill") or st.get("disabledUntil")
                if disabled_till:
                    try:
                        if isinstance(disabled_till, str):
                            dt = datetime.fromisoformat(disabled_till.replace("Z", "+00:00"))
                        else:
                            dt = disabled_till
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt > now:
                            blocked[idx_id] = True
                            continue
                    except (ValueError, TypeError):
                        blocked[idx_id] = True
                        continue
                if (st.get("escalationLevel") or 0) > 0:
                    blocked[idx_id] = True
        except Exception as e:
            logger.warning("Impossible de lire indexerstatus Prowlarr: %s", e)
        return blocked

    async def _test_arr_indexer_by_id(
        self,
        client: SonarrClient | RadarrClient,
        indexer_id: int | None,
        *,
        service: str,
    ) -> bool:
        if indexer_id is None:
            logger.warning("Indexeur %s sans id — test ignoré", service)
            return False
        try:
            return await client.test_indexer_by_id(indexer_id)
        finally:
            await client.close()

    async def _get_excluded_names(self) -> set[str]:
        result = await self.db.execute(select(ExcludedIndexer))
        return {normalize_indexer_name(r.name) for r in result.scalars().all()}

    async def _get_state(self, name: str) -> IndexerHealthState | None:
        result = await self.db.execute(
            select(IndexerHealthState).where(IndexerHealthState.indexer_name == name)
        )
        return result.scalar_one_or_none()

    async def _save_state(
        self,
        name: str,
        prowlarr_id: int | None,
        status: str,
        sonarr_ok: bool | None,
        radarr_ok: bool | None,
        prowlarr_ok: bool | None,
        message: str,
    ) -> None:
        state = await self._get_state(name)
        now = datetime.now(timezone.utc)
        if state:
            state.status = status
            state.prowlarr_id = prowlarr_id
            state.sonarr_ok = sonarr_ok
            state.radarr_ok = radarr_ok
            state.prowlarr_ok = prowlarr_ok
            state.last_message = message[:500]
            state.last_checked_at = now
        else:
            self.db.add(IndexerHealthState(
                indexer_name=name,
                prowlarr_id=prowlarr_id,
                status=status,
                sonarr_ok=sonarr_ok,
                radarr_ok=radarr_ok,
                prowlarr_ok=prowlarr_ok,
                last_message=message[:500],
                last_checked_at=now,
            ))
        await self.db.commit()

    async def _maybe_alert(
        self,
        pushover: PushoverClient,
        name: str,
        alert_key: str,
        message: str,
        enabled: bool,
        stats: dict,
    ) -> None:
        if not enabled:
            return
        result = await self.db.execute(
            select(IndexerAlert).where(IndexerAlert.alert_key == alert_key)
        )
        if result.scalar_one_or_none():
            return
        sent = await pushover.send("MediaGuard - Indexeur KO", message, priority=0)
        if sent:
            self.db.add(IndexerAlert(indexer_name=name, alert_key=alert_key))
            await self.db.commit()
            stats["alerts"] += 1

    async def _notify_recovery(
        self,
        pushover: PushoverClient,
        name: str,
        message_parts: list[str],
        stats: dict,
    ) -> None:
        sent = await pushover.send(
            "MediaGuard - Indexeur récupéré",
            f"{name}\n" + " · ".join(message_parts),
            priority=0,
        )
        if sent:
            stats["alerts"] += 1

    async def _clear_alert(self, alert_key: str) -> None:
        await self.db.execute(delete(IndexerAlert).where(IndexerAlert.alert_key == alert_key))
        await self.db.commit()


async def fetch_indexer_overview(
    cfg: RuntimeConfig,
    db: AsyncSession,
) -> dict:
    """Aperçu live pour la page indexeurs."""
    settings = await cfg.all_settings()
    overview: dict = {
        "prowlarr_configured": False,
        "prowlarr_ok": False,
        "indexers": [],
        "excluded": [],
        "states": {},
        "errors": {},
        "issue_count": 0,
    }

    result = await db.execute(select(ExcludedIndexer).order_by(ExcludedIndexer.name))
    overview["excluded"] = list(result.scalars().all())

    states = await db.execute(select(IndexerHealthState))
    overview["states"] = {s.indexer_name: s for s in states.scalars().all()}

    prowlarr = ProwlarrClient(
        base_url=normalize_arr_url(settings.get("prowlarr_url")),
        api_key=(settings.get("prowlarr_api_key") or "").strip(),
    )
    if not prowlarr.configured:
        overview["errors"]["prowlarr"] = "Prowlarr non configuré — renseignez l'URL et la clé API"
        return overview

    overview["prowlarr_configured"] = True
    try:
        await prowlarr.get_system_status()
        overview["prowlarr_ok"] = True
        indexers = await prowlarr.get_indexers()
        statuses = await IndexerHealthService(db)._prowlarr_blocked_map(prowlarr)
        excluded = {normalize_indexer_name(e.name) for e in overview["excluded"]}

        sonarr_health: dict[str, str] = {}
        radarr_health: dict[str, str] = {}
        sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
        radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
        try:
            if sonarr.configured:
                for issue in await sonarr.get_health():
                    if issue.get("source") in INDEXER_HEALTH_SOURCES:
                        n = extract_indexer_name_from_health(issue.get("message", ""))
                        if n:
                            sonarr_health[normalize_indexer_name(n)] = issue.get("message", "")
        except Exception as e:
            overview["errors"]["sonarr"] = str(e)
        finally:
            await sonarr.close()
        try:
            if radarr.configured:
                for issue in await radarr.get_health():
                    if issue.get("source") in INDEXER_HEALTH_SOURCES:
                        n = extract_indexer_name_from_health(issue.get("message", ""))
                        if n:
                            radarr_health[normalize_indexer_name(n)] = issue.get("message", "")
        except Exception as e:
            overview["errors"]["radarr"] = str(e)
        finally:
            await radarr.close()

        rows = []
        for idx in sorted(indexers, key=lambda x: (x.get("name") or "").lower()):
            name = idx.get("name", "?")
            norm = normalize_indexer_name(name)
            state = overview["states"].get(name)
            rows.append({
                "id": idx.get("id"),
                "name": name,
                "enabled": idx.get("enable", False),
                "protocol": idx.get("protocol", "?"),
                "excluded": norm in excluded,
                "prowlarr_blocked": statuses.get(idx.get("id"), False),
                "sonarr_issue": sonarr_health.get(norm),
                "radarr_issue": radarr_health.get(norm),
                "last_status": state.status if state else "unknown",
                "last_checked": state.last_checked_at if state else None,
                "last_message": state.last_message if state else None,
            })
        overview["indexers"] = rows
        overview["issue_count"] = sum(
            1 for r in rows
            if r["enabled"] and not r["excluded"]
            and (r["prowlarr_blocked"] or r["sonarr_issue"] or r["radarr_issue"])
        )
    except Exception as e:
        overview["errors"]["prowlarr"] = friendly_connection_error(
            e, "Prowlarr", settings.get("prowlarr_url", ""),
        )
    finally:
        await prowlarr.close()

    return overview

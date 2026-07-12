"""Routes de l'interface web."""

import asyncio
import json
import logging

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.prowlarr import ProwlarrClient
from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.config import get_settings
from app.db.models import AnimeWatch, ExcludedIndexer, ExcludedMedia, IgnoredImport, MediaUpgradeRule, TaskLog
from app.db.session import async_session, get_db
from app.services.runtime_config import SETTING_GROUPS, TASK_META, RuntimeConfig, iter_setting_fields
from app.services.pending_imports import fetch_pending_imports
from app.services.task_logger import TaskLogger, get_log_details
from app.services.dashboard_data import fetch_recent_grabs
from app.services.indexer_health import fetch_indexer_overview
from app.services.wanted_search import WantedSearchService, list_wanted_preview
from app.utils.timezone import format_local

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
templates.env.filters["local_dt"] = format_local

LOG_SERVICES = ["sonarr", "radarr", "upgrade", "import", "anime", "search", "prowlarr"]
LOG_PAGE_SIZE = 25


async def _fetch_logs(db: AsyncSession, service: str | None = None, offset: int = 0, limit: int = LOG_PAGE_SIZE):
    query = select(TaskLog).order_by(TaskLog.created_at.desc())
    if service:
        query = query.where(TaskLog.service == service)
    query = query.offset(offset).limit(limit + 1)
    result = await db.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def _service_counts(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        select(TaskLog.service, func.count()).group_by(TaskLog.service)
    )
    return dict(result.all())


async def _pending_count(db: AsyncSession) -> int:
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()
    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
    try:
        items, _ = await fetch_pending_imports(sonarr, radarr)
        return len(items)
    except Exception:
        return 0
    finally:
        await sonarr.close()
        await radarr.close()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()

    sonarr_ok = radarr_ok = prowlarr_ok = False
    prowlarr_enabled = settings.get("prowlarr_enabled") == "true"
    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
    prowlarr = ProwlarrClient(
        base_url=settings.get("prowlarr_url"),
        api_key=settings.get("prowlarr_api_key"),
    )
    pushover = PushoverClient(
        user_key=settings.get("pushover_user_key"),
        api_token=settings.get("pushover_api_token"),
    )

    try:
        await sonarr.get_system_status()
        sonarr_ok = True
    except Exception:
        pass
    finally:
        await sonarr.close()

    try:
        await radarr.get_system_status()
        radarr_ok = True
    except Exception:
        pass
    finally:
        await radarr.close()

    if prowlarr_enabled and prowlarr.configured:
        try:
            await prowlarr.get_system_status()
            prowlarr_ok = True
        except Exception:
            pass
        finally:
            await prowlarr.close()
    else:
        await prowlarr.close()

    indexer_overview = await fetch_indexer_overview(cfg, db)
    recent_grabs = await fetch_recent_grabs(settings)

    logs, _ = await _fetch_logs(db, limit=10)
    service_stats = await _service_counts(db)
    pending_count = await _pending_count(db)

    anime_pending = await db.scalar(
        select(func.count()).select_from(AnimeWatch).where(AnimeWatch.resolved.is_(False))
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "page": "dashboard",
            "sonarr_ok": sonarr_ok,
            "radarr_ok": radarr_ok,
            "prowlarr_ok": prowlarr_ok,
            "prowlarr_enabled": prowlarr_enabled,
            "indexer_overview": indexer_overview,
            "recent_grabs": recent_grabs,
            "pushover_ok": pushover.configured,
            "dry_run": settings.get("dry_run") == "true",
            "logs": logs,
            "service_stats": service_stats,
            "tasks": TASK_META,
            "settings": settings,
            "anime_pending": anime_pending or 0,
            "pending_count": pending_count,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    service: str | None = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    if service and service not in LOG_SERVICES:
        service = None

    logs, has_more = await _fetch_logs(db, service=service, offset=offset)
    service_counts = await _service_counts(db)

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "page": "logs",
            "logs": logs,
            "active_service": service,
            "services": LOG_SERVICES,
            "service_counts": service_counts,
            "has_more": has_more,
            "next_offset": offset + LOG_PAGE_SIZE,
        },
    )


@router.get("/logs/{log_id}/details", response_class=HTMLResponse)
async def log_details_partial(log_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    log, details = await get_log_details(db, log_id)
    if not log:
        return HTMLResponse("<p class='text-muted text-sm p-4'>Log introuvable.</p>")

    return templates.TemplateResponse(
        "partials/log_details.html",
        {"request": request, "log": log, "details": details},
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "page": "settings", "groups": SETTING_GROUPS, "settings": settings},
    )


@router.post("/settings")
async def save_settings(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    updates: dict[str, str] = {}

    for field in iter_setting_fields():
        key = field["key"]
        if field["type"] == "toggle":
            updates[key] = "true" if form.get(key) == "true" else "false"
        elif field["type"] == "password":
            value = str(form.get(key, "")).strip()
            if value:
                updates[key] = value
        elif key in form:
            updates[key] = str(form[key]).strip()

    cfg = RuntimeConfig(db)
    await cfg.set_many(updates)
    tab = form.get("active_tab", "")
    suffix = f"&tab={tab}" if tab else ""
    return RedirectResponse(f"/settings?saved=1{suffix}", status_code=303)


@router.post("/tasks/{task_name}/run")
async def run_task(task_name: str):
    valid = {t["name"] for t in TASK_META}
    if task_name not in valid:
        return RedirectResponse("/?error=task", status_code=303)

    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job(task_name)
    await redis.aclose()
    return RedirectResponse("/?triggered=1", status_code=303)


@router.get("/ignored", response_class=HTMLResponse)
async def ignored_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(IgnoredImport).order_by(IgnoredImport.created_at.desc()))
    items = list(result.scalars().all())
    return templates.TemplateResponse(
        "ignored.html", {"request": request, "page": "ignored", "items": items}
    )


@router.post("/ignored")
async def add_ignored(
    service: str = Form(...),
    external_id: int = Form(...),
    title: str = Form(...),
    path: str = Form(""),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        IgnoredImport(
            service=service,
            external_id=external_id,
            title=title,
            path=path or None,
            reason=reason or None,
        )
    )
    await db.commit()
    return RedirectResponse("/ignored?added=1", status_code=303)


@router.post("/ignored/{item_id}/delete")
async def delete_ignored(item_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(IgnoredImport).where(IgnoredImport.id == item_id))
    await db.commit()
    return RedirectResponse("/ignored?deleted=1", status_code=303)


@router.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MediaUpgradeRule).order_by(MediaUpgradeRule.created_at.desc()))
    rules = list(result.scalars().all())
    return templates.TemplateResponse(
        "rules.html", {"request": request, "page": "rules", "rules": rules}
    )


@router.post("/rules")
async def add_rule(
    service: str = Form(...),
    external_id: int = Form(...),
    title: str = Form(...),
    required_codec: str = Form("any"),
    min_size_gb: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        MediaUpgradeRule(
            service=service,
            external_id=external_id,
            title=title,
            required_codec=required_codec,
            min_size_gb=min_size_gb,
        )
    )
    await db.commit()
    return RedirectResponse("/rules?added=1", status_code=303)


@router.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(MediaUpgradeRule).where(MediaUpgradeRule.id == rule_id))
    await db.commit()
    return RedirectResponse("/rules?deleted=1", status_code=303)


@router.get("/excluded", response_class=HTMLResponse)
async def excluded_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExcludedMedia).order_by(ExcludedMedia.created_at.desc()))
    items = list(result.scalars().all())
    return templates.TemplateResponse(
        "excluded.html", {"request": request, "page": "excluded", "items": items}
    )


@router.post("/excluded")
async def add_excluded(
    service: str = Form(...),
    external_id: int = Form(...),
    title: str = Form(...),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        ExcludedMedia(
            service=service,
            external_id=external_id,
            title=title,
            reason=reason or None,
        )
    )
    await db.commit()
    return RedirectResponse("/excluded?added=1", status_code=303)


@router.post("/excluded/{item_id}/delete")
async def delete_excluded(item_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ExcludedMedia).where(ExcludedMedia.id == item_id))
    await db.commit()
    return RedirectResponse("/excluded?deleted=1", status_code=303)


@router.get("/anime", response_class=HTMLResponse)
async def anime_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AnimeWatch).order_by(AnimeWatch.switched_at.desc()).limit(50))
    watches = list(result.scalars().all())
    return templates.TemplateResponse(
        "anime.html", {"request": request, "page": "anime", "watches": watches}
    )


@router.get("/pending-imports", response_class=HTMLResponse)
async def pending_imports_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()

    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))

    items, errors = await fetch_pending_imports(sonarr, radarr)
    await sonarr.close()
    await radarr.close()

    sonarr_count = sum(1 for i in items if i.service == "sonarr")
    radarr_count = sum(1 for i in items if i.service == "radarr")

    return templates.TemplateResponse(
        "pending_imports.html",
        {
            "request": request,
            "page": "pending",
            "items": items,
            "errors": errors,
            "sonarr_count": sonarr_count,
            "radarr_count": radarr_count,
            "pending_count": len(items),
        },
    )


@router.post("/pending-imports/ignore")
async def ignore_pending_import(
    service: str = Form(...),
    external_id: int = Form(...),
    title: str = Form(...),
    path: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    db.add(
        IgnoredImport(
            service=service,
            external_id=external_id,
            title=title,
            path=path or None,
            reason="cross-seed / import manuel",
        )
    )
    await db.commit()
    return RedirectResponse("/pending-imports?ignored=1", status_code=303)


@router.get("/wanted", response_class=HTMLResponse)
async def wanted_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()
    logs, _ = await _fetch_logs(db, service="search", limit=8)

    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))

    return templates.TemplateResponse(
        "wanted.html",
        {
            "request": request,
            "page": "wanted",
            "settings": settings,
            "search_logs": logs,
            "sonarr_configured": sonarr.configured,
            "radarr_configured": radarr.configured,
        },
    )


@router.get("/wanted/preview", response_class=HTMLResponse)
async def wanted_preview_partial(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()

    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))

    preview = await list_wanted_preview(
        sonarr if sonarr.configured else None,
        radarr if radarr.configured else None,
    )
    await sonarr.close()
    await radarr.close()

    return templates.TemplateResponse(
        "partials/wanted_preview.html",
        {
            "request": request,
            "preview": preview,
            "settings": settings,
            "sonarr_configured": sonarr.configured,
            "radarr_configured": radarr.configured,
            "active_tab": request.query_params.get("tab", "sonarr"),
        },
    )


@router.post("/wanted/search", response_class=HTMLResponse)
async def wanted_search_manual(
    request: Request,
    service: str = Form(...),
    series_id: int | None = Form(None),
    movie_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()

    if service == "sonarr":
        if not series_id:
            return _wanted_search_error(request, "empty", service)
        client = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
        if not client.configured:
            await client.close()
            return _wanted_search_error(request, "sonarr", service)
        media_title = await _sonarr_series_title(settings, series_id)
        await client.close()
    elif service == "radarr":
        if not movie_id:
            return _wanted_search_error(request, "empty", service)
        client = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
        if not client.configured:
            await client.close()
            return _wanted_search_error(request, "radarr", service)
        media_title = await _radarr_movie_title(settings, movie_id)
        await client.close()
    else:
        return _wanted_search_error(request, "service", service)

    tlog = TaskLogger(db, "wanted_search", service="search")
    log_id = await tlog.begin(f"Recherche {media_title}…")
    asyncio.create_task(_execute_wanted_search(log_id, service, series_id, movie_id))

    return templates.TemplateResponse(
        "partials/wanted_search_progress.html",
        {
            "request": request,
            "log_id": log_id,
            "service": service,
            "media_title": media_title,
            "running": True,
            "log": await db.get(TaskLog, log_id),
            "details": [],
            "grouped_details": [],
            "result": {},
            "settings": settings,
        },
    )


@router.get("/wanted/search/progress/{log_id}", response_class=HTMLResponse)
async def wanted_search_progress(
    log_id: int,
    request: Request,
    service: str = "sonarr",
    db: AsyncSession = Depends(get_db),
):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()
    log, details = await get_log_details(db, log_id)
    if not log:
        return HTMLResponse("<p class='empty-state'>Recherche introuvable.</p>", status_code=404)

    await db.refresh(log)

    result: dict = {}
    if log.stats_json:
        try:
            result = json.loads(log.stats_json)
        except json.JSONDecodeError:
            result = {}

    service = request.query_params.get("service", service)
    media_title = log.message.replace("Recherche ", "").rstrip("…") if log.message else "—"
    if details:
        for d in details:
            if d.action != "progress":
                media_title = d.media_title
                break

    grouped = _group_search_details([d for d in details if d.action != "progress"])
    running = log.status == "running"

    ctx = {
        "request": request,
        "log_id": log_id,
        "service": service,
        "media_title": media_title,
        "running": running,
        "log": log,
        "details": details,
        "grouped_details": grouped,
        "result": result,
        "settings": settings,
    }

    return templates.TemplateResponse("partials/wanted_search_progress.html", ctx)


async def _execute_wanted_search(
    log_id: int,
    service: str,
    series_id: int | None,
    movie_id: int | None,
) -> None:
    async with async_session() as db:
        tlog = TaskLogger(db, "wanted_search", service="search")
        await tlog.resume(log_id)
        finished = False
        try:
            tlog.detail("progress", "MediaGuard", None, "Démarrage de la recherche…")
            svc = WantedSearchService(db)
            if service == "sonarr":
                await svc.run(series_id=series_id, log_id=log_id)
            else:
                await svc.run(movie_id=movie_id, log_id=log_id)
            finished = True
        except Exception:
            logger.exception("Erreur recherche wanted manuelle")
            tlog.detail("error", "Recherche", info="Erreur inattendue")
            tlog.set_stats({"errors": 1})
            await tlog.finish("error", "Erreur lors de la recherche")
            finished = True
        finally:
            if not finished:
                log = await db.get(TaskLog, log_id)
                if log and log.status == "running":
                    tlog.set_stats({"errors": 1})
                    await tlog.finish("error", "Recherche interrompue")


def _wanted_search_error(request: Request, code: str, tab: str) -> RedirectResponse:
    return RedirectResponse(f"/wanted?error={code}&tab={tab}", status_code=303)


async def _sonarr_series_title(settings: dict, series_id: int) -> str:
    client = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    try:
        series = await client.get_series_by_id(series_id)
        return series.get("title", f"Série #{series_id}")
    except Exception:
        return f"Série #{series_id}"
    finally:
        await client.close()


async def _radarr_movie_title(settings: dict, movie_id: int) -> str:
    client = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
    try:
        movie = await client.get_movie(movie_id)
        return movie.get("title", f"Film #{movie_id}")
    except Exception:
        return f"Film #{movie_id}"
    finally:
        await client.close()


def _group_search_details(details: list) -> list[dict]:
    """Regroupe les détails de recherche par saison (Sonarr)."""
    groups: dict[str, list] = {}
    order: list[str] = []

    for item in details:
        label = "Résultats"
        detail = item.detail or ""
        if detail.startswith("S") and "E" in detail[:6]:
            label = detail.split("E")[0].split("·")[0].strip()
        elif "season pack" in detail.lower():
            label = detail.split("season pack")[0].strip() or "Season pack"
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(item)

    return [{"label": label, "items": groups[label]} for label in order]


@router.post("/wanted/run-sonarr")
async def wanted_run_sonarr():
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await redis.enqueue_job("wanted_search", only_service="sonarr")
    await redis.aclose()
    return RedirectResponse("/wanted?started=1&tab=sonarr", status_code=303)


@router.post("/wanted/run-radarr")
async def wanted_run_radarr():
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await redis.enqueue_job("wanted_search", only_service="radarr")
    await redis.aclose()
    return RedirectResponse("/wanted?started=1&tab=radarr", status_code=303)


@router.post("/wanted/run-all")
async def wanted_search_all():
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await redis.enqueue_job("wanted_search")
    await redis.aclose()
    return RedirectResponse("/wanted?started=1", status_code=303)


@router.get("/indexers", response_class=HTMLResponse)
async def indexers_page(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()
    overview = await fetch_indexer_overview(cfg, db)
    logs, _ = await _fetch_logs(db, service="prowlarr", limit=5)
    return templates.TemplateResponse(
        "indexers.html",
        {
            "request": request,
            "page": "indexers",
            "settings": settings,
            "overview": overview,
            "indexer_logs": logs,
        },
    )


@router.post("/indexers/run")
async def indexers_run_check():
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await redis.enqueue_job("indexer_health")
    await redis.aclose()
    return RedirectResponse("/indexers?started=1", status_code=303)


@router.post("/indexers/run-global")
async def indexers_run_global_check():
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await redis.enqueue_job("indexer_global_check")
    await redis.aclose()
    return RedirectResponse("/indexers?global_started=1", status_code=303)


@router.post("/indexers/exclude")
async def indexers_add_exclusion(
    name: str = Form(...),
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    name = name.strip()
    if not name:
        return RedirectResponse("/indexers?error=empty", status_code=303)
    result = await db.execute(select(ExcludedIndexer).where(ExcludedIndexer.name == name))
    if not result.scalar_one_or_none():
        db.add(ExcludedIndexer(name=name, reason=reason or None))
        await db.commit()
    return RedirectResponse("/indexers?added=1", status_code=303)


@router.post("/indexers/exclude/{item_id}/delete")
async def indexers_remove_exclusion(item_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ExcludedIndexer).where(ExcludedIndexer.id == item_id))
    await db.commit()
    return RedirectResponse("/indexers?removed=1", status_code=303)

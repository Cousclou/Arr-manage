"""Routes de l'interface web."""

import logging

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.config import get_settings
from app.db.models import AnimeWatch, ExcludedMedia, IgnoredImport, MediaUpgradeRule, TaskLog
from app.db.session import get_db
from app.services.runtime_config import SETTING_GROUPS, TASK_META, RuntimeConfig
from app.services.task_logger import get_log_details

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

LOG_SERVICES = ["sonarr", "radarr", "upgrade", "import", "anime"]
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


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = RuntimeConfig(db)
    settings = await cfg.all_settings()

    sonarr_ok = radarr_ok = False
    sonarr = SonarrClient(base_url=settings.get("sonarr_url"), api_key=settings.get("sonarr_api_key"))
    radarr = RadarrClient(base_url=settings.get("radarr_url"), api_key=settings.get("radarr_api_key"))
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

    logs, _ = await _fetch_logs(db, limit=10)
    service_stats = await _service_counts(db)

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
            "pushover_ok": pushover.configured,
            "dry_run": settings.get("dry_run") == "true",
            "logs": logs,
            "service_stats": service_stats,
            "tasks": TASK_META,
            "settings": settings,
            "anime_pending": anime_pending or 0,
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

    for group in SETTING_GROUPS:
        for field in group["fields"]:
            key = field["key"]
            if field["type"] == "toggle":
                updates[key] = "true" if key in form else "false"
            elif key in form:
                updates[key] = str(form[key])

    cfg = RuntimeConfig(db)
    await cfg.set_many(updates)
    return RedirectResponse("/settings?saved=1", status_code=303)


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

import logging

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    HealthResponse,
    IgnoredImportCreate,
    IgnoredImportResponse,
    LogDetailResponse,
    SettingsResponse,
    SettingsUpdate,
    TaskLogResponse,
    TriggerResponse,
    UpgradeRuleCreate,
    UpgradeRuleResponse,
)
from app.clients.pushover import PushoverClient
from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient
from app.config import get_settings
from app.db.models import IgnoredImport, MediaUpgradeRule, TaskLog
from app.db.session import get_db
from app.services.runtime_config import SETTING_DEFAULTS, RuntimeConfig
from app.services.task_logger import get_log_details

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
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

    return HealthResponse(
        status="ok" if sonarr_ok or radarr_ok else "degraded",
        sonarr=sonarr_ok,
        radarr=radarr_ok,
        pushover=pushover.configured,
    )


@router.post("/tasks/{task_name}/trigger", response_model=TriggerResponse)
async def trigger_task(task_name: str) -> TriggerResponse:
    valid_tasks = {
        "sonarr_monitor",
        "radarr_monitor",
        "upgrade_check",
        "import_monitor",
        "anime_handler",
    }
    if task_name not in valid_tasks:
        raise HTTPException(404, f"Tâche inconnue: {task_name}")

    settings = get_settings()
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    job = await redis.enqueue_job(task_name)
    await redis.aclose()

    return TriggerResponse(
        task=task_name,
        job_id=job.job_id if job else None,
        message=f"Tâche {task_name} mise en file",
    )


@router.get("/logs", response_model=list[TaskLogResponse])
async def get_logs(
    limit: int = 50,
    service: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[TaskLog]:
    query = select(TaskLog).order_by(TaskLog.created_at.desc()).limit(limit)
    if service:
        query = query.where(TaskLog.service == service)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/logs/{log_id}/details", response_model=list[LogDetailResponse])
async def get_log_details_api(log_id: int, db: AsyncSession = Depends(get_db)) -> list:
    log, details = await get_log_details(db, log_id)
    if not log:
        raise HTTPException(404, "Log introuvable")
    return details


@router.get("/ignored-imports", response_model=list[IgnoredImportResponse])
async def list_ignored_imports(db: AsyncSession = Depends(get_db)) -> list[IgnoredImport]:
    result = await db.execute(select(IgnoredImport).order_by(IgnoredImport.created_at.desc()))
    return list(result.scalars().all())


@router.post("/ignored-imports", response_model=IgnoredImportResponse, status_code=201)
async def add_ignored_import(
    body: IgnoredImportCreate,
    db: AsyncSession = Depends(get_db),
) -> IgnoredImport:
    entry = IgnoredImport(
        service=body.service,
        external_id=body.external_id,
        title=body.title,
        path=body.path,
        reason=body.reason,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/ignored-imports/{import_id}", status_code=204)
async def remove_ignored_import(import_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await db.execute(delete(IgnoredImport).where(IgnoredImport.id == import_id))
    await db.commit()


@router.get("/upgrade-rules", response_model=list[UpgradeRuleResponse])
async def list_upgrade_rules(db: AsyncSession = Depends(get_db)) -> list[MediaUpgradeRule]:
    result = await db.execute(select(MediaUpgradeRule).order_by(MediaUpgradeRule.created_at.desc()))
    return list(result.scalars().all())


@router.post("/upgrade-rules", response_model=UpgradeRuleResponse, status_code=201)
async def add_upgrade_rule(
    body: UpgradeRuleCreate,
    db: AsyncSession = Depends(get_db),
) -> MediaUpgradeRule:
    rule = MediaUpgradeRule(
        service=body.service,
        external_id=body.external_id,
        title=body.title,
        required_codec=body.required_codec,
        min_size_gb=body.min_size_gb,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/upgrade-rules/{rule_id}", status_code=204)
async def remove_upgrade_rule(rule_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await db.execute(delete(MediaUpgradeRule).where(MediaUpgradeRule.id == rule_id))
    await db.commit()


@router.get("/settings", response_model=SettingsResponse)
async def get_settings_api(db: AsyncSession = Depends(get_db)) -> SettingsResponse:
    cfg = RuntimeConfig(db)
    return SettingsResponse(settings=await cfg.all_settings())


@router.put("/settings", response_model=SettingsResponse)
async def update_settings_api(body: SettingsUpdate, db: AsyncSession = Depends(get_db)) -> SettingsResponse:
    valid = {k: v for k, v in body.settings.items() if k in SETTING_DEFAULTS}
    cfg = RuntimeConfig(db)
    await cfg.set_many(valid)
    return SettingsResponse(settings=await cfg.all_settings())

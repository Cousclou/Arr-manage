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

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    sonarr_ok = False
    radarr_ok = False

    sonarr = SonarrClient()
    radarr = RadarrClient()
    pushover = PushoverClient()

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
async def get_logs(limit: int = 50, db: AsyncSession = Depends(get_db)) -> list[TaskLog]:
    result = await db.execute(
        select(TaskLog).order_by(TaskLog.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


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

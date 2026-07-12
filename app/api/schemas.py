from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    sonarr: bool
    radarr: bool
    prowlarr: bool = False
    pushover: bool


class IgnoredImportCreate(BaseModel):
    service: str = Field(..., pattern="^(sonarr|radarr)$")
    external_id: int
    title: str
    path: str | None = None
    reason: str | None = None


class IgnoredImportResponse(BaseModel):
    id: int
    service: str
    external_id: int
    title: str
    path: str | None
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UpgradeRuleCreate(BaseModel):
    service: str = Field(..., pattern="^(sonarr|radarr)$")
    external_id: int
    title: str
    required_codec: str = Field("any", pattern="^(av1|h265|any)$")
    min_size_gb: float | None = None


class UpgradeRuleResponse(BaseModel):
    id: int
    service: str
    external_id: int
    title: str
    required_codec: str
    min_size_gb: float | None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskLogResponse(BaseModel):
    id: int
    task_name: str
    service: str
    status: str
    message: str | None
    details_count: int
    details_truncated: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LogDetailResponse(BaseModel):
    id: int
    action: str
    media_title: str
    external_id: int | None
    detail: str | None

    model_config = {"from_attributes": True}


class TriggerResponse(BaseModel):
    task: str
    job_id: str | None
    message: str


class SettingsResponse(BaseModel):
    settings: dict[str, str]


class SettingsUpdate(BaseModel):
    settings: dict[str, str]

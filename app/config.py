from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mediaguard:changeme@localhost:5432/mediaguard"
    redis_url: str = "redis://localhost:6379/0"

    sonarr_url: str = "http://localhost:8989"
    sonarr_api_key: str = ""

    radarr_url: str = "http://localhost:7878"
    radarr_api_key: str = ""

    prowlarr_url: str = "http://localhost:9696"
    prowlarr_api_key: str = ""

    pushover_user_key: str = ""
    pushover_api_token: str = ""

    sonarr_monitor_interval: int = 3600
    radarr_monitor_interval: int = 3600
    upgrade_check_interval: int = 7200
    import_check_interval: int = 300
    anime_check_interval: int = 600

    upgrade_size_threshold_gb: float = 15.0
    radarr_exclude_tag_ids: str = ""

    @property
    def radarr_exclude_tags(self) -> list[int]:
        if not self.radarr_exclude_tag_ids.strip():
            return []
        return [int(t.strip()) for t in self.radarr_exclude_tag_ids.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Configuration runtime : fusion .env + paramètres en base."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import AppSetting

# Clés modifiables via l'interface web (valeurs stockées en texte)
SETTING_DEFAULTS: dict[str, str] = {
    # Connexions
    "sonarr_url": "",
    "sonarr_api_key": "",
    "radarr_url": "",
    "radarr_api_key": "",
    "pushover_user_key": "",
    "pushover_api_token": "",
    # Intervalles (secondes)
    "sonarr_monitor_interval": "3600",
    "radarr_monitor_interval": "3600",
    "upgrade_check_interval": "7200",
    "import_check_interval": "300",
    "anime_check_interval": "600",
    # Activation des tâches
    "task_sonarr_monitor_enabled": "true",
    "task_radarr_monitor_enabled": "true",
    "task_upgrade_check_enabled": "true",
    "task_import_monitor_enabled": "true",
    "task_anime_handler_enabled": "true",
    # Sonarr
    "sonarr_unmonitor_downloaded": "true",
    "sonarr_set_new_episodes_only": "true",
    "sonarr_unmonitor_complete_seasons": "true",
    "sonarr_exclude_tag_ids": "",
    "sonarr_skip_continuing": "false",
    "sonarr_skip_anime": "false",
    # Radarr
    "radarr_unmonitor_downloaded": "true",
    "radarr_exclude_tag_ids": "",
    "radarr_keep_monitored_if_upgrade": "false",
    # Upgrades
    "upgrade_size_threshold_gb": "15",
    "upgrade_min_savings_percent": "15",
    "upgrade_preferred_codec": "av1",
    "upgrade_check_sonarr": "true",
    "upgrade_check_radarr": "true",
    "upgrade_auto_search": "false",
    "upgrade_notify_pushover": "true",
    # Anime
    "anime_enabled": "true",
    "anime_wait_hours": "1",
    # Import
    "import_notify_enabled": "true",
    "import_check_queue": "true",
    "import_check_history": "true",
    # Général
    "dry_run": "false",
}

ENV_OVERRIDES: dict[str, str] = {
    "sonarr_url": "sonarr_url",
    "sonarr_api_key": "sonarr_api_key",
    "radarr_url": "radarr_url",
    "radarr_api_key": "radarr_api_key",
    "pushover_user_key": "pushover_user_key",
    "pushover_api_token": "pushover_api_token",
    "sonarr_monitor_interval": "sonarr_monitor_interval",
    "radarr_monitor_interval": "radarr_monitor_interval",
    "upgrade_check_interval": "upgrade_check_interval",
    "import_check_interval": "import_check_interval",
    "anime_check_interval": "anime_check_interval",
    "upgrade_size_threshold_gb": "upgrade_size_threshold_gb",
    "radarr_exclude_tag_ids": "radarr_exclude_tag_ids",
}


def _env_value(env: Settings, attr: str) -> str:
    val = getattr(env, attr)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        return str(val)
    return str(val) if val is not None else ""


class RuntimeConfig:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._data: dict[str, str] | None = None

    async def load(self) -> dict[str, str]:
        if self._data is not None:
            return self._data

        env = get_settings()
        merged = dict(SETTING_DEFAULTS)

        for key, attr in ENV_OVERRIDES.items():
            env_val = _env_value(env, attr)
            if env_val:
                merged[key] = env_val

        result = await self.db.execute(select(AppSetting))
        for row in result.scalars().all():
            merged[row.key] = row.value

        self._data = merged
        return merged

    async def get(self, key: str, default: str | None = None) -> str:
        data = await self.load()
        return data.get(key, default if default is not None else SETTING_DEFAULTS.get(key, ""))

    async def get_bool(self, key: str) -> bool:
        return (await self.get(key)).lower() in ("true", "1", "yes", "on")

    async def get_int(self, key: str) -> int:
        try:
            return int(await self.get(key))
        except ValueError:
            return int(SETTING_DEFAULTS.get(key, "0"))

    async def get_float(self, key: str) -> float:
        try:
            return float(await self.get(key))
        except ValueError:
            return float(SETTING_DEFAULTS.get(key, "0"))

    async def get_int_list(self, key: str) -> list[int]:
        raw = await self.get(key)
        if not raw.strip():
            return []
        return [int(x.strip()) for x in raw.split(",") if x.strip()]

    async def set_many(self, updates: dict[str, str]) -> None:
        for key, value in updates.items():
            if key not in SETTING_DEFAULTS:
                continue
            result = await self.db.execute(select(AppSetting).where(AppSetting.key == key))
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = value
            else:
                self.db.add(AppSetting(key=key, value=value))
        await self.db.commit()
        self._data = None

    async def all_settings(self) -> dict[str, str]:
        return await self.load()


async def seed_default_settings(db: AsyncSession) -> None:
    result = await db.execute(select(AppSetting.key))
    existing = set(result.scalars().all())
    for key, value in SETTING_DEFAULTS.items():
        if key not in existing:
            db.add(AppSetting(key=key, value=value))
    await db.commit()


SETTING_GROUPS: list[dict] = [
    {
        "id": "connections",
        "title": "Connexions",
        "icon": "🔗",
        "fields": [
            {"key": "sonarr_url", "label": "URL Sonarr", "type": "text", "placeholder": "http://sonarr:8989"},
            {"key": "sonarr_api_key", "label": "Clé API Sonarr", "type": "password"},
            {"key": "radarr_url", "label": "URL Radarr", "type": "text", "placeholder": "http://radarr:7878"},
            {"key": "radarr_api_key", "label": "Clé API Radarr", "type": "password"},
            {"key": "pushover_user_key", "label": "Pushover User Key", "type": "password"},
            {"key": "pushover_api_token", "label": "Pushover API Token", "type": "password"},
        ],
    },
    {
        "id": "tasks",
        "title": "Tâches planifiées",
        "icon": "⏱️",
        "fields": [
            {"key": "task_sonarr_monitor_enabled", "label": "Activer monitoring Sonarr", "type": "toggle"},
            {"key": "sonarr_monitor_interval", "label": "Intervalle Sonarr (sec)", "type": "number"},
            {"key": "task_radarr_monitor_enabled", "label": "Activer monitoring Radarr", "type": "toggle"},
            {"key": "radarr_monitor_interval", "label": "Intervalle Radarr (sec)", "type": "number"},
            {"key": "task_upgrade_check_enabled", "label": "Activer vérification upgrades", "type": "toggle"},
            {"key": "upgrade_check_interval", "label": "Intervalle upgrades (sec)", "type": "number"},
            {"key": "task_import_monitor_enabled", "label": "Activer surveillance imports", "type": "toggle"},
            {"key": "import_check_interval", "label": "Intervalle imports (sec)", "type": "number"},
            {"key": "task_anime_handler_enabled", "label": "Activer handler anime", "type": "toggle"},
            {"key": "anime_check_interval", "label": "Intervalle anime (sec)", "type": "number"},
        ],
    },
    {
        "id": "sonarr",
        "title": "Sonarr",
        "icon": "📺",
        "fields": [
            {"key": "sonarr_unmonitor_downloaded", "label": "Désactiver suivi épisodes téléchargés", "type": "toggle"},
            {"key": "sonarr_set_new_episodes_only", "label": "Mode nouveaux épisodes uniquement", "type": "toggle"},
            {"key": "sonarr_unmonitor_complete_seasons", "label": "Désactiver saisons complètes d'un coup", "type": "toggle"},
            {"key": "sonarr_skip_continuing", "label": "Ignorer séries en cours de diffusion", "type": "toggle"},
            {"key": "sonarr_skip_anime", "label": "Ignorer séries anime (handler dédié)", "type": "toggle"},
            {"key": "sonarr_exclude_tag_ids", "label": "Tags exclus (IDs, virgules)", "type": "text", "placeholder": "1,2,3"},
        ],
    },
    {
        "id": "radarr",
        "title": "Radarr",
        "icon": "🎬",
        "fields": [
            {"key": "radarr_unmonitor_downloaded", "label": "Désactiver suivi films téléchargés", "type": "toggle"},
            {"key": "radarr_keep_monitored_if_upgrade", "label": "Garder suivi si upgrade disponible", "type": "toggle"},
            {"key": "radarr_exclude_tag_ids", "label": "Tags exclus (IDs, virgules)", "type": "text", "placeholder": "4,5"},
        ],
    },
    {
        "id": "upgrades",
        "title": "Upgrades",
        "icon": "📦",
        "fields": [
            {"key": "upgrade_check_sonarr", "label": "Vérifier upgrades Sonarr", "type": "toggle"},
            {"key": "upgrade_check_radarr", "label": "Vérifier upgrades Radarr", "type": "toggle"},
            {"key": "upgrade_size_threshold_gb", "label": "Seuil taille (Go)", "type": "number", "step": "0.1"},
            {"key": "upgrade_min_savings_percent", "label": "Économie minimale (%)", "type": "number"},
            {"key": "upgrade_preferred_codec", "label": "Codec préféré", "type": "select", "options": ["av1", "h265", "any"]},
            {"key": "upgrade_auto_search", "label": "Lancer recherche auto si upgrade trouvé", "type": "toggle"},
            {"key": "upgrade_notify_pushover", "label": "Notifier Pushover si upgrade trouvé", "type": "toggle"},
        ],
    },
    {
        "id": "anime",
        "title": "Anime",
        "icon": "🎌",
        "fields": [
            {"key": "anime_enabled", "label": "Activer gestion anime", "type": "toggle"},
            {"key": "anime_wait_hours", "label": "Délai avant décision (heures)", "type": "number"},
        ],
    },
    {
        "id": "imports",
        "title": "Imports",
        "icon": "📥",
        "fields": [
            {"key": "import_notify_enabled", "label": "Notifications Pushover activées", "type": "toggle"},
            {"key": "import_check_queue", "label": "Surveiller la file d'attente", "type": "toggle"},
            {"key": "import_check_history", "label": "Surveiller l'historique", "type": "toggle"},
        ],
    },
    {
        "id": "advanced",
        "title": "Avancé",
        "icon": "⚙️",
        "fields": [
            {"key": "dry_run", "label": "Mode simulation (aucune modification)", "type": "toggle"},
        ],
    },
]

TASK_META = [
    {"name": "sonarr_monitor", "label": "Monitoring Sonarr", "enabled_key": "task_sonarr_monitor_enabled"},
    {"name": "radarr_monitor", "label": "Monitoring Radarr", "enabled_key": "task_radarr_monitor_enabled"},
    {"name": "upgrade_check", "label": "Vérification upgrades", "enabled_key": "task_upgrade_check_enabled"},
    {"name": "import_monitor", "label": "Surveillance imports", "enabled_key": "task_import_monitor_enabled"},
    {"name": "anime_handler", "label": "Handler anime", "enabled_key": "task_anime_handler_enabled"},
]

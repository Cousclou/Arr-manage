"""Configuration runtime : fusion .env + paramètres en base."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.utils.timezone import normalize_timezone_name, resolve_timezone
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
    "app_timezone": "Europe/Paris",
    # Recherche wanted
    "task_wanted_search_enabled": "false",
    "wanted_search_interval": "1800",
    "search_sonarr_enabled": "true",
    "search_radarr_enabled": "false",
    "search_min_seeders": "2",
    "search_notify_low_seeders": "true",
    "search_prefer_season_pack": "true",
    "search_auto_grab": "false",
    "search_old_series_season_first": "true",
    # Prowlarr / indexeurs
    "prowlarr_url": "",
    "prowlarr_api_key": "",
    "prowlarr_enabled": "true",
    "task_indexer_health_enabled": "true",
    "indexer_health_interval": "900",
    "indexer_health_check_sonarr": "true",
    "indexer_health_check_radarr": "true",
    "indexer_notify_on_failure": "true",
    "indexer_notify_on_recovery": "true",
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
    "prowlarr_url": "prowlarr_url",
    "prowlarr_api_key": "prowlarr_api_key",
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

    async def get_timezone(self):
        return resolve_timezone(await self.get("app_timezone"))

    async def set_many(self, updates: dict[str, str]) -> None:
        for key, value in updates.items():
            if key not in SETTING_DEFAULTS:
                continue
            if key == "app_timezone":
                value = normalize_timezone_name(value)
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
        "id": "sonarr",
        "title": "Sonarr",
        "icon": "tv",
        "sections": [
            {
                "title": "Connexion",
                "description": "URL et clé API de votre instance Sonarr.",
                "fields": [
                    {"key": "sonarr_url", "label": "URL Sonarr", "type": "text", "placeholder": "http://sonarr:8989"},
                    {"key": "sonarr_api_key", "label": "Clé API", "type": "password"},
                ],
            },
            {
                "title": "Planification",
                "fields": [
                    {"key": "task_sonarr_monitor_enabled", "label": "Activer le monitoring", "type": "toggle"},
                    {"key": "sonarr_monitor_interval", "label": "Intervalle (secondes)", "type": "number"},
                ],
            },
            {
                "title": "Comportement",
                "fields": [
                    {"key": "sonarr_unmonitor_downloaded", "label": "Désactiver suivi épisodes téléchargés", "type": "toggle"},
                    {"key": "sonarr_set_new_episodes_only", "label": "Mode nouveaux épisodes uniquement", "type": "toggle"},
                    {"key": "sonarr_unmonitor_complete_seasons", "label": "Désactiver saisons complètes", "type": "toggle"},
                    {"key": "sonarr_skip_continuing", "label": "Ignorer séries en cours de diffusion", "type": "toggle"},
                    {"key": "sonarr_skip_anime", "label": "Ignorer séries anime (handler dédié)", "type": "toggle"},
                    {"key": "sonarr_exclude_tag_ids", "label": "Tags exclus (IDs, virgules)", "type": "text", "placeholder": "1,2,3"},
                ],
            },
        ],
    },
    {
        "id": "radarr",
        "title": "Radarr",
        "icon": "film",
        "sections": [
            {
                "title": "Connexion",
                "description": "URL et clé API de votre instance Radarr.",
                "fields": [
                    {"key": "radarr_url", "label": "URL Radarr", "type": "text", "placeholder": "http://radarr:7878"},
                    {"key": "radarr_api_key", "label": "Clé API", "type": "password"},
                ],
            },
            {
                "title": "Planification",
                "fields": [
                    {"key": "task_radarr_monitor_enabled", "label": "Activer le monitoring", "type": "toggle"},
                    {"key": "radarr_monitor_interval", "label": "Intervalle (secondes)", "type": "number"},
                ],
            },
            {
                "title": "Comportement",
                "fields": [
                    {"key": "radarr_unmonitor_downloaded", "label": "Désactiver suivi films téléchargés", "type": "toggle"},
                    {"key": "radarr_keep_monitored_if_upgrade", "label": "Garder suivi si upgrade disponible", "type": "toggle"},
                    {"key": "radarr_exclude_tag_ids", "label": "Tags exclus (IDs, virgules)", "type": "text", "placeholder": "4,5"},
                ],
            },
        ],
    },
    {
        "id": "notifications",
        "title": "Notifications",
        "icon": "bell",
        "sections": [
            {
                "title": "Pushover",
                "description": "Configuration des alertes Pushover.",
                "fields": [
                    {"key": "pushover_user_key", "label": "User Key", "type": "password"},
                    {"key": "pushover_api_token", "label": "API Token", "type": "password"},
                ],
            },
            {
                "title": "Alertes imports",
                "fields": [
                    {"key": "import_notify_enabled", "label": "Notifier les échecs d'import", "type": "toggle"},
                ],
            },
            {
                "title": "Alertes upgrades",
                "fields": [
                    {"key": "upgrade_notify_pushover", "label": "Notifier si upgrade disponible", "type": "toggle"},
                ],
            },
        ],
    },
    {
        "id": "upgrades",
        "title": "Upgrades",
        "icon": "package",
        "sections": [
            {
                "title": "Planification",
                "fields": [
                    {"key": "task_upgrade_check_enabled", "label": "Activer la vérification", "type": "toggle"},
                    {"key": "upgrade_check_interval", "label": "Intervalle (secondes)", "type": "number"},
                ],
            },
            {
                "title": "Critères",
                "fields": [
                    {"key": "upgrade_check_sonarr", "label": "Vérifier Sonarr", "type": "toggle"},
                    {"key": "upgrade_check_radarr", "label": "Vérifier Radarr", "type": "toggle"},
                    {"key": "upgrade_size_threshold_gb", "label": "Seuil taille (Go)", "type": "number", "step": "0.1"},
                    {"key": "upgrade_min_savings_percent", "label": "Économie minimale (%)", "type": "number"},
                    {"key": "upgrade_preferred_codec", "label": "Codec préféré", "type": "select", "options": ["av1", "h265", "any"]},
                    {"key": "upgrade_auto_search", "label": "Recherche auto si upgrade trouvé", "type": "toggle"},
                ],
            },
        ],
    },
    {
        "id": "imports",
        "title": "Imports",
        "icon": "download",
        "sections": [
            {
                "title": "Surveillance",
                "description": "Détection des imports nécessitant une action manuelle.",
                "fields": [
                    {"key": "task_import_monitor_enabled", "label": "Activer la surveillance", "type": "toggle"},
                    {"key": "import_check_interval", "label": "Intervalle (secondes)", "type": "number"},
                    {"key": "import_check_queue", "label": "Surveiller la file d'attente", "type": "toggle"},
                    {"key": "import_check_history", "label": "Surveiller l'historique", "type": "toggle"},
                ],
            },
        ],
    },
    {
        "id": "anime",
        "title": "Anime",
        "icon": "star",
        "sections": [
            {
                "title": "Planification",
                "fields": [
                    {"key": "task_anime_handler_enabled", "label": "Activer le handler anime", "type": "toggle"},
                    {"key": "anime_check_interval", "label": "Intervalle (secondes)", "type": "number"},
                ],
            },
            {
                "title": "Comportement",
                "fields": [
                    {"key": "anime_enabled", "label": "Activer gestion anime", "type": "toggle"},
                    {"key": "anime_wait_hours", "label": "Délai avant décision (heures)", "type": "number"},
                ],
            },
        ],
    },
    {
        "id": "search",
        "title": "Recherche",
        "icon": "search",
        "sections": [
            {
                "title": "Planification",
                "description": "Recherche automatique des épisodes et films wanted.",
                "fields": [
                    {"key": "task_wanted_search_enabled", "label": "Activer la recherche wanted", "type": "toggle"},
                    {"key": "wanted_search_interval", "label": "Intervalle (secondes)", "type": "number"},
                    {"key": "search_sonarr_enabled", "label": "Rechercher sur Sonarr", "type": "toggle"},
                    {"key": "search_radarr_enabled", "label": "Rechercher sur Radarr", "type": "toggle"},
                ],
            },
            {
                "title": "Stratégie Sonarr",
                "description": "Par défaut, les séries dont l'année est antérieure à l'année en cours : season pack d'abord, puis épisode par épisode si besoin.",
                "fields": [
                    {"key": "search_old_series_season_first", "label": "Séries anciennes : season pack puis épisodes", "type": "toggle"},
                    {"key": "search_prefer_season_pack", "label": "Préférer les season packs (séries récentes)", "type": "toggle"},
                ],
            },
            {
                "title": "Stratégie Radarr",
                "description": "Recherche directe sur chaque film wanted. Les films sans release sont ignorés ; une notification est envoyée si une release est trouvée mais avec trop peu de seeders (voir critères ci-dessous).",
                "fields": [],
            },
            {
                "title": "Critères & actions",
                "fields": [
                    {"key": "search_min_seeders", "label": "Seeders minimum", "type": "number"},
                    {"key": "search_notify_low_seeders", "label": "Notifier si seeders insuffisants", "type": "toggle"},
                    {"key": "search_auto_grab", "label": "Télécharger automatiquement si critères OK", "type": "toggle"},
                ],
            },
        ],
    },
    {
        "id": "prowlarr",
        "title": "Prowlarr",
        "icon": "plug",
        "sections": [
            {
                "title": "Connexion",
                "description": "Prowlarr est utilisé pour vérifier et réactiver les indexeurs signalés KO par Sonarr/Radarr.",
                "fields": [
                    {"key": "prowlarr_enabled", "label": "Activer Prowlarr", "type": "toggle"},
                    {"key": "prowlarr_url", "label": "URL Prowlarr", "type": "text", "placeholder": "http://prowlarr:9696"},
                    {"key": "prowlarr_api_key", "label": "Clé API", "type": "password"},
                ],
            },
            {
                "title": "Santé indexeurs",
                "description": "Si un indexeur est KO côté Sonarr/Radarr, MediaGuard vérifie Prowlarr, relance un test, puis reteste sur Sonarr/Radarr.",
                "fields": [
                    {"key": "task_indexer_health_enabled", "label": "Activer le monitoring", "type": "toggle"},
                    {"key": "indexer_health_interval", "label": "Intervalle (secondes)", "type": "number"},
                    {"key": "indexer_health_check_sonarr", "label": "Surveiller Sonarr", "type": "toggle"},
                    {"key": "indexer_health_check_radarr", "label": "Surveiller Radarr", "type": "toggle"},
                    {"key": "indexer_notify_on_failure", "label": "Notifier si indexeur KO", "type": "toggle"},
                    {"key": "indexer_notify_on_recovery", "label": "Notifier si récupération", "type": "toggle"},
                ],
            },
        ],
    },
    {
        "id": "advanced",
        "title": "Avancé",
        "icon": "sliders",
        "sections": [
            {
                "title": "Général",
                "fields": [
                    {"key": "dry_run", "label": "Mode simulation (aucune modification)", "type": "toggle"},
                    {
                        "key": "app_timezone",
                        "label": "Fuseau horaire",
                        "type": "text",
                        "placeholder": "Europe/Paris",
                    },
                ],
            },
        ],
    },
]


def iter_setting_fields() -> list[dict]:
    """Parcourt tous les champs de configuration."""
    fields: list[dict] = []
    for group in SETTING_GROUPS:
        for section in group.get("sections", []):
            fields.extend(section.get("fields", []))
        fields.extend(group.get("fields", []))
    return fields

TASK_META = [
    {"name": "sonarr_monitor", "label": "Monitoring Sonarr", "enabled_key": "task_sonarr_monitor_enabled"},
    {"name": "radarr_monitor", "label": "Monitoring Radarr", "enabled_key": "task_radarr_monitor_enabled"},
    {"name": "upgrade_check", "label": "Vérification upgrades", "enabled_key": "task_upgrade_check_enabled"},
    {"name": "import_monitor", "label": "Surveillance imports", "enabled_key": "task_import_monitor_enabled"},
    {"name": "anime_handler", "label": "Handler anime", "enabled_key": "task_anime_handler_enabled"},
    {"name": "wanted_search", "label": "Recherche wanted", "enabled_key": "task_wanted_search_enabled"},
    {"name": "indexer_health", "label": "Santé indexeurs", "enabled_key": "task_indexer_health_enabled"},
]

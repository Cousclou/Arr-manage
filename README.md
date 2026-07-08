# MediaGuard

Application de gestion d'état pour **Sonarr** et **Radarr** visant à réduire la charge des scans et optimiser la bibliothèque média.

## Fonctionnalités

- **Sonarr** : désactive le suivi des épisodes déjà téléchargés et passe les séries en mode « nouveaux épisodes uniquement »
- **Radarr** : désactive le suivi des films téléchargés, avec exclusion par tags et règles de format
- **Upgrades** : détecte les versions plus légères (priorité AV1 puis H.265) au-delà d'un seuil de taille
- **Anime** : bascule automatique en « standard » pour les animes d'années précédentes, retour en « anime » si fichier trouvé après 1 h
- **Notifications Pushover** : alertes sur les imports échoués, avec liste d'ignorés pour le cross-seed

## Stack

- FastAPI + PostgreSQL + Redis (ARQ)
- Docker Compose

## Démarrage rapide

```bash
cp .env.example .env
# Éditer .env avec vos clés API Sonarr, Radarr et Pushover

docker compose up -d --build
```

L'API est disponible sur `http://localhost:8000` — documentation Swagger sur `/docs`.

## Configuration

| Variable | Description |
|----------|-------------|
| `SONARR_URL` / `SONARR_API_KEY` | Connexion Sonarr |
| `RADARR_URL` / `RADARR_API_KEY` | Connexion Radarr |
| `PUSHOVER_USER_KEY` / `PUSHOVER_API_TOKEN` | Notifications |
| `UPGRADE_SIZE_THRESHOLD_GB` | Seuil (Go) pour chercher des versions plus légères |
| `RADARR_EXCLUDE_TAG_IDS` | IDs de tags Radarr à exclure (séparés par virgules) |
| `*_INTERVAL` | Intervalles des tâches en secondes |

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/health` | État des connexions |
| `POST /api/v1/tasks/{name}/trigger` | Déclencher une tâche manuellement |
| `GET/POST/DELETE /api/v1/ignored-imports` | Gérer les imports ignorés |
| `GET/POST/DELETE /api/v1/upgrade-rules` | Règles de format par média |
| `GET /api/v1/logs` | Historique des tâches |

Tâches disponibles : `sonarr_monitor`, `radarr_monitor`, `upgrade_check`, `import_monitor`, `anime_handler`.

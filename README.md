# MediaGuard

Application de gestion d'état pour **Sonarr** et **Radarr** visant à réduire la charge des scans et optimiser la bibliothèque média.

## Fonctionnalités

- **Interface web** sur `http://localhost:8000` pour configurer toute l'application
- **Sonarr** : désactive le suivi des épisodes/saisons téléchargés, mode nouveaux épisodes uniquement
- **Radarr** : désactive le suivi des films téléchargés, exclusion par tags et règles de format
- **Upgrades** : détecte les versions plus légères (AV1 / H.265), recherche auto optionnelle
- **Anime** : bascule standard/anime avec délai configurable
- **Pushover** : alertes imports échoués, liste d'ignorés pour cross-seed

## Stack

- FastAPI + interface web Jinja2
- PostgreSQL + Redis (ARQ)
- Docker Compose

## Démarrage rapide

```bash
cp .env.example .env
docker compose up -d --build
```

- **Interface web** : http://localhost:8000
- **API Swagger** : http://localhost:8000/docs

La configuration peut être modifiée entièrement depuis l'interface web (connexions, intervalles, options Sonarr/Radarr, etc.) sans redémarrage.

## Pages web

| Page | Description |
|------|-------------|
| `/` | Tableau de bord, statut, lancement manuel des tâches |
| `/settings` | Configuration complète par onglets |
| `/ignored` | Imports ignorés (cross-seed) |
| `/excluded` | Médias exclus du traitement auto |
| `/rules` | Règles de format par média |
| `/anime` | Suivi des bascules anime |

## Options utiles ajoutées

- Activation/désactivation de chaque tâche
- Mode simulation (dry-run)
- Exclusion Sonarr/Radarr par tags ou ID média
- Désactivation par saison complète (Sonarr)
- Ignorer séries en cours de diffusion
- Garder suivi Radarr si upgrade disponible
- Seuil d'économie minimale pour upgrades (%)
- Recherche automatique si upgrade trouvé
- Notifications Pushover pour upgrades
- Délai anime configurable

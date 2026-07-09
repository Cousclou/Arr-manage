"""Résolution d'un média Sonarr/Radarr par ID, titre ou ID externe."""

from __future__ import annotations

import httpx

from app.clients.radarr import RadarrClient
from app.clients.sonarr import SonarrClient


class MediaResolveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _normalize_title(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _series_titles(series: dict) -> list[str]:
    titles: list[str] = []
    for key in ("title", "sortTitle"):
        if series.get(key):
            titles.append(_normalize_title(series[key]))
    for alt in series.get("alternateTitles") or []:
        if isinstance(alt, dict) and alt.get("title"):
            titles.append(_normalize_title(alt["title"]))
    return list(dict.fromkeys(titles))


def _movie_titles(movie: dict) -> list[str]:
    titles: list[str] = []
    for key in ("title", "sortTitle"):
        if movie.get(key):
            titles.append(_normalize_title(movie[key]))
    for alt in movie.get("alternateTitles") or []:
        if isinstance(alt, dict) and alt.get("title"):
            titles.append(_normalize_title(alt["title"]))
    return list(dict.fromkeys(titles))


def _match_external_id(value: object, query: int) -> bool:
    if value is None:
        return False
    try:
        return int(value) == query
    except (TypeError, ValueError):
        return False


async def resolve_sonarr_series(
    client: SonarrClient,
    lookup_type: str,
    query: str,
) -> tuple[int, str]:
    """Retourne (series_id, titre) depuis la bibliothèque Sonarr."""
    raw = query.strip()
    if not raw:
        raise MediaResolveError("empty", "Valeur de recherche vide")

    lookup = lookup_type.lower()
    if lookup == "id":
        try:
            series_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID Sonarr invalide") from exc
        try:
            series = await client.get_series_by_id(series_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MediaResolveError("notfound", f"Aucune série avec l'ID {series_id}") from exc
            raise MediaResolveError("search", f"Erreur Sonarr : {exc}") from exc
        return series["id"], series.get("title", "?")

    series_list = await client.get_series()

    if lookup == "tvdb":
        try:
            tvdb_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID TVDB invalide") from exc
        for series in series_list:
            if _match_external_id(series.get("tvdbId"), tvdb_id):
                return series["id"], series.get("title", "?")
        raise MediaResolveError("notfound", f"Aucune série avec TVDB {tvdb_id}")

    if lookup == "tmdb":
        try:
            tmdb_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID TMDb invalide") from exc
        for series in series_list:
            if _match_external_id(series.get("tmdbId"), tmdb_id):
                return series["id"], series.get("title", "?")
        raise MediaResolveError("notfound", f"Aucune série avec TMDb {tmdb_id}")

    if lookup == "title":
        needle = _normalize_title(raw)
        exact = [
            s for s in series_list
            if any(needle == title for title in _series_titles(s))
        ]
        if len(exact) == 1:
            return exact[0]["id"], exact[0].get("title", "?")
        if len(exact) > 1:
            titles = ", ".join(s.get("title", "?") for s in exact[:3])
            raise MediaResolveError("ambiguous", f"Plusieurs séries trouvées : {titles}")

        partial = [
            s for s in series_list
            if any(needle in title for title in _series_titles(s))
        ]
        if len(partial) == 1:
            return partial[0]["id"], partial[0].get("title", "?")
        if len(partial) > 1:
            titles = ", ".join(s.get("title", "?") for s in partial[:3])
            raise MediaResolveError("ambiguous", f"Plusieurs séries trouvées : {titles}")
        raise MediaResolveError("notfound", f"Aucune série correspondant à « {raw} »")

    raise MediaResolveError("invalid", f"Type de recherche inconnu : {lookup_type}")


async def resolve_radarr_movie(
    client: RadarrClient,
    lookup_type: str,
    query: str,
) -> tuple[int, str]:
    """Retourne (movie_id, titre) depuis la bibliothèque Radarr."""
    raw = query.strip()
    if not raw:
        raise MediaResolveError("empty", "Valeur de recherche vide")

    lookup = lookup_type.lower()
    if lookup == "id":
        try:
            movie_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID Radarr invalide") from exc
        try:
            movie = await client.get_movie(movie_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MediaResolveError("notfound", f"Aucun film avec l'ID {movie_id}") from exc
            raise MediaResolveError("search", f"Erreur Radarr : {exc}") from exc
        return movie["id"], movie.get("title", "?")

    movies = await client.get_movies()

    if lookup == "tmdb":
        try:
            tmdb_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID TMDb invalide") from exc
        for movie in movies:
            if _match_external_id(movie.get("tmdbId"), tmdb_id):
                return movie["id"], movie.get("title", "?")
        raise MediaResolveError("notfound", f"Aucun film avec TMDb {tmdb_id}")

    if lookup == "tvdb":
        try:
            tvdb_id = int(raw)
        except ValueError as exc:
            raise MediaResolveError("invalid", "ID TVDB invalide") from exc
        for movie in movies:
            if _match_external_id(movie.get("tvdbId"), tvdb_id):
                return movie["id"], movie.get("title", "?")
        raise MediaResolveError("notfound", f"Aucun film avec TVDB {tvdb_id}")

    if lookup == "title":
        needle = _normalize_title(raw)
        exact = [
            m for m in movies
            if any(needle == title for title in _movie_titles(m))
        ]
        if len(exact) == 1:
            return exact[0]["id"], exact[0].get("title", "?")
        if len(exact) > 1:
            titles = ", ".join(m.get("title", "?") for m in exact[:3])
            raise MediaResolveError("ambiguous", f"Plusieurs films trouvés : {titles}")

        partial = [
            m for m in movies
            if any(needle in title for title in _movie_titles(m))
        ]
        if len(partial) == 1:
            return partial[0]["id"], partial[0].get("title", "?")
        if len(partial) > 1:
            titles = ", ".join(m.get("title", "?") for m in partial[:3])
            raise MediaResolveError("ambiguous", f"Plusieurs films trouvés : {titles}")
        raise MediaResolveError("notfound", f"Aucun film correspondant à « {raw} »")

    raise MediaResolveError("invalid", f"Type de recherche inconnu : {lookup_type}")

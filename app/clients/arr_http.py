"""Requêtes HTTP partagées pour Sonarr, Radarr et Prowlarr."""

from __future__ import annotations

from typing import Any

import httpx


def normalize_arr_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def alternate_scheme(url: str) -> str | None:
    if url.startswith("https://"):
        return "http://" + url[8:]
    if url.startswith("http://"):
        return "https://" + url[7:]
    return None


def candidate_urls(base_url: str, resolved_url: str | None = None) -> list[str]:
    primary = normalize_arr_url(resolved_url or base_url)
    if not primary:
        return []
    urls = [primary]
    alt = alternate_scheme(primary)
    if alt and alt not in urls:
        urls.append(alt)
    return urls


def is_ssl_scheme_mismatch(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "wrong_version_number" in msg or "wrong version number" in msg


def friendly_connection_error(exc: BaseException, service: str, url: str) -> str:
    msg = str(exc)
    if is_ssl_scheme_mismatch(exc):
        scheme = "https" if url.startswith("https://") else "http"
        other = "http" if scheme == "https" else "https"
        return (
            f"Erreur SSL vers {service} — l'URL utilise {scheme}:// "
            f"mais le service répond peut-être en {other}://. "
            f"Vérifiez l'URL dans Configuration."
        )
    if "401" in msg or "403" in msg:
        return f"Clé API {service} invalide."
    if "Connection refused" in msg or "Name or service not known" in msg:
        return f"Impossible de joindre {service} à {url}."
    return f"{service} : {msg[:200]}"


async def arr_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    *,
    resolved_url: str | None = None,
    json: Any = None,
    params: dict | None = None,
    timeout: float = 120.0,
) -> tuple[httpx.Response, str]:
    """Exécute une requête avec repli http↔https si erreur SSL."""
    last_err: Exception | None = None

    for url in candidate_urls(base_url, resolved_url):
        try:
            async with httpx.AsyncClient(
                base_url=url,
                headers={"X-Api-Key": api_key},
                timeout=timeout,
                verify=False,
            ) as client:
                resp = await client.request(method, path, json=json, params=params or {})
                resp.raise_for_status()
                return resp, url
        except Exception as e:
            if is_ssl_scheme_mismatch(e):
                last_err = e
                continue
            raise

    if last_err:
        raise last_err
    raise httpx.ConnectError(f"URL invalide : {base_url}")

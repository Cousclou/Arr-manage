"""Client HTTP pour l'API Prowlarr v1."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ProwlarrClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else ""
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-Api-Key": self.api_key},
                timeout=120.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        client = await self._get_client()
        resp = await client.get(f"/api/v1{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, data: Any = None, **params: Any) -> Any:
        client = await self._get_client()
        resp = await client.post(f"/api/v1{path}", json=data, params=params)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def get_system_status(self) -> dict:
        return await self.get("/system/status")

    async def get_indexers(self) -> list[dict]:
        return await self.get("/indexer")

    async def get_indexer(self, indexer_id: int) -> dict:
        return await self.get(f"/indexer/{indexer_id}")

    async def get_indexer_status(self) -> list[dict]:
        return await self.get("/indexerstatus")

    async def test_indexer(self, indexer: dict, *, force: bool = False) -> bool:
        """Teste un indexeur. Retourne True si le test réussit."""
        try:
            await self.post("/indexer/test", indexer, forceTest=force)
            return True
        except httpx.HTTPStatusError as e:
            logger.warning("Test Prowlarr échoué pour %s: %s", indexer.get("name"), e)
            return False
        except Exception as e:
            logger.warning("Erreur test Prowlarr %s: %s", indexer.get("name"), e)
            return False

    async def test_all_indexers(self) -> bool:
        try:
            await self.post("/indexer/testall")
            return True
        except Exception as e:
            logger.warning("Testall Prowlarr échoué: %s", e)
            return False

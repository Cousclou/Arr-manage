"""Client HTTP pour l'API Prowlarr v1."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.clients.arr_http import arr_request, normalize_arr_url

logger = logging.getLogger(__name__)


class ProwlarrClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = normalize_arr_url(base_url or "")
        self.api_key = (api_key or "").strip()
        self._resolved_url: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def close(self) -> None:
        return None

    async def get(self, path: str, **params: Any) -> Any:
        resp, self._resolved_url = await arr_request(
            self.base_url,
            self.api_key,
            "GET",
            f"/api/v1{path}",
            resolved_url=self._resolved_url,
            params=params,
        )
        return resp.json()

    async def post(self, path: str, data: Any = None, **params: Any) -> Any:
        resp, self._resolved_url = await arr_request(
            self.base_url,
            self.api_key,
            "POST",
            f"/api/v1{path}",
            resolved_url=self._resolved_url,
            json=data,
            params=params,
        )
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

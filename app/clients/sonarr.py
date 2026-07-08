"""Client HTTP pour l'API Sonarr v3."""

from typing import Any

import httpx

from app.config import get_settings


class SonarrClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.sonarr_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.sonarr_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-Api-Key": self.api_key},
                timeout=60.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        client = await self._get_client()
        resp = await client.get(f"/api/v3{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def put(self, path: str, data: Any) -> Any:
        client = await self._get_client()
        resp = await client.put(f"/api/v3{path}", json=data)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, data: Any = None, **params: Any) -> Any:
        client = await self._get_client()
        resp = await client.post(f"/api/v3{path}", json=data, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_series(self) -> list[dict]:
        return await self.get("/series")

    async def get_series_by_id(self, series_id: int) -> dict:
        return await self.get(f"/series/{series_id}")

    async def update_series(self, series: dict) -> dict:
        return await self.put("/series", series)

    async def get_episodes(self, series_id: int) -> list[dict]:
        return await self.get("/episode", seriesId=series_id)

    async def set_episode_monitored(self, episode_ids: list[int], monitored: bool) -> None:
        episodes = await self.get("/episode", episodeIds=",".join(str(i) for i in episode_ids))
        for ep in episodes:
            ep["monitored"] = monitored
        await self.put("/episode/monitor", episodes)

    async def get_queue(self) -> dict:
        return await self.get("/queue", page=1, pageSize=100, includeUnknownSeriesItems=True)

    async def get_history(self, page: int = 1, page_size: int = 50) -> dict:
        return await self.get("/history", page=page, pageSize=page_size)

    async def get_episode_file(self, file_id: int) -> dict:
        return await self.get(f"/episodefile/{file_id}")

    async def search_releases(self, episode_id: int) -> list[dict]:
        return await self.post("/release", params={"episodeId": episode_id})

    async def trigger_episode_search(self, episode_ids: list[int]) -> dict:
        return await self.post("/command", {"name": "EpisodeSearch", "episodeIds": episode_ids})

    async def get_tags(self) -> list[dict]:
        return await self.get("/tag")

    async def get_system_status(self) -> dict:
        return await self.get("/system/status")

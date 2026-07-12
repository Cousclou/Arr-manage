"""Client HTTP pour l'API Sonarr v3."""

from typing import Any

import httpx

from app.clients.arr_http import arr_request, normalize_arr_url
from app.config import get_settings


class SonarrClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.base_url = normalize_arr_url(base_url or settings.sonarr_url or "")
        raw_key = api_key if api_key is not None else settings.sonarr_api_key
        self.api_key = (raw_key or "").strip()
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
            f"/api/v3{path}",
            resolved_url=self._resolved_url,
            params=params,
        )
        return resp.json()

    async def put(self, path: str, data: Any) -> Any:
        resp, self._resolved_url = await arr_request(
            self.base_url,
            self.api_key,
            "PUT",
            f"/api/v3{path}",
            resolved_url=self._resolved_url,
            json=data,
        )
        return resp.json()

    async def post(self, path: str, data: Any = None, **params: Any) -> Any:
        resp, self._resolved_url = await arr_request(
            self.base_url,
            self.api_key,
            "POST",
            f"/api/v3{path}",
            resolved_url=self._resolved_url,
            json=data,
            params=params,
        )
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

    async def get_history(self, page: int = 1, page_size: int = 50, event_type: str | None = None) -> dict:
        params: dict = {"page": page, "pageSize": page_size}
        if event_type:
            params["eventType"] = event_type
        return await self.get("/history", **params)

    async def get_episode_file(self, file_id: int) -> dict:
        return await self.get(f"/episodefile/{file_id}")

    async def search_releases(self, episode_id: int) -> list[dict]:
        return await self.get("/release", episodeId=episode_id)

    async def search_season_releases(self, series_id: int, season_number: int) -> list[dict]:
        return await self.get("/release", seriesId=series_id, seasonNumber=season_number)

    async def get_wanted_missing(
        self,
        page: int = 1,
        page_size: int = 100,
        *,
        include_series: bool = True,
    ) -> dict:
        return await self.get(
            "/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="series.title",
            sortDirection="ascending",
            includeSeries=include_series,
        )

    async def trigger_episode_search(self, episode_ids: list[int]) -> dict:
        return await self.post("/command", {"name": "EpisodeSearch", "episodeIds": episode_ids})

    async def trigger_season_search(self, series_id: int, season_number: int) -> dict:
        return await self.post("/command", {
            "name": "SeasonSearch",
            "seriesId": series_id,
            "seasonNumber": season_number,
        })

    async def grab_release(self, release: dict) -> dict:
        return await self.post("/release", release)

    async def get_tags(self) -> list[dict]:
        return await self.get("/tag")

    async def get_system_status(self) -> dict:
        return await self.get("/system/status")

    async def get_health(self) -> list[dict]:
        return await self.get("/health")

    async def get_indexers(self) -> list[dict]:
        return await self.get("/indexer")

    async def get_indexer(self, indexer_id: int) -> dict:
        return await self.get(f"/indexer/{indexer_id}")

    async def test_indexer(self, indexer: dict) -> bool:
        try:
            await self.post("/indexer/test", indexer)
            return True
        except httpx.HTTPStatusError:
            return False
        except Exception:
            return False

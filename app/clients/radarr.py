"""Client HTTP pour l'API Radarr v3."""

from typing import Any

import httpx

from app.clients.arr_http import arr_request, normalize_arr_url
from app.config import get_settings


class RadarrClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.base_url = normalize_arr_url(base_url or settings.radarr_url or "")
        raw_key = api_key if api_key is not None else settings.radarr_api_key
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

    async def get_movies(self) -> list[dict]:
        return await self.get("/movie")

    async def get_movie(self, movie_id: int) -> dict:
        return await self.get(f"/movie/{movie_id}")

    async def update_movie(self, movie: dict) -> dict:
        return await self.put("/movie", movie)

    async def get_queue(self) -> dict:
        return await self.get("/queue", page=1, pageSize=100)

    async def get_history(self, page: int = 1, page_size: int = 50, event_type: str | None = None) -> dict:
        params: dict = {"page": page, "pageSize": page_size}
        if event_type:
            params["eventType"] = event_type
        return await self.get("/history", **params)

    async def get_movie_file(self, file_id: int) -> dict:
        return await self.get(f"/moviefile/{file_id}")

    async def search_releases(self, movie_id: int) -> list[dict]:
        return await self.get("/release", movieId=movie_id)

    async def get_wanted_missing(
        self,
        page: int = 1,
        page_size: int = 100,
        *,
        include_movie: bool = True,
    ) -> dict:
        return await self.get(
            "/wanted/missing",
            page=page,
            pageSize=page_size,
            sortKey="title",
            sortDirection="ascending",
            includeMovie=include_movie,
        )

    async def trigger_movie_search(self, movie_ids: list[int]) -> dict:
        return await self.post("/command", {"name": "MoviesSearch", "movieIds": movie_ids})

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

    async def test_all_indexers(self) -> bool:
        try:
            await self.post("/indexer/testall")
            return True
        except Exception:
            return False

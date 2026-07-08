"""Client Pushover pour les notifications."""

import httpx

from app.config import get_settings


class PushoverClient:
    def __init__(self, user_key: str | None = None, api_token: str | None = None) -> None:
        settings = get_settings()
        self.user_key = user_key if user_key is not None else settings.pushover_user_key
        self.api_token = api_token if api_token is not None else settings.pushover_api_token

    @property
    def configured(self) -> bool:
        return bool(self.user_key and self.api_token)

    async def send(self, title: str, message: str, priority: int = 0) -> bool:
        if not self.configured:
            return False

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": self.api_token,
                    "user": self.user_key,
                    "title": title,
                    "message": message,
                    "priority": priority,
                },
            )
            return resp.status_code == 200

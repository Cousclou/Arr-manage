"""Client Pushover pour les notifications."""

import httpx

from app.config import get_settings

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


class PushoverClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.user_key = settings.pushover_user_key
        self.api_token = settings.pushover_api_token

    @property
    def configured(self) -> bool:
        return bool(self.user_key and self.api_token)

    async def send(self, title: str, message: str, priority: int = 0) -> bool:
        if not self.configured:
            return False

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                PUSHOVER_URL,
                data={
                    "token": self.api_token,
                    "user": self.user_key,
                    "title": title,
                    "message": message,
                    "priority": priority,
                },
            )
            return resp.status_code == 200

"""Bluesky client for posting highlight clips (single account)."""

from __future__ import annotations

import logging

from atproto import AsyncClient

from config.settings import get_settings

logger = logging.getLogger(__name__)


class BlueskyClient:
    """Post text/video to Bluesky.

    Credentials come from Settings (``.env`` locally, SSM-fed env vars in
    Lambda) — one account, no per-team switching.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._username = settings.bluesky_handle
        self._password = settings.bluesky_app_password.get_secret_value()
        self.client = AsyncClient(base_url=settings.bluesky_pds_host)

    async def _login(self) -> None:
        logger.info("logging into bsky")
        await self.client.login(self._username, self._password)

    async def post(self, text: str) -> str:
        """Post plain text. Returns the post URI."""
        await self._login()
        logger.info("posting text post")
        resp = await self.client.send_post(text=text)
        return resp.uri

    async def post_video(self, text: str, video: bytes, alt: str = "Video") -> str:
        """Post text + video bytes. Returns the post URI."""
        await self._login()
        logger.info("posting video (%d bytes)", len(video))
        resp = await self.client.send_video(text=text, video=video, video_alt=alt)
        return resp.uri

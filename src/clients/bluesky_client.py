"""Bluesky client for posting highlight clips (single account)."""

from __future__ import annotations

import logging

from atproto import AsyncClient, client_utils

from config.settings import get_settings

logger = logging.getLogger(__name__)


class BlueskyClient:
    """Post text/video to Bluesky.

    Credentials come from Settings (``.env`` locally, SSM-fed env vars in
    Lambda) — one account, no per-team switching.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._username = self.settings.bluesky_handle
        self._password = self.settings.bluesky_app_password.get_secret_value()
        self.client = AsyncClient()
        self.text_builder = client_utils.TextBuilder()

    async def _login(self) -> None:
        logger.info("logging into bsky")
        await self.client.login(self._username, self._password)

    def _build_text(self, text, tag):
        return self.text_builder.text(text).tag(f"#{tag}", tag)

    async def post_text(self, text: str, tag: str) -> str:
        """Post plain text. Returns the post URI."""
        await self._login()
        logger.info("posting text post")
        built_text = self._build_text(text, tag)
        resp = await self.client.send_post(text=built_text)
        return resp.uri

    async def post_video(
        self, text: str, tag: str, video: bytes, alt: str = "Video"
    ) -> str:
        """Post text + video bytes. Returns the post URI."""
        await self._login()
        logger.info("posting video (%d bytes)", len(video))
        built_text = self._build_text(text, tag)
        resp = await self.client.send_video(text=built_text, video=video, video_alt=alt)
        return resp.uri

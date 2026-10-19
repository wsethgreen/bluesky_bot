"""Bluesky client for posting goal text and highlight clips (single account)."""

from __future__ import annotations

import logging

from atproto import AsyncClient, client_utils, models

from config.settings import get_settings
from models.bluesky import BlueskyPostRef

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

    def _build_leading_tag_text(self, text: str, tag: str):
        """Rich text whose hashtag facet is the leading ``#TAG`` token.

        Goal text posts open with e.g. ``#CBJ goal!``; detect that prefix
        and attach the tag facet to it so the post starts with a tappable
        hashtag instead of ending with one. Text without the prefix is
        sent as plain text.
        """
        prefix = f"#{tag}"
        builder = client_utils.TextBuilder()
        if text.startswith(prefix):
            return builder.tag(prefix, tag).text(text[len(prefix) :])
        return builder.text(text)

    async def post_text(self, text: str, tag: str) -> BlueskyPostRef:
        """Post plain text. Returns the new post's strong ref.

        When ``text`` starts with ``#{tag}`` (the goal post format), that
        leading hashtag becomes the post's tag facet; there is no
        trailing hashtag.
        """
        await self._login()
        logger.info("posting text post")
        built_text = self._build_leading_tag_text(text, tag)
        resp = await self.client.send_post(text=built_text)
        return BlueskyPostRef(uri=resp.uri, cid=resp.cid)

    async def post_video(
        self,
        text: str,
        tag: str,
        video: bytes,
        alt: str = "Video",
        *,
        quote: BlueskyPostRef | None = None,
    ) -> BlueskyPostRef:
        """Post text + video bytes, optionally quoting another post.

        A post record has a single ``embed`` slot, so a quoted video uses
        ``app.bsky.embed.recordWithMedia``: the record embed carries the
        quote ref, the media embed carries the video blob. Without a
        quote it's a plain ``app.bsky.embed.video`` post. Returns the new
        post's strong ref.
        """
        await self._login()
        logger.info(
            "posting video (%d bytes)%s",
            len(video),
            " quoting " + quote.uri if quote else "",
        )
        built_text = self._build_text(text, tag)
        if quote is None:
            resp = await self.client.send_video(
                text=built_text, video=video, video_alt=alt
            )
        else:
            blob = await self.client.upload_blob(video)
            embed = models.AppBskyEmbedRecordWithMedia.Main(
                record=models.AppBskyEmbedRecord.Main(
                    record=models.ComAtprotoRepoStrongRef.Main(
                        uri=quote.uri, cid=quote.cid
                    )
                ),
                media=models.AppBskyEmbedVideo.Main(video=blob.blob, alt=alt),
            )
            resp = await self.client.send_post(text=built_text, embed=embed)
        return BlueskyPostRef(uri=resp.uri, cid=resp.cid)

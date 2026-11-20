"""Bluesky client for posting text and highlight clips to a team account."""

from __future__ import annotations

import logging

from atproto import AsyncClient, client_utils, models

from models.bluesky import BlueskyPostRef

logger = logging.getLogger(__name__)


class BlueskyClient:
    """Post text/video to Bluesky from one team's account.

    The caller injects the credentials — usually pulled from a Settings
    account group (``config.cbj`` / ``config.cavs``) — so this class
    has no knowledge of config, accounts, or leagues and the same
    client works for any Bluesky account.
    """

    def __init__(self, account_handle: str, account_password: str) -> None:
        self._username = account_handle
        self._password = account_password
        self.client = AsyncClient()
        self.text_builder = client_utils.TextBuilder()

    async def _login(self) -> None:
        logger.info("logging into bsky as %s", self._username)
        await self.client.login(self._username, self._password)

    def _build_text(self, text: str) -> client_utils.TextBuilder:
        """Rich text with a facet on the post's hashtag.

        Bluesky only makes a hashtag tappable when a facet covers it,
        so the ``#word`` in the text — a goal header's ``#CBJ``, the
        ``#LetEmKnow`` on a quarter board or video caption — is split
        out and faceted. Every post format carries exactly one; the
        builder only facets what's there, it never invents a tag.

        atproto's ``TextBuilder`` mutates in place (its buffer and
        facet list accumulate), so each post starts from a clean
        ``self.text_builder`` — a reused instance would carry the
        previous post's text into the next one.
        """
        self.text_builder = client_utils.TextBuilder()
        builder = self.text_builder
        start = text.find("#")
        if start == -1:
            return builder.text(text)
        # The tag runs from "#" up to the first character that can't
        # be part of a hashtag.
        end = start + 1
        while end < len(text) and (text[end].isalnum() or text[end] == "_"):
            end += 1
        if end == start + 1:
            # A "#" with nothing tag-like after it — plain text.
            return builder.text(text)
        return (
            builder.text(text[:start])
            .tag(text[start:end], text[start + 1 : end])
            .text(text[end:])
        )

    async def post_text(self, text: str, tag: str) -> BlueskyPostRef:
        """Post plain text. Returns the new post's strong ref.

        The hashtag already in the text (the goal post format leads
        with ``#{tag}``) gets a facet; there is no trailing hashtag
        on text posts.
        """
        await self._login()
        logger.info("posting text post")
        built_text = self._build_text(text)
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
        built_text = self._build_text(text)
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

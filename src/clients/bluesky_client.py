"""Bluesky client for posting text and highlight clips to a team account."""

from __future__ import annotations

import logging
import re

from atproto import AsyncClient, client_utils, models

from models.bluesky import BlueskyPostRef

logger = logging.getLogger(__name__)

# A hashtag as Bluesky renders one: "#" at a word boundary followed by
# tag characters. Post formats put hashtags in the body — goal headers
# (``#CBJ power play goal!``), video captions (``... #CLE``), the
# ``#LetEmKnow`` tag on quarter-end boards — and each one needs its own
# facet to be tappable.
_HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]+)")


class BlueskyClient:
    """Post text/video to Bluesky from one team's account.

    The caller injects the credentials — usually pulled from a Settings
    account group (``settings.cbj`` / ``settings.cavs``) — so this class
    has no knowledge of settings, accounts, or leagues and the same
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
        """Rich text with a facet on every hashtag in ``text``.

        Bluesky only makes a hashtag tappable when a facet covers it,
        so every ``#word`` gets its own facet — not just the first one
        (a ``#CBJ ...`` header plus ``#LetEmKnow`` would otherwise
        leave the second tag inert).

        The post formats carry their own hashtags in the body — goal
        headers (``#CBJ power play goal!``), video captions
        (``🎥 #CBJ``, ``... #CLE``), quarter boards ending their first
        line with ``#LetEmKnow`` — so the builder only facets what's
        there; it never invents a tag.

        atproto's ``TextBuilder`` mutates in place (its buffer and
        facet list accumulate), so each post starts from a clean
        ``self.text_builder`` — a reused instance would carry the
        previous post's text into the next one.
        """
        self.text_builder = client_utils.TextBuilder()
        builder = self.text_builder
        position = 0
        for match in _HASHTAG_RE.finditer(text):
            if match.start() > position:
                builder = builder.text(text[position : match.start()])
            display = match.group(0)
            builder = builder.tag(display, display[1:])
            position = match.end()
        if position < len(text):
            builder = builder.text(text[position:])
        return builder

    async def post_text(self, text: str, tag: str) -> BlueskyPostRef:
        """Post plain text. Returns the new post's strong ref.

        Every hashtag already in the text (the goal post format leads
        with ``#{tag}``) gets its own facet; there is no trailing
        hashtag on text posts.
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

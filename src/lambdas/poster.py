"""Poster Lambda: DynamoDB pending posts -> Bluesky.

Currently dry-run: logs what would be posted. Uncomment the Bluesky block
to go live (requires BLUESKY_* env vars + video download wiring).
"""

from __future__ import annotations

import asyncio
import logging

from repos.posts import PostsRepo

# from clients.bsky_client import BskyClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def _post(event: dict) -> dict:
    posts = PostsRepo()
    # bsky = BskyClient()

    limit = int(event.get("limit", 25))
    game_id = event.get("game_id")
    pending = posts.list_pending(limit=limit)
    if game_id is not None:
        pending = [p for p in pending if p.get("game_id") == int(game_id)]

    posted = 0
    for item in pending:
        logger.info("found content to post: %s", item)
        # TODO(go-live):
        # video_bytes = await download_clip_bytes(item["clip_id"])  # Brightcove MP4
        # text = build_post_text(item)  # e.g. "GOAL: <scorer> (CBJ) ..."
        # uri = await bsky.post_video(text=text, video=video_bytes)
        # posts.mark_posted(item["clip_id"], uri)
        posted += 0  # dry-run: nothing marked posted

    return {"status": "ok", "pending": len(pending), "posted": posted}


def handler(event: dict | None, context) -> dict:
    """Entry point. Event: {"game_id"?, "limit"?} (Scheduler passes game_id)."""
    result = asyncio.run(_post(event or {}))
    logger.info("poster result: %s", result)
    return result

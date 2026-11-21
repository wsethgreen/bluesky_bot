"""Bluesky publishing job: post the oldest pending text/video post.

Runs every minute on game days. Claims the oldest ``posted=False`` row
whose game is inside its posting window (``[now - window_hours, now]``
on ``Game.start_time``) with ``FOR UPDATE SKIP LOCKED`` — rows outside
the window, or waiting out a retry backoff, are never locked, so a
stale row awaiting ``--force`` cannot head-of-line block newer
in-window posts behind it. Text rows (goal announcements,
end-of-period scoreboards, and penalty updates) go out immediately.
Video rows resolve a direct MP4 URL for the owning game's league
(``_resolver_for``: Brightcove for the NHL, the claim-time content-feed
URL for the NBA), download it, and post it with the short caption —
quoting the goal's text post when that post is already up, standalone
otherwise.
Every row publishes from its league's own Bluesky account
(``_account_for``: the CBJ account for the NHL, the Cavs account for
the NBA), so the two bots never cross-post.
Rows are claimed oldest-first and a goal's text row always precedes its
video row, so the quote is normally available by the time the video
posts. Any failure leaves the row unposted and schedules the next
attempt with an exponential backoff (:func:`retry_delay`), so a
transient upstream error has time to clear; after ``MAX_ATTEMPTS``
failures the row is marked ``defunct`` and never retried on its own.

When ``env == "local"`` nothing is sent to Bluesky: the post's content
(text, or caption + highlight URL) is logged instead, and the row is
consumed with a synthetic ``local://`` ref so the queue keeps draining
and nothing re-logs on the next run. Brightcove and Bluesky credentials
are not touched in that mode.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx2

from clients.bluesky_client import BlueskyClient
from clients.http_client import HttpClient
from db.models.game import Game
from models.bluesky import BlueskyPostRef
from models.enum import PostKind
from repos.posts_repo import MAX_ATTEMPTS, PostsRepo

if TYPE_CHECKING:
    from config.settings import BlueskyAccountSettings, Settings
    from db.models.post import Post
    from jobs.base import WorkerContext

logger = logging.getLogger(__name__)

JOB_ID = "poster"


async def post_pending(ctx: WorkerContext, *, force: bool = False) -> str:
    """Post the oldest pending Bluesky post (text or video), one per run.

    Non-video rows — goal text and period-end scoreboards alike — share
    the plain-text branch below; only video rows need clip resolution.

    ``force`` (manual runner only) bypasses the posting window
    (``config.poster.window_hours``) entirely — both the claim's
    window filter and the inline re-check below — and the retry
    backoff on a previously failed row, so late posts from a finished
    game (or one that just failed an attempt) can still go out.

    ``ctx.config.env == "local"`` logs the post instead of publishing
    it (see module docstring); without ``force`` the window still
    applies, so ``force`` remains the way to drain rows from outside it.

    No game-day gate: off-days with nothing pending cost one indexed
    read. Rows outside their window are never claimed (they wait for
    ``--force``), so this needs no separate off-day short-circuit and
    an empty queue is distinguishable from "rows waiting out of window"
    in the returned summary.
    """
    async with ctx.sessions() as session:
        posts = PostsRepo(session)
        window_hours = ctx.settings.poster.window_hours
        pending = await posts.claim_pending_batch(
            limit=1, window_hours=window_hours, force=force
        )
        if not pending:
            logger.info("No pending posts")
            await session.commit()
            waiting = await posts.count_pending()
            if waiting:
                return (
                    f"no claimable posts ({waiting} pending — outside "
                    f"the window or waiting out a retry backoff; "
                    f"--force drains them)"
                )
            return "no pending posts"

        post = pending[0]
        label = f"{post.kind} post {post.id}"
        try:
            game = await session.get(Game, post.game_id)
            if game is None:
                await session.rollback()
                return f"{label}: no game row found"

            # Defense in depth: the claim already filtered on this exact
            # range, but re-check so a claim-side regression can never
            # publish out-of-window (force skips both).
            if not force:
                reason = _window_skip_reason(
                    game.start_time, datetime.now(UTC), window_hours
                )
                if reason is not None:
                    await session.rollback()
                    return f"{label}: {reason}"

            tag = (post.team or game.league).strip().upper()

            if ctx.settings.env.strip().lower() == "local":
                # Dry run: log what would go out instead of calling
                # Bluesky, then consume the row with a synthetic local://
                # ref so the queue drains instead of re-logging this row
                # on every run.
                detail = _would_post_detail(post)
                await posts.mark_posted(post.id, uri=f"local://{post.kind}/{post.id}")
                await session.commit()
                logger.info("local env: would post %s: %s", label, detail)
                return f"{label}: local env, logged only ({detail})"

            creds = _account_for(ctx.settings, game.league)
            bsky = BlueskyClient(
                account_handle=creds.bluesky_handle,
                account_password=creds.bluesky_app_password.get_secret_value(),
            )

            if post.kind == PostKind.VIDEO:
                quote = await _quoted_text_ref(posts, post)

                # League-specific resolution (Brightcove for the NHL,
                # claim-time feed URL for the NBA; more leagues register
                # in _VIDEO_RESOLVERS). None means no playable
                # rendition right now — retry next run.
                resolver = _resolver_for(game.league)
                mp4_url = await resolver(ctx, post)
                if mp4_url is None:
                    return f"{label}: no MP4 rendition available"

                # Download the video bytes.
                async with HttpClient() as http:
                    resp = await http.get(mp4_url, headers={"Accept": "*/*"})
                    video_bytes = resp.content

                ref = await bsky.post_video(
                    text=post.text,
                    tag=tag,
                    video=video_bytes,
                    alt=f"{game.league.upper()} {tag} highlight",
                    quote=quote,
                )
            else:
                ref = await bsky.post_text(text=post.text, tag=tag)

            await posts.mark_posted(post.id, uri=ref.uri, cid=ref.cid)
            await session.commit()
            return f"posted {label} ({ref.uri})"

        except Exception as exc:
            # Grab the PK first: rollback below expires the claimed row,
            # and any attribute read on an expired row (``post.id``
            # included) fires a lazy refresh that can't run here.
            post_id = post.id
            await session.rollback()
            logger.exception("failed to post %s", label)

            # Record the failed attempt and schedule the next one:
            # ``retry_decision`` spaces the attempts out (see
            # ``retry_delay``) so a transient upstream error has time
            # to clear, and gives up once MAX_ATTEMPTS is reached.
            # Re-read the row through the session: the rollback expired
            # the old instance, and ``await``-ing the get runs its
            # refresh inside the greenlet (reading the stale instance's
            # attributes directly raises MissingGreenlet).
            post = await posts.get_post(post_id)
            if post is None:
                return f"{label}: post row disappeared after rollback"
            post.attempts = (post.attempts or 0) + 1
            post.last_error = f"{type(exc).__name__}: {exc}"
            defunct, next_attempt_at = retry_decision(post.attempts, datetime.now(UTC))
            post.defunct = defunct
            post.next_attempt_at = next_attempt_at
            await session.commit()

            if next_attempt_at is None:
                # The one failure a human has to act on: nothing
                # retries this row any more, so make the give-up loud
                # here (grep-able WARNING) and in
                # ``workers.manual status``.
                logger.warning(
                    "giving up on %s after %d attempts — marked "
                    "defunct, it will not be retried; last error: %s",
                    label,
                    post.attempts,
                    post.last_error,
                )
                return f"{label}: gave up after {post.attempts} attempts (defunct)"
            return (
                f"{label}: post failed (attempt {post.attempts}; next try "
                f"{next_attempt_at.isoformat(timespec='seconds')})"
            )


def _window_skip_reason(
    start: datetime, now: datetime, window_hours: int
) -> str | None:
    """Why a post must wait for its window, or None when ``now`` fits.

    The posting window is ``[start, start + window_hours]`` — both
    edges inclusive (a game that just started is postable; one exactly
    ``window_hours`` old still is). A naive ``start`` (legacy row, or a
    driver that drops tz) is read as UTC so it compares safely against
    an aware ``now`` instead of raising.

    Pure so the window semantics are unit-testable; ``post_pending``
    uses it as defense in depth behind the claim query, which filters
    rows on the same range before they are ever locked.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if now < start:
        return f"before game start ({start})"
    window_end = start + timedelta(hours=window_hours)
    if now > window_end:
        return f"outside {window_hours}h posting window (ended {window_end})"
    return None


# Retry schedule for a failed post: the next attempt waits
# RETRY_BASE_DELAY, doubling per failure (3, 6, 12, 24 minutes across
# the four retries before MAX_ATTEMPTS gives up), so the give-up lands
# ~45 minutes after the first failure instead of the ~4 minutes it took
# when every failed run retried on the very next one. RETRY_MAX_DELAY
# caps the gap so raising MAX_ATTEMPTS cannot spin it out to hours.
RETRY_BASE_DELAY = timedelta(minutes=3)
RETRY_MAX_DELAY = timedelta(minutes=60)


def retry_delay(attempts: int) -> timedelta:
    """How long a post that failed ``attempts`` times waits before retrying.

    ``attempts`` is 1-based (the caller counts the failure before
    asking), so the delays double 3 → 6 → 12 → 24 minutes and sum to
    45 minutes over ``MAX_ATTEMPTS``.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts!r}")
    return min(RETRY_BASE_DELAY * (2 ** (attempts - 1)), RETRY_MAX_DELAY)


def retry_decision(attempts: int, now: datetime) -> tuple[bool, datetime | None]:
    """``(defunct, next_attempt_at)`` after a post's ``attempts``-th failure.

    The give-up rule is unchanged — ``attempts >= MAX_ATTEMPTS`` marks
    the row ``defunct`` — but every failure now also schedules the next
    attempt with :func:`retry_delay`, which is what gives a transient
    upstream error time to clear. A Brightcove 403 on a clip whose
    playback policy has not propagated yet used to consume all five
    attempts in four minutes; with the backoff the same clip gets
    ~45 minutes.

    Pure (``now`` is passed in) so the schedule is unit-testable the
    same way as ``_window_skip_reason``.
    """
    if attempts >= MAX_ATTEMPTS:
        return True, None
    return False, now + retry_delay(attempts)


# A video resolver turns a pending video row into a direct-download MP4
# URL, or None when no playable rendition is available yet (retry next
# run). Keyed by ``Game.league``.
VideoResolver = Callable[["WorkerContext", "Post"], Awaitable[str | None]]


# Brightcove statuses that mean "not ready yet" rather than "wrong": a
# freshly uploaded clip's playback policy can take a few minutes to
# propagate, and until it does the API answers 403 even for a valid
# policy key (404 the same way while the clip is not visible yet). Both
# clear on their own, so they behave like a missing rendition and retry
# without consuming an attempt. Every other status (401, 5xx) is a real
# failure and still counts toward MAX_ATTEMPTS.
_NOT_READY_STATUS_CODES = frozenset({403, 404})


async def _nhl_mp4_url(ctx: WorkerContext, post: Post) -> str | None:
    """Brightcove rendition lookup: an NHL ``clip_id`` is a Brightcove video id.

    Returns None when nothing can be posted this run and no attempt
    should be recorded: the video has no MP4 rendition yet, or
    Brightcove answered one of ``_NOT_READY_STATUS_CODES``. Raises when
    the row is malformed (no ``clip_id``) or on any other status, so
    those attempts are counted toward ``MAX_ATTEMPTS``.
    """
    if post.clip_id is None:
        raise ValueError("video row has no clip_id")
    try:
        video = await ctx.nhl.brightcove.get_video(post.clip_id)
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code not in _NOT_READY_STATUS_CODES:
            raise
        logger.info(
            "Brightcove %s for clip %s — not ready yet, retrying "
            "without recording an attempt",
            exc.response.status_code,
            post.clip_id,
        )
        return None
    mp4 = next(
        (s for s in (video.sources or []) if s.container == "MP4"),
        None,
    )
    if mp4 is None or not mp4.src:
        return None
    return mp4.src


async def _nba_mp4_url(ctx: WorkerContext, post: Post) -> str | None:
    """NBA content-feed URL captured at claim time from the team feed.

    The poller picks the best ``videoAssets`` rendition when it claims
    the row and stores it in ``highlight_url``, so the poster needs no
    second feed fetch. A row without one is malformed (claim always
    captures it when a rendition exists), so it raises toward
    ``MAX_ATTEMPTS`` like a missing Brightcove clip id.
    """
    if post.highlight_url is None:
        raise ValueError("video row has no highlight_url")
    return post.highlight_url


_VIDEO_RESOLVERS: dict[str, VideoResolver] = {
    "nhl": _nhl_mp4_url,
    "nba": _nba_mp4_url,
}


def _account_for(settings: Settings, league: str) -> BlueskyAccountSettings:
    """Credentials group for a game's league; an unknown league fails loudly.

    Posting an NHL row from the Cavs account (or vice versa) is the
    mix-up this exists to prevent, so — like ``_resolver_for`` — an
    unregistered league raises instead of falling back to a default
    account. New leagues register in the local mapping below, pairing
    the league with its Settings account group (``config.cbj`` for
    the CBJ/NHL bot, ``config.cavs`` for the Cavs/NBA bot).
    """
    groups: dict[str, BlueskyAccountSettings] = {
        "nhl": settings.cbj,
        "nba": settings.cavs,
    }
    try:
        return groups[league]
    except KeyError:
        raise ValueError(
            f"no Bluesky account for league {league!r} "
            f"(known: {', '.join(sorted(groups))})"
        ) from None


def _resolver_for(league: str) -> VideoResolver:
    """Resolver for a game's league; an unknown league fails loudly.

    A video row must never resolve against the wrong source: treating an
    NBA content id as a Brightcove id would 404 forever (or worse,
    publish the wrong clip), so a missing entry raises instead of
    guessing. New leagues register in ``_VIDEO_RESOLVERS``.
    """
    try:
        return _VIDEO_RESOLVERS[league]
    except KeyError:
        raise ValueError(
            f"no video resolver for league {league!r} "
            f"(known: {', '.join(sorted(_VIDEO_RESOLVERS))})"
        ) from None


def _would_post_detail(post: Post) -> str:
    """Log-ready description of what a local-env run would have published.

    Video rows carry the highlight URL captured at claim time, so the
    dry run never needs Brightcove to say what would go out.
    """
    if post.kind == PostKind.VIDEO:
        return (
            f"video caption {post.text!r} (clip {post.clip_id}), "
            f"highlight {post.highlight_url or '(no highlight url)'}"
        )
    return f"text {post.text!r}"


async def _quoted_text_ref(posts: PostsRepo, post: Post) -> BlueskyPostRef | None:
    """Strong ref of the goal's text post, when it is already published.

    Video rows normally follow their text row (older claim order), so the
    ref is usually available. ``None`` means "post the video standalone":
    the text post may still be retrying, went defunct, or the row is a
    legacy pre-migration video post with no text sibling.
    """
    ref = await posts.get_posted_text_ref(post.game_id, post.event_id)
    return None if ref is None else BlueskyPostRef(uri=ref[0], cid=ref[1])

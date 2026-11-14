"""NBA poll job: DB-first polling of the tracked team's scoreboard game.

The NBA counterpart of ``poll_nhl_live``, minus the play-by-play work:
it keeps the game/team rows fresh, queues one end-of-quarter post per
buzzer (score plus each team's PTS/REB/AST leaders, see
``_queue_quarter_ends``), and — after FINAL — queues ``VIDEO`` posts for
the team-site feed's post-game videos (game/player highlights and
pressers, see ``_queue_post_game_videos``). The feed publishes those
minutes to ~2 hours after the buzzer, so the video check keeps running
inside ``nba.video_grace_minutes`` from ``ended_at``.

Cost per cycle (one scoreboard call covers state *and* scores, so there
is no separate play-by-play fetch):
- Off-day (cached "no game" check): 0 calls. The negative answer is
  stored per (team, day) in ``game_day_checks``; only the first poll of
  a new day re-checks the API to refresh it.
- Game in DB, still mutable (FUTURE/LIVE): 1 call (scoreboard).
- Each newly-ended quarter: +1 game-summary call (quarter scores and
  team leaders for the post); a caught-up game pays one indexed read of
  ``posts`` instead — already-queued quarters never re-fetch.
- Final, inside the video grace window: +1 content-feed call per cycle
  (post-game videos), plus one indexed ``posts`` read when candidates
  match. Outside the window: 0 calls.
- Game settled (FINAL/POSTPONED/CANCELED): 0 calls; ``poll-nba
  --force`` re-polls regardless (e.g. to pick up a same-day reschedule
  or backfill videos).
- First poll of game day (DB miss): 1 call, plus team-row upserts from
  the payload itself (no separate team lookup, unlike the NHL).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.game import Game
from db.models.team import nba_team_row_id
from models.enum import PostKind
from models.nba_content import NbaContentVideo
from models.nba_live import NbaCompetitor, NbaGameStatus, NbaScheduleGame, NbaSeason
from models.nba_summary import NbaSummaryLineScore, NbaSummaryResponse, NbaTeamLeaders
from repos.game_day_checks_repo import GameDayChecksRepo
from repos.games_repo import GamesRepo
from repos.posts_repo import PostsRepo
from repos.teams_repo import TeamsRepo
from services.nba_service import NbaService
from services.post_text import PostTextBuilder

if TYPE_CHECKING:
    from jobs.base import WorkerContext

logger = logging.getLogger(__name__)

JOB_ID = "nba_live_poller"

# The feed's game labels date the game in Eastern ("11/11/2026 CLE@NYK");
# every NBA tip time falls on the same Eastern date as its local date
# (latest tip 10:30 PM ET), so converting with this zone is exact.
_NBA_ET = ZoneInfo("America/New_York")

# States after which nothing in today's scoreboard can change the row:
# the poller stops (unless ``--force``). Only FINAL stamps ``ended_at`` —
# a postponed/canceled game never "ended", it just isn't happening.
# A rare same-day reschedule flips a settled row back to live, and only
# a forced poll will notice it; a brand-new game day rediscovers fresh.
NBA_STOP_STATES = frozenset({"FINAL", "POSTPONED", "CANCELED"})


def map_game_state(status: NbaGameStatus | None) -> str | None:
    """Stored ``Game.game_state`` for an ESPN status; ``None`` = unmapped.

    The column is shared across leagues, so ESPN's vocabulary maps into
    the states the NHL poller already stores: ``pre`` → FUTURE,
    ``in`` → LIVE, ``post`` → FINAL. A settled-but-unplayed game is
    detected from the human ``detail`` first ("Postponed" / "Canceled"),
    so it can never masquerade as FINAL. An unrecognized status returns
    ``None`` and the caller keeps the stored state rather than
    overwriting it with a guess.
    """
    if status is None or status.type is None:
        return None
    detail = (status.type.detail or status.type.short_detail or "").strip().upper()
    if "POSTPON" in detail:
        return "POSTPONED"
    if "CANCEL" in detail:
        return "CANCELED"
    state = (status.type.state or "").strip().lower()
    if state == "post":
        return "FINAL"
    if state == "in":
        return "LIVE"
    if state == "pre":
        return "FUTURE"
    return None


def _stop_reason(state: str | None) -> str | None:
    """Why no further scheduled poll can change the row, or None to poll.

    Pure so the gating is unit-testable; ``force`` is handled by the
    caller, mirroring the NHL poller's terminal gate.
    """
    if state in NBA_STOP_STATES:
        return f"{state.lower()}, nothing left to poll"
    return None


def _score_line(away_score: int | None, home_score: int | None) -> str:
    """``"104-117"`` (away first, matching the "AWAY @ HOME" header)."""
    if away_score is None and home_score is None:
        return "no score yet"
    away = "?" if away_score is None else str(away_score)
    home = "?" if home_score is None else str(home_score)
    return f"{away}-{home}"


def season_code(year: int) -> int:
    """NHL-shaped season code from ESPN's season *end* year.

    ESPN labels the 2026-27 season ``2027``; the DB stores NHL-style
    ``20262027`` (start * 10000 + end) so both leagues' ``season``
    columns read the same way.
    """
    return (year - 1) * 10000 + year


def game_type_code(season: NbaSeason | None) -> int:
    """NHL-shaped game type: 1 preseason / 2 regular / 3 postseason.

    Mirrors the NHL stats API's ``gameTypeId`` numbering (2 = regular
    season, see docs/nhl-api.md); falls back to ESPN's own numeric
    ``season.type`` when the readable slug is absent, else 0.
    """
    if season is None:
        return 0
    slug = (season.slug or "").strip().lower()
    if slug == "preseason":
        return 1
    if slug == "regular-season":
        return 2
    if slug == "postseason":
        return 3
    return season.type or 0


# ESPN's per-team game leaders → the post's row labels, in post order
# (the feed serves them points/assists/rebounds; the post prints
# PTS/REB/AST).
NBA_LEADER_LABELS: tuple[tuple[str, str], ...] = (
    ("points", "PTS"),
    ("rebounds", "REB"),
    ("assists", "AST"),
)


def completed_quarters(state: str | None, period: int | None) -> list[int]:
    """Quarter numbers whose buzzer has sounded (1-based), ascending.

    ``FINAL`` means every period the feed counted has ended — the
    period is the clock's final count (4 for regulation, 5+ with OT;
    a missing count falls back to basketball's 4-quarter regulation).
    ``LIVE`` means only periods strictly below the current one have
    ended (period 1 → none yet). Anything else (FUTURE, the settled-
    but-unplayed states, an unknown/absent period) can't claim a
    completed quarter.

    Pure so the queueing gate is unit-testable; the caller pairs it
    with the row's post-apply state and the scoreboard's period.
    """
    if state == "FINAL":
        return list(range(1, (period or 4) + 1))
    if state == "LIVE" and period is not None and period >= 1:
        return list(range(1, period))
    return []


def score_through(
    linescores: list[NbaSummaryLineScore] | None, through: int
) -> int | None:
    """Cumulative score after quarter ``through`` from per-quarter scores.

    Sums the first ``through`` linescore entries — earlier quarters
    are final even while a later one is in progress, so a poll mid-Q2
    still gets Q1's buzzer number rather than the live total. Returns
    ``None`` when the feed can't answer exactly (missing/short list,
    non-numeric entry): a wrong score is worse than a "-", so the data
    decides, not the caller.
    """
    if through < 1 or not linescores or len(linescores) < through:
        return None
    total = 0
    for entry in linescores[:through]:
        try:
            total += int((entry.display_value or "").strip())
        except ValueError:
            return None
    return total


def leader_cells(nba_team_leaders: NbaTeamLeaders | None) -> dict[str, str | None]:
    """Post row cells (``{"PTS": "Mitchell 23", ...}``) for one team's leaders.

    Fixed keys in :data:`NBA_LEADER_LABELS` order — a category ESPN
    doesn't list is ``None`` (rendered "-"), never a dropped row, so
    both teams' rows stay aligned. Cell = leader's display name plus
    value, full name as the name fallback; a category with no usable
    leader stays ``None``.
    """
    cells: dict[str, str | None] = {label: None for _, label in NBA_LEADER_LABELS}
    if nba_team_leaders is None:
        return cells
    by_name = {c.name: c for c in nba_team_leaders.leaders if c.name}
    for espn_name, label in NBA_LEADER_LABELS:
        category = by_name.get(espn_name)
        if category is None or not category.leaders:
            continue
        leader = category.leaders[0]  # ESPN's first entry is the leader
        athlete = leader.athlete
        name = ""
        if athlete is not None:
            name = (athlete.display_name or athlete.full_name or "").strip()
        value = (leader.display_value or "").strip()
        if name and value:
            cells[label] = f"{name} {value}"
        elif value:
            cells[label] = value
    return cells


def team_leaders(summary: NbaSummaryResponse, tri: str | None) -> NbaTeamLeaders | None:
    """The summary's leader entry for a tri-code, or None when absent.

    Tri-codes match case-insensitively; no tri-code means no match
    (the caller then renders a leaders-less post rather than guessing
    whose leaders to print).
    """
    if not tri:
        return None
    want = tri.strip().upper()
    return next(
        (
            entry
            for entry in summary.leaders
            if entry.team is not None
            and (entry.team.abbreviation or "").strip().upper() == want
        ),
        None,
    )


def linescores_by_side(
    summary: NbaSummaryResponse, game_id: int
) -> tuple[list[NbaSummaryLineScore], list[NbaSummaryLineScore]]:
    """``(home, away)`` per-quarter scores from the summary header.

    Raises when either side's linescores are absent: the quarter score
    is the post's core content, and claiming without it would freeze a
    "-" into a row the dedup key can never re-claim. The caller runs
    this *before* committing, so the failure rolls the whole cycle
    back and the next poll retries with fresh data.
    """
    competition = (
        summary.header.competitions[0]
        if summary.header and summary.header.competitions
        else None
    )
    by_side: dict[str, list[NbaSummaryLineScore]] = {}
    if competition is not None:
        for side in competition.competitors:
            if side.home_away in ("home", "away") and side.linescores:
                by_side[side.home_away] = side.linescores
    home, away = by_side.get("home"), by_side.get("away")
    if home is None or away is None:
        raise ValueError(f"NBA summary for game {game_id} missing per-quarter scores")
    return home, away


async def poll_nba_live(ctx: WorkerContext) -> str:
    """Every N min: poll the tracked team's scoreboard game.

    Self-gating via the DB: off-days cost one indexed read of the cached
    schedule check (plus, once per day, one scoreboard call to refresh
    that cache) — no scheduler pause/resume required. After FINAL the
    same cycle also checks the team-site feed for post-game videos
    inside the configured grace window.
    """
    worker = NbaPollWorker(
        nba=ctx.nba,
        sessions=ctx.sessions,
        team_abbreviation=ctx.settings.nba.team_abbreviation,
        video_grace=timedelta(minutes=ctx.settings.nba.video_grace_minutes),
        video_max_duration_seconds=ctx.settings.nba.video_max_duration_seconds,
    )
    return await worker.poll_once()


class NbaPollWorker:
    """One poll cycle for the tracked NBA team.

    1. Look up today's game in the DB; only hit the scoreboard on a miss
       or while the game can still change.
    2. Discovery: record "no game" in ``game_day_checks`` (so off-days
       cost nothing), or store the game row (``league="nba"``) with both
       team rows.
    3. Refresh: apply the scoreboard's state/scores; the first FINAL
       sighting stamps ``ended_at`` — the grace-window start for both
       late quarter posts and the post-game video feed check.
    4. Once FINAL: claim ``VIDEO`` posts for post-game highlights and
       pressers from the team-site feed while ``ended_at`` is inside
       ``video_grace`` (see :meth:`_queue_post_game_videos`).
    """

    def __init__(
        self,
        *,
        nba: NbaService,
        sessions: async_sessionmaker[AsyncSession],
        team_abbreviation: str,
        video_grace: timedelta,
        video_max_duration_seconds: int,
    ) -> None:
        self._nba = nba
        self._sessions = sessions
        self._team = team_abbreviation.strip().upper()
        self._video_grace = video_grace
        self._video_max_duration_seconds = video_max_duration_seconds

    async def poll_once(self, *, force: bool = False, day: str | None = None) -> str:
        """Run a single cycle, returning a human-readable summary.

        ``force`` (manual runner only) ignores the cached off-day answer
        and the settled-state gate, so a row can be re-polled at will.
        ``day`` (manual runner only) targets a previous day's stored
        game instead of today's — pair with ``force`` to backfill a game
        that ended while the scheduler was down (ESPN's scoreboard
        serves past dates, so the final state and any unqueued quarter
        posts still arrive). Scheduled runs pass neither.
        """
        if not self._team:
            return "skipped: NBA_TEAM_ABBREVIATION not set"

        async with self._sessions() as session:
            teams = TeamsRepo(session)
            games = GamesRepo(session)
            checks = GameDayChecksRepo(session)

            day = day or self._nba.today
            row = await games.get_game_by_date(day, self._team, league="nba")
            if row is None:
                # Gate: a stored "no game today" for this (team, day)
                # skips the scoreboard entirely (0 API calls). `force`
                # (manual runner) ignores the cached answer.
                if not force:
                    cached = await checks.get_check(self._team, day)
                    if cached is not None and not cached.has_game:
                        return f"no {self._team} game today (cached)"
                return await self._discover(teams, games, checks, session, day)
            return await self._refresh(
                teams, games, checks, session, row, force=force, day=day
            )

    async def _discover(
        self,
        teams: TeamsRepo,
        games: GamesRepo,
        checks: GameDayChecksRepo,
        session: AsyncSession,
        day: str,
    ) -> str:
        """First poll of game day: one scoreboard call, then store.

        Records the schedule answer in ``game_day_checks`` so later
        off-day cycles skip the API. Failures before that record is
        written leave no cached answer, so the next cycle retries.
        """
        game = await self._nba.get_team_game_on_date(
            date=day, team_abbreviation=self._team
        )
        if game is None:
            await checks.record_check(self._team, day, has_game=False)
            await session.commit()
            return f"no {self._team} game today"
        if game.id is None:
            return f"game found but has no id ({map_game_state(game.game_status)})"

        home, away = game.home_competitor, game.away_competitor
        if (
            home is None
            or home.team is None
            or home.team.id is None
            or away is None
            or away.team is None
            or away.team.id is None
        ):
            return f"game {game.id} missing team ids (not stored)"

        for side in (home, away):
            await self._ensure_team(teams, side)
        row = self._build_game(game)
        await games.upsert_game(row)
        self._apply_row_state(
            row,
            map_game_state(game.game_status),
            home_score=home.score,
            away_score=away.score,
        )
        status = game.game_status
        queued = await self._queue_quarter_ends(
            session,
            row.game_id,
            game,
            completed_quarters(row.game_state, status.period if status else None),
            final=row.game_state == "FINAL",
        )
        video_queued = await self._queue_post_game_videos(
            session, row, self._tri(away), self._tri(home)
        )
        await session.commit()

        summary = (
            f"{self._tri(away)} @ {self._tri(home)} ({row.game_state}): "
            f"game stored, {_score_line(row.away_team_score, row.home_team_score)}"
        )
        if queued:
            summary += f" (+{queued} quarter-end post(s))"
        if video_queued:
            summary += f" (+{video_queued} video post(s))"
        logger.info("poll(nba): %s", summary)
        return summary

    async def _refresh(
        self,
        teams: TeamsRepo,
        games: GamesRepo,
        checks: GameDayChecksRepo,
        session: AsyncSession,
        row: Game,
        *,
        force: bool = False,
        day: str,
    ) -> str:
        """Steady state: game known, one scoreboard fetch (or none)."""
        away = await teams.get_team(row.away_team_id)
        home = await teams.get_team(row.home_team_id)
        if away is None or home is None:
            # Partial write from an earlier failure — rediscover to heal.
            return await self._discover(teams, games, checks, session, day)

        if not force:
            reason = _stop_reason(row.game_state)
            if reason is not None:
                # Settled game: post-game videos keep arriving after the
                # buzzer, so the team-site feed is still checked inside
                # the video grace window (one cheap call per cycle);
                # outside it — or for a game that never finished — the
                # old stop reason stands with no feed call at all.
                video_queued = await self._queue_post_game_videos(
                    session, row, away.tri_code, home.tri_code
                )
                await session.commit()
                if video_queued is None:
                    return f"{away.tri_code} @ {home.tri_code}: {reason}"
                summary = f"{away.tri_code} @ {home.tri_code}: final, checking videos"
                summary += (
                    f" (+{video_queued} video post(s))"
                    if video_queued
                    else " (no new videos)"
                )
                logger.info("poll(nba): %s", summary)
                return summary

        scoreboard = await self._nba.get_scoreboard(day)
        game = next((g for g in scoreboard.events if g.id == row.game_id), None)
        if game is None:
            # A row the feed no longer lists for this day (removed, or
            # queried past the day boundary) keeps its stored state.
            summary = (
                f"{away.tri_code} @ {home.tri_code}: "
                f"not in the {day} scoreboard (state unchanged)"
            )
            logger.info("poll(nba): %s", summary)
            return summary

        home_c, away_c = game.home_competitor, game.away_competitor
        self._apply_row_state(
            row,
            map_game_state(game.game_status),
            home_score=home_c.score if home_c else None,
            away_score=away_c.score if away_c else None,
        )
        status = game.game_status
        queued = await self._queue_quarter_ends(
            session,
            row.game_id,
            game,
            completed_quarters(row.game_state, status.period if status else None),
            final=row.game_state == "FINAL",
        )
        video_queued = await self._queue_post_game_videos(
            session, row, away.tri_code, home.tri_code, force=force
        )
        await session.commit()

        summary = (
            f"{away.tri_code} @ {home.tri_code} ({row.game_state}): "
            f"{_score_line(row.away_team_score, row.home_team_score)}"
        )
        if queued:
            summary += f" (+{queued} quarter-end post(s))"
        if video_queued:
            summary += f" (+{video_queued} video post(s))"
        logger.info("poll(nba): %s", summary)
        return summary

    async def _queue_quarter_ends(
        self,
        session: AsyncSession,
        game_id: int,
        espn_game: NbaScheduleGame,
        completed: list[int],
        *,
        final: bool = False,
    ) -> int:
        """Claim one ``period_end`` post per not-yet-queued quarter.

        Dedup rides the same (game, event, kind) key the NHL poller
        uses, with ``event_id`` = the quarter number — so re-polls
        claim nothing, and an already-caught-up game pays one indexed
        read of ``posts`` instead of a summary fetch. Text freezes at
        claim time: scores come from the summary's per-quarter
        linescores summed through that quarter (a Q1 post caught mid-Q2
        still shows the Q1 buzzer score, not the live total), leaders
        from ESPN's per-team game leaders as of this fetch.

        When ``final`` (the game is over), the deciding quarter — the
        last one whose buzzer sounded (Q4, or the OT period) — gets
        the result header (``Cleveland Cavaliers win! The Cleveland
        Cavaliers defeat the New York Knicks 115-107.``) from
        :meth:`PostTextBuilder.build_quarter_end_text`; the other
        quarters keep the plain end-of-quarter line. Every quarter's
        text also carries the game context when both teams' display
        names are known (``The Cleveland Cavaliers trail the New York
        Knicks by 4.``), read off the ESPN payload, and each header
        line ends with ``#LetEmKnow`` — the Cavs campaign tag, passed
        only when this poller's team is CLE.

        The summary fetch and text build run *before the caller
        commits*: any failure propagates and rolls the whole cycle
        back — stored state included — so the poll retries next round
        instead of advancing to FINAL with quarters unqueued (the
        settled gate would never look again without ``--force``).

        Returns the number of newly claimed rows.
        """
        if not completed:
            return 0
        posts = PostsRepo(session)
        existing = await posts.existing_event_ids(game_id, PostKind.PERIOD_END)
        missing = [q for q in completed if q not in existing]
        if not missing:
            return 0

        # Both teams' display names feed every quarter's text: the
        # context sentence on the plain headers, the result sentence
        # on the deciding one. The name comes from the payload's
        # displayName ("Cleveland Cavaliers"), falling back to the
        # short name and finally the abbreviation.
        deciding = max(completed) if final else None
        tracked_full_name: str | None = None
        opponent_full_name: str | None = None
        tracked_at_home = False
        if espn_game.involves(self._team):
            home_c, away_c = espn_game.home_competitor, espn_game.away_competitor
            tracked_at_home = home_c is not None and (
                self._tri(home_c).upper() == self._team.upper()
            )
            tracked_c = home_c if tracked_at_home else away_c
            opponent_c = away_c if tracked_at_home else home_c
            tracked_full_name = self._team_full_name(tracked_c)
            opponent_full_name = self._team_full_name(opponent_c)

        # #LetEmKnow is the Cavs campaign tag — only on a Cavs game's
        # posts; other teams' posts carry no tag.
        campaign_tag = "LetEmKnow" if self._team.upper() == "CLE" else None

        summary = await self._nba.get_summary(game_id)
        home_lines, away_lines = linescores_by_side(summary, game_id)
        home_tri = self._tri(espn_game.home_competitor)
        away_tri = self._tri(espn_game.away_competitor)
        home_leaders = leader_cells(team_leaders(summary, home_tri))
        away_leaders = leader_cells(team_leaders(summary, away_tri))

        new_posts = 0
        for quarter in missing:
            home_score = score_through(home_lines, quarter)
            away_score = score_through(away_lines, quarter)
            tracked_score = home_score if tracked_at_home else away_score
            opponent_score = away_score if tracked_at_home else home_score
            text = PostTextBuilder.build_quarter_end_text(
                period_number=quarter,
                home_tri=home_tri,
                home_score=home_score,
                away_tri=away_tri,
                away_score=away_score,
                home_leaders=home_leaders,
                away_leaders=away_leaders,
                is_final=quarter == deciding,
                tracked_full_name=tracked_full_name,
                opponent_full_name=opponent_full_name,
                tracked_score=tracked_score,
                opponent_score=opponent_score,
                campaign_tag=campaign_tag,
            )
            if await posts.claim_pending(
                kind=PostKind.PERIOD_END,
                game_id=game_id,
                team=self._team,
                event_id=quarter,
                text=text,
            ):
                new_posts += 1
                logger.info(
                    "poll(nba): quarter-end post queued (quarter %s, game %s)",
                    quarter,
                    game_id,
                )
        return new_posts

    async def _queue_post_game_videos(
        self,
        session: AsyncSession,
        row: Game,
        away_tri: str,
        home_tri: str,
        *,
        force: bool = False,
    ) -> int | None:
        """Claim one ``VIDEO`` post per postable feed video for this game.

        Runs after FINAL — the team-site feed publishes highlights and
        pressers minutes to ~2 hours after the buzzer — and only while
        the ``ended_at`` grace window is open (``force`` bypasses the
        window for backfills). Returns ``None`` when the window is
        closed or the game never finished (no feed call), else the
        number of newly claimed rows (0 = checked, nothing new).

        Candidates come from the newest page of the feed, filtered by
        :meth:`select_postable_videos`: tagged with *this* game via
        ``taxonomy.games`` (Eastern date + away@home), highlights or
        pressers only, and within Bluesky's duration cap. Dedup rides
        the (game, event, kind) key with ``event_id`` = the feed's
        video id; the best MP4 rendition is captured into
        ``highlight_url`` at claim time so the poster never re-fetches.
        """
        if row.game_state != "FINAL":
            return None
        if row.ended_at is None:
            # Legacy row stored FINAL before ended_at existed: start
            # its window now so the check stays bounded.
            row.ended_at = datetime.now(UTC)
        if not force and not self._in_video_grace(row):
            return None

        page = await self._nba.content.get_latest_videos(count=50)
        candidates = self.select_postable_videos(
            page.results.items,
            game_day=self._game_et_date(row.start_time),
            away_tri=away_tri,
            home_tri=home_tri,
            max_duration_seconds=self._video_max_duration_seconds,
        )
        if not candidates:
            return 0

        posts = PostsRepo(session)
        existing = await posts.existing_event_ids(row.game_id, PostKind.VIDEO)
        new_posts = 0
        for video in candidates:
            if video.id in existing:
                continue
            assert video.video_assets is not None  # select_postable_videos filter
            rendition = video.video_assets.best_rendition()
            if rendition is None:
                continue  # no MP4 rendition yet — next cycle re-checks
            # Caption: the feed's description prose (falls back to the
            # title when a feed item has none) plus the #LetEmKnow
            # campaign tag — no emoji prefix. The description is
            # trimmed to whatever room the suffix leaves under
            # Bluesky's 300-char cap.
            suffix = "\n\n#LetEmKnow"
            description = (video.description or video.title).strip()
            budget = 300 - len(suffix)
            if len(description) > budget:
                description = description[: budget - 1].rstrip() + "…"
            caption = f"{description}{suffix}"
            if await posts.claim_pending(
                kind=PostKind.VIDEO,
                game_id=row.game_id,
                team=self._team,
                event_id=video.id,
                text=caption,
                clip_id=video.id,
                highlight_url=rendition.video,
            ):
                new_posts += 1
                logger.info(
                    "poll(nba): video post queued (%s, game %s)",
                    video.title,
                    row.game_id,
                )
        return new_posts

    @staticmethod
    def video_matches_game(
        video: NbaContentVideo, *, game_day: date, away_tri: str, home_tri: str
    ) -> bool:
        """Whether the feed video belongs to the stored game.

        Every game video carries ``taxonomy.games`` labels like
        ``"11/11/2026 CLE@NYK 0022600218"`` — Eastern date plus the
        away@home matchup (ESPN's order), which is exactly what the
        stored game row knows, so both parts must match. Videos
        without the tag (docuseries, team misc) never match; neither
        does a video for the team's *other* recent game when labels
        parse differently — a wrong match would post the wrong
        highlight under the wrong game.
        """
        want_matchup = f"{away_tri}@{home_tri}"
        for label in video.taxonomy.games.values():
            parts = label.split()
            if len(parts) < 2 or parts[1] != want_matchup:
                continue
            try:
                month, day, year = parts[0].split("/")
                label_day = date(int(year), int(month), int(day))
            except ValueError:
                continue
            if label_day == game_day:
                return True
        return False

    @staticmethod
    def is_postable_nba_video(
        video: NbaContentVideo, *, max_duration_seconds: int
    ) -> bool:
        """Whether the feed video is a highlight/presser Bluesky can take.

        Selection: the ``game-highlights`` category slug (game *and*
        player highlight cuts carry it) or a ``Post Game`` title
        (pressers, which arrive as ``media-availability``). Pre-game
        shootarounds, media availability without "Post Game", and
        docuseries don't qualify.

        The duration cap is Bluesky's 10-minute upload limit; videos
        over it are skipped rather than queued to fail at upload. An
        unknown duration is allowed (the feed always carries one in
        practice) and a video without any MP4 rendition is never
        postable.
        """
        assets = video.video_assets
        if assets is None or not assets.renditions:
            return False
        slugs = video.taxonomy.categories
        title = video.title or ""
        if "game-highlights" not in slugs and "Post Game" not in title:
            return False
        duration_ms = assets.duration or max(
            rendition.duration for rendition in assets.renditions.values()
        )
        return duration_ms <= max_duration_seconds * 1000

    @staticmethod
    def select_postable_videos(
        videos: list[NbaContentVideo],
        *,
        game_day: date,
        away_tri: str,
        home_tri: str,
        max_duration_seconds: int,
    ) -> list[NbaContentVideo]:
        """The feed page's videos worth queueing for this game, in feed order."""
        return [
            video
            for video in videos
            if NbaPollWorker.video_matches_game(
                video, game_day=game_day, away_tri=away_tri, home_tri=home_tri
            )
            and NbaPollWorker.is_postable_nba_video(
                video, max_duration_seconds=max_duration_seconds
            )
        ]

    def _in_video_grace(self, row: Game) -> bool:
        """Whether a final game is still inside its post-game video window.

        Highlights typically land ~30-45 minutes after the buzzer and
        pressers sooner, but one observed game took ~80 minutes; the
        window from ``ended_at`` caps the wait either way (0 disables
        video queueing entirely).
        """
        if self._video_grace <= timedelta(0):
            return False
        if row.ended_at is None:
            return True
        ended = row.ended_at
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        return datetime.now(UTC) < ended + self._video_grace

    @staticmethod
    def _game_et_date(start: datetime) -> date:
        """Eastern date of tip-off — the feed's game labels are ET dates."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        return start.astimezone(_NBA_ET).date()

    async def _ensure_team(self, teams: TeamsRepo, competitor: NbaCompetitor) -> None:
        """Store the team row when missing — the scoreboard payload is
        the only team source (no stats-API lookup like the NHL's)."""
        info = competitor.team
        if info is None or info.id is None or not info.abbreviation:
            return
        if await teams.get_team(nba_team_row_id(info.id)) is None:
            await teams.upsert_from_nba(
                team_id=info.id,
                display_name=info.display_name or "",
                tri_code=info.abbreviation,
            )

    @staticmethod
    def _tri(competitor: NbaCompetitor) -> str:
        info = competitor.team
        return (info.abbreviation if info else None) or "?"

    @staticmethod
    def _team_full_name(competitor: NbaCompetitor | None) -> str | None:
        """Full display name for the post's team sentences — ESPN's
        ``displayName`` ("Cleveland Cavaliers"), falling back to the
        short name and finally the abbreviation; ``None`` when the
        payload carries no competitor/team at all (the post then
        omits the sentence rather than guessing)."""
        if competitor is None:
            return None
        info = competitor.team
        if info is None:
            return None
        return info.display_name or info.name or info.abbreviation

    @staticmethod
    def _build_game(game: NbaScheduleGame) -> Game:
        assert game.id is not None
        home, away = game.home_competitor, game.away_competitor
        assert home is not None and home.team is not None and home.team.id is not None
        assert away is not None and away.team is not None and away.team.id is not None
        if game.date:
            start = datetime.fromisoformat(game.date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = datetime.now(UTC)
        return Game(
            game_id=game.id,
            league="nba",
            season=season_code(game.season.year)
            if game.season is not None and game.season.year
            else 0,
            game_type=game_type_code(game.season),
            start_time=start,
            game_state=map_game_state(game.game_status),
            game_schedule_state=None,  # ESPN has no schedule-state word
            away_team_id=nba_team_row_id(away.team.id),
            away_team_score=away.score,
            home_team_id=nba_team_row_id(home.team.id),
            home_team_score=home.score,
        )

    @staticmethod
    def _apply_row_state(
        row: Game,
        state: str | None,
        *,
        home_score: int | None = None,
        away_score: int | None = None,
    ) -> None:
        """Mutate a managed row in place (caller commits).

        ``state`` is ``None`` for an unmappable status, which leaves the
        stored state alone; scores apply only when present so a
        pregame payload can't erase a stored final. The first FINAL
        sighting stamps ``ended_at`` (start of the future highlight-clip
        grace window, mirroring the NHL poller); settled-but-unplayed
        states never stamp.
        """
        if state:
            row.game_state = state
        if row.game_state == "FINAL" and row.ended_at is None:
            row.ended_at = datetime.now(UTC)
        if away_score is not None:
            row.away_team_score = away_score
        if home_score is not None:
            row.home_team_score = home_score

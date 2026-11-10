"""NBA poll job: DB-first polling of the tracked team's scoreboard game.

The NBA counterpart of ``poll_nhl_live``, minus the play-by-play work:
it keeps the game/team rows fresh and queues one end-of-quarter post
per buzzer — score plus each team's PTS/REB/AST leaders (see
``_queue_quarter_ends``). Highlight/video posts land with the video
pipeline.

Cost per cycle (one scoreboard call covers state *and* scores, so there
is no separate play-by-play fetch):
- Off-day (cached "no game" check): 0 calls. The negative answer is
  stored per (team, day) in ``game_day_checks``; only the first poll of
  a new day re-checks the API to refresh it.
- Game in DB, still mutable (FUTURE/LIVE): 1 call (scoreboard).
- Each newly-ended quarter: +1 game-summary call (quarter scores and
  team leaders for the post); a caught-up game pays one indexed read
  of ``posts`` instead — already-queued quarters never re-fetch.
- Game settled (FINAL/POSTPONED/CANCELED): 0 calls; ``poll-nba
  --force`` re-polls regardless (e.g. to pick up a same-day reschedule).
- First poll of game day (DB miss): 1 call, plus team-row upserts from
  the payload itself (no separate team lookup, unlike the NHL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.game import Game
from db.models.team import nba_team_row_id
from models.enum import PostKind
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
    that cache) — no scheduler pause/resume required.
    """
    worker = NbaPollWorker(
        nba=ctx.nba,
        sessions=ctx.sessions,
        team_abbreviation=ctx.settings.nba.team_abbreviation,
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
       sighting stamps ``ended_at`` — the same grace-window start the
       NHL poller uses for late highlight clips.
    """

    def __init__(
        self,
        *,
        nba: NbaService,
        sessions: async_sessionmaker[AsyncSession],
        team_abbreviation: str,
    ) -> None:
        self._nba = nba
        self._sessions = sessions
        self._team = team_abbreviation.strip().upper()

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
        )
        await session.commit()

        summary = (
            f"{self._tri(away)} @ {self._tri(home)} ({row.game_state}): "
            f"game stored, {_score_line(row.away_team_score, row.home_team_score)}"
        )
        if queued:
            summary += f" (+{queued} quarter-end post(s))"
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
                return f"{away.tri_code} @ {home.tri_code}: {reason}"

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
        )
        await session.commit()

        summary = (
            f"{away.tri_code} @ {home.tri_code} ({row.game_state}): "
            f"{_score_line(row.away_team_score, row.home_team_score)}"
        )
        if queued:
            summary += f" (+{queued} quarter-end post(s))"
        logger.info("poll(nba): %s", summary)
        return summary

    async def _queue_quarter_ends(
        self,
        session: AsyncSession,
        game_id: int,
        espn_game: NbaScheduleGame,
        completed: list[int],
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

        summary = await self._nba.get_summary(game_id)
        home_lines, away_lines = linescores_by_side(summary, game_id)
        home_tri = self._tri(espn_game.home_competitor)
        away_tri = self._tri(espn_game.away_competitor)
        home_leaders = leader_cells(team_leaders(summary, home_tri))
        away_leaders = leader_cells(team_leaders(summary, away_tri))

        new_posts = 0
        for quarter in missing:
            text = PostTextBuilder.build_quarter_end_text(
                period_number=quarter,
                home_tri=home_tri,
                home_score=score_through(home_lines, quarter),
                away_tri=away_tri,
                away_score=score_through(away_lines, quarter),
                home_leaders=home_leaders,
                away_leaders=away_leaders,
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

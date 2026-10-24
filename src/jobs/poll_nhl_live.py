"""NHL live-poll job: DB-first polling with minimal NHL API calls.

Job entry point is ``poll_nhl_live``; ``PollWorker`` holds the actual
per-cycle logic (discovery, refresh, goal/post upserts).

Cost per cycle:
- Game already in DB, not final: 1 call (play-by-play).
- Game final in DB: 0 calls.
- Off-day (cached "no game" check): 0 calls. The negative answer is
  stored per (team, day) in ``game_day_checks``; only the first poll of
  a new day re-checks the schedule API to refresh it.
- First poll of game day (DB miss): schedule + play-by-play + team
  lookups only for teams missing from the DB.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.game import Game
from db.models.goal import Goal
from models.enum import PostKind
from models.nhl import NhlPlay, NhlScheduleGame, NhlScheduleTeam
from models.player import NhlPlayer
from repos.game_day_checks_repo import GameDayChecksRepo
from repos.games_repo import GamesRepo
from repos.goals_repo import GoalsRepo
from repos.players_repo import PlayersRepo
from repos.posts_repo import PostsRepo
from repos.teams_repo import TeamsRepo
from services.nhl_service import NhlLiveSnapshot, NhlService
from services.post_text import PostTextBuilder

if TYPE_CHECKING:
    from jobs.base import WorkerContext

logger = logging.getLogger(__name__)

JOB_ID = "nhl_live_poller"


async def poll_nhl_live(ctx: WorkerContext) -> str:
    """Every N min: poll the followed team's game/goals/clips.

    Self-gating via the DB: on off-days this costs one indexed read of
    the cached schedule check (plus, once per day, one schedule call to
    refresh that cache) — no scheduler pause/resume required.
    """
    worker = PollWorker(
        nhl=ctx.nhl,
        sessions=ctx.sessions,
        team_abbreviation=ctx.settings.nhl_team_abbreviation,
    )
    return await worker.poll_once()


# Play-by-play states after which nothing more can happen. Polls for these
# games cost zero API calls.
TERMINAL_STATES = frozenset({"FINAL", "OFF"})

# Player snapshots refresh only when missing or older than this. Keeps the
# per-poll cost at local DB reads after the first sighting.
PLAYER_REFRESH_DAYS = 7


class PollWorker:
    """One poll cycle for the followed team.

    1. Look up today's game in the DB; only hit the schedule API on a miss.
    2. Fetch one play-by-play payload: game state, the team's goals,
       the per-period end plays, and both sides' score/SOG header.
    3. Upsert goals, scorer/assister snapshots, and unposted ``Post`` rows
       for the poster: a text post per goal (claimed immediately at
       detection), a video post once the highlight clip id appears, and
       an "End of N period." post per ``period-end`` play.
    """

    def __init__(
        self,
        *,
        nhl: NhlService,
        sessions: async_sessionmaker[AsyncSession],
        team_abbreviation: str,
    ) -> None:
        self._nhl = nhl
        self._sessions = sessions
        self._team = team_abbreviation.strip().upper()

    async def poll_once(self, *, force: bool = False) -> str:
        """Run a single cycle, returning a human-readable summary.

        ``force`` re-polls even games already marked final — used by the
        manual runner to pick up highlight clips that arrive after the
        game ends.
        """
        if not self._team:
            return "skipped: NHL_TEAM_ABBREVIATION not set"

        async with self._sessions() as session:
            teams = TeamsRepo(session)
            games = GamesRepo(session)
            goals_repo = GoalsRepo(session)
            posts = PostsRepo(session)
            players = PlayersRepo(session)
            checks = GameDayChecksRepo(session)

            game_row = await games.get_game_by_date(self._nhl.today, self._team)
            if game_row is None:
                # Gate: a stored "no game today" for this (team, day)
                # skips discovery entirely (0 API calls). `force` (manual
                # runner) ignores the cached answer and re-checks the API.
                if not force:
                    cached = await checks.get_check(self._team, self._nhl.today)
                    if cached is not None and not cached.has_game:
                        return f"no {self._team} game today (cached)"
                return await self._discover(
                    teams, games, goals_repo, posts, players, checks, session
                )
            return await self._refresh(
                teams,
                games,
                goals_repo,
                posts,
                players,
                checks,
                session,
                game_row,
                force=force,
            )

    async def _discover(
        self,
        teams: TeamsRepo,
        games: GamesRepo,
        goals_repo: GoalsRepo,
        posts: PostsRepo,
        players: PlayersRepo,
        checks: GameDayChecksRepo,
        session: AsyncSession,
    ) -> str:
        """First poll of game day: schedule lookup, then full cycle.

        Records the schedule answer in ``game_day_checks`` so later
        off-day cycles skip the API. Failures before that record is
        written leave no cached answer, so the next cycle retries.
        """
        game = await self._nhl.get_team_game_on_date(team_abbreviation=self._team)
        if game is None:
            await checks.record_check(self._team, self._nhl.today, has_game=False)
            await session.commit()
            return f"no {self._team} game today"
        if game.id is None:
            return f"game found but has no id ({game.game_state})"
        if (
            game.away_team is None
            or game.away_team.id is None
            or game.home_team is None
            or game.home_team.id is None
        ):
            return f"game {game.id} missing team ids (not stored)"

        for side in (game.away_team, game.home_team):
            await self._ensure_team(teams, side)
        await games.upsert_game(self._build_game(game))

        team_id = self._side_id(game)
        if team_id is None:
            await session.commit()
            return f"game {game.id} stored (team side not resolved)"

        snap = await self._nhl.get_live_snapshot(game.id, team_id)
        new_text_posts, new_video_posts, players_refreshed = await self._store_goals(
            goals_repo,
            posts,
            players,
            game.id,
            snap.goals,
            home_tri=game.home_team.abbrev if game.home_team else None,
            away_tri=game.away_team.abbrev if game.away_team else None,
            home_shots=snap.home.sog,
            away_shots=snap.away.sog,
        )
        new_period_posts = await self._store_period_ends(posts, game.id, snap)
        await self._apply_live_state(games, game.id, snap.game_state, snap.goals)
        await session.commit()

        away = game.away_team.abbrev if game.away_team else "?"
        home = game.home_team.abbrev if game.home_team else "?"
        summary = (
            f"{away} @ {home} ({snap.game_state or game.game_state}): "
            f"{len(snap.goals)} goals, {new_text_posts} new text posts, "
            f"{new_video_posts} new video posts, "
            f"{new_period_posts} new period-end posts, "
            f"{players_refreshed} players refreshed"
        )
        logger.info("poll: %s", summary)
        return summary

    async def _refresh(
        self,
        teams: TeamsRepo,
        games: GamesRepo,
        goals_repo: GoalsRepo,
        posts: PostsRepo,
        players: PlayersRepo,
        checks: GameDayChecksRepo,
        session: AsyncSession,
        row: Game,
        *,
        force: bool = False,
    ) -> str:
        """Steady state: game known, one play-by-play fetch (or none)."""
        away = await teams.get_team(row.away_team_id)
        home = await teams.get_team(row.home_team_id)
        if away is None or home is None:
            # Partial write from an earlier failure — rediscover to heal.
            return await self._discover(
                teams, games, goals_repo, posts, players, checks, session
            )

        if row.game_state in TERMINAL_STATES and not force:
            return (
                f"{away.tri_code} @ {home.tri_code} ({row.game_state}): "
                "final, no check needed"
            )

        team_id = away.id if away.tri_code.upper() == self._team else home.id
        snap = await self._nhl.get_live_snapshot(row.game_id, team_id)
        new_text_posts, new_video_posts, players_refreshed = await self._store_goals(
            goals_repo,
            posts,
            players,
            row.game_id,
            snap.goals,
            home_tri=home.tri_code,
            away_tri=away.tri_code,
            home_shots=snap.home.sog,
            away_shots=snap.away.sog,
        )
        new_period_posts = await self._store_period_ends(posts, row.game_id, snap)
        self._apply_row_state(row, snap.game_state, snap.goals)
        await session.commit()

        summary = (
            f"{away.tri_code} @ {home.tri_code} ({snap.game_state or row.game_state}): "
            f"{len(snap.goals)} goals, {new_text_posts} new text posts, "
            f"{new_video_posts} new video posts, "
            f"{new_period_posts} new period-end posts, "
            f"{players_refreshed} players refreshed"
        )
        logger.info("poll: %s", summary)
        return summary

    async def _ensure_team(
        self, teams: TeamsRepo, side: NhlScheduleTeam | None
    ) -> None:
        """Fetch team stats only when the team row is missing."""
        if side is None or side.id is None:
            return
        if await teams.get_team(side.id) is None:
            stats = await self._nhl.get_team(side.id)
            await teams.upsert_from_stats(stats)

    async def _store_goals(
        self,
        goals_repo: GoalsRepo,
        posts: PostsRepo,
        players: PlayersRepo,
        game_id: int,
        plays: list[NhlPlay],
        home_tri: str | None = None,
        away_tri: str | None = None,
        home_shots: int | None = None,
        away_shots: int | None = None,
    ) -> tuple[int, int, int]:
        """Upsert goals, players, and the pending Bluesky posts.

        A text post row is claimed for every goal on every poll — the
        (game, event, kind) unique key dedupes re-polls — so the goal
        text goes out as soon as the poster runs, whether or not the
        highlight clip exists yet. A video row is claimed only once the
        NHL attaches the clip id, which can be several minutes later.

        Both texts are built at detection time (when goal/assist numbers
        and the play-by-play shot counts are accurate) and stored on the
        ``Post`` rows so the poster stays simple. The text row is claimed
        before the video row so, within one game, the goal text post
        always precedes its highlight video.

        Newly-inserted goals get a local stat bump (scorer +1 goal, each
        assister +1 assist) so the post text reflects the fresh count even
        though the NHL stats API lags the play-by-play. The bump is applied
        only on first detection — re-polls see the existing goal row and
        skip it — and the next scheduled API refresh overwrites the row
        with official totals.

        Returns ``(new text posts, new video posts, players refreshed
        from API)``.
        """
        goals: list[Goal] = []
        clips: dict[int, int] = {}  # highlight clip id -> event id
        new_goals: list[Goal] = []
        for play in plays:
            is_new = (
                play.event_id is not None
                and await goals_repo.get_goal(game_id, play.event_id) is None
            )
            goal = await goals_repo.upsert_from_play(game_id, play)
            if goal is None:
                continue
            goals.append(goal)
            if is_new:
                new_goals.append(goal)
            if goal.highlight_clip_id is not None:
                clips[goal.highlight_clip_id] = goal.event_id
        players_refreshed = await self._ensure_players(players, plays)
        await self._bump_player_stats(players, new_goals)

        new_text_posts = 0
        for goal in goals:
            text = await self._build_goal_text(
                players,
                goal,
                home_tri=home_tri,
                away_tri=away_tri,
                home_shots=home_shots,
                away_shots=away_shots,
            )
            if await posts.claim_pending(
                kind=PostKind.TEXT,
                game_id=game_id,
                team=self._team,
                event_id=goal.event_id,
                text=text or "",
            ):
                new_text_posts += 1
                logger.info("poll: new goal text post for event %s", goal.event_id)

        new_video_posts = 0
        caption = PostTextBuilder.build_highlight_video_caption()
        for clip_id, event_id in clips.items():
            highlight_url = self._brightcove_video_url(clip_id)
            if await posts.claim_pending(
                kind=PostKind.VIDEO,
                game_id=game_id,
                team=self._team,
                event_id=event_id,
                text=caption,
                clip_id=clip_id,
                highlight_url=highlight_url,
            ):
                new_video_posts += 1
                logger.info(
                    "poll: new highlight clip %s for event %s (video post row created)",
                    clip_id,
                    event_id,
                )
        return new_text_posts, new_video_posts, players_refreshed

    async def _store_period_ends(
        self,
        posts: PostsRepo,
        game_id: int,
        snap: NhlLiveSnapshot,
    ) -> int:
        """Queue an "End of N period." post per newly-seen period-end play.

        Dedup rides the same (game, event, kind) key as goal posts: each
        ``period-end`` play carries a unique ``event_id``, so re-polls
        claim nothing. The scoreboard text is built here — from the same
        payload the play came in — and frozen onto the row, so later
        polls and the poster publish exactly what the header said at
        claim time.

        Periods whose end was already posted before this code existed —
        or a bot restart mid-game — simply claim whatever period-end
        plays the current payload has that aren't in ``posts`` yet.

        Returns the number of newly claimed rows.
        """
        new_posts = 0
        for play in snap.period_ends:
            if play.event_id is None:
                continue  # no dedup key → would re-queue every poll
            text = PostTextBuilder.build_period_end_text(
                period_number=play.period.number if play.period else None,
                home_tri=snap.home.abbrev,
                home_score=snap.home.score,
                away_tri=snap.away.abbrev,
                away_score=snap.away.score,
                home_shots=snap.home.sog,
                away_shots=snap.away.sog,
            )
            if await posts.claim_pending(
                kind=PostKind.PERIOD_END,
                game_id=game_id,
                team=self._team,
                event_id=play.event_id,
                text=text,
            ):
                new_posts += 1
                logger.info(
                    "poll: new period-end post for event %s (period %s)",
                    play.event_id,
                    play.period.number if play.period else None,
                )
        return new_posts

    async def _build_goal_text(
        self,
        players: PlayersRepo,
        goal,
        *,
        home_tri: str | None = None,
        away_tri: str | None = None,
        home_shots: int | None = None,
        away_shots: int | None = None,
    ) -> str | None:
        """Build post text for a goal, looking up player rows from the DB."""
        if goal is None:
            return None

        scorer = None
        scorer_season_goals = 0
        if goal.scoring_player_id is not None:
            scorer = await players.get_player(goal.scoring_player_id)
            if scorer is not None:
                scorer_season_goals = scorer.goals

        assist_players: list = []
        for pid in (goal.assist1_player_id, goal.assist2_player_id):
            if pid is not None:
                p = await players.get_player(pid)
                assist_players.append(p)
            else:
                assist_players.append(None)

        return PostTextBuilder.build_highlight_post_text(
            team_abbrev=self._team,
            scorer=scorer,
            scorer_season_goals=scorer_season_goals,
            assists=assist_players,
            home_tri=home_tri,
            home_score=goal.home_score,
            away_tri=away_tri,
            away_score=goal.away_score,
            home_shots=home_shots,
            away_shots=away_shots,
            time_remaining=goal.time_remaining,
            period_number=goal.period_number,
        )

    @staticmethod
    async def _bump_player_stats(players: PlayersRepo, goals: list) -> None:
        """Locally bump season stats for freshly-detected goals.

        The stats API lags the play-by-play, so we trust the event we just
        saw: scorer +1 goal (+1 point), each assister +1 assist (+1 point).
        Runs after ``_ensure_players`` (rows exist) and before the clip text
        is built, so post text shows the current count. Idempotent: only
        called for goal rows inserted by this poll.
        """
        for goal in goals:
            if goal.scoring_player_id is not None:
                await players.bump_scorer(goal.scoring_player_id)
            for pid in (goal.assist1_player_id, goal.assist2_player_id):
                if pid is not None:
                    await players.bump_assist(pid)

    async def _ensure_players(self, players: PlayersRepo, plays: list[NhlPlay]) -> int:
        """Upsert scorer/assister snapshots. Returns API refresh count.

        Get-or-refresh: rows missing or older than ``PLAYER_REFRESH_DAYS``
        hit the player API, everything else is a local DB read. One bad
        player never fails the poll.
        """
        ids: set[int] = set()
        for play in plays:
            details = play.details
            if details is None:
                continue
            for pid in (
                details.scoring_player_id,
                details.assist1_player_id,
                details.assist2_player_id,
            ):
                if pid is not None:
                    ids.add(pid)
        cutoff = datetime.now(UTC) - timedelta(days=PLAYER_REFRESH_DAYS)
        refreshed = 0
        for pid in ids:
            row = await players.get_player(pid)
            if row is not None and self._is_fresh(row.updated_at, cutoff):
                continue
            try:
                landing = await self._nhl.get_player(pid)
            except Exception:
                logger.warning("player %d lookup failed, skipping", pid, exc_info=True)
                continue
            summary = NhlPlayer.model_validate(landing)
            if summary.player_id is None:
                continue
            await players.upsert_from_summary(summary)
            refreshed += 1
        return refreshed

    @staticmethod
    def _is_fresh(updated_at: datetime | None, cutoff: datetime) -> bool:
        """Whether a snapshot timestamp is newer than the cutoff."""
        if updated_at is None:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return updated_at >= cutoff

    async def _apply_live_state(
        self,
        games: GamesRepo,
        game_id: int,
        state: str | None,
        goals: list[NhlPlay],
    ) -> None:
        """Refresh a just-discovered row with live state/scores."""
        row = await games.get_game(game_id)
        if row is None:
            return
        self._apply_row_state(row, state, goals)

    @staticmethod
    def _apply_row_state(row: Game, state: str | None, goals: list[NhlPlay]) -> None:
        """Mutate a managed row in place (caller commits)."""
        if state:
            row.game_state = state
        if goals:
            details = goals[-1].details
            if details is not None:
                if details.away_score is not None:
                    row.away_team_score = details.away_score
                if details.home_score is not None:
                    row.home_team_score = details.home_score

    def _brightcove_video_url(self, clip_id: int) -> str | None:
        """Construct the Brightcove playback API URL for a clip.

        Built from ``BRIGHTCOVE_BASE_URL`` + ``BRIGHTCOVE_ACCOUNT_ID`` +
        the clip id, e.g.
        ``https://edge.api.brightcove.com/playback/v1/accounts/{account}/videos/{clip_id}``.

        The poster later fetches this URL (with the policy key header) to
        resolve the direct MP4 ``src`` from the video's renditions.
        Returns ``None`` when Brightcove isn't configured.
        """
        try:
            bc = self._nhl.brightcove
        except ValueError:
            logger.warning(
                "Brightcove not configured; cannot build video URL for clip %s",
                clip_id,
            )
            return None
        except AttributeError:
            logger.warning(
                "Brightcove not configured; cannot build video URL for clip %s",
                clip_id,
            )
            return None
        return f"{bc.base_url}/accounts/{bc.account_id}/videos/{clip_id}"

    def _side_id(self, game: NhlScheduleGame) -> int | None:
        """Numeric id of our side of the matchup."""
        for side in (game.home_team, game.away_team):
            if side is not None and (side.abbrev or "").upper() == self._team:
                return side.id
        return None

    def _build_game(self, game: NhlScheduleGame) -> Game:
        assert game.id is not None
        assert game.away_team and game.away_team.id is not None
        assert game.home_team and game.home_team.id is not None
        if game.start_time_utc:
            start = datetime.fromisoformat(game.start_time_utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = datetime.now(UTC)
        return Game(
            game_id=game.id,
            season=game.season or 0,
            game_type=game.game_type or 0,
            start_time=start,
            game_state=game.game_state,
            game_schedule_state=game.game_schedule_state,
            away_team_id=game.away_team.id,
            away_team_score=game.away_team.score,
            home_team_id=game.home_team.id,
            home_team_score=game.home_team.score,
        )

"""Game-day poll worker: DB-first live polling with minimal NHL API calls.

Cost per cycle:
- Game already in DB, not final: 1 call (play-by-play).
- Game final in DB: 0 calls.
- First poll of game day (DB miss): schedule + play-by-play + team
  lookups only for teams missing from the DB.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.game import Game
from models.nhl import NhlPlay, NhlScheduleGame, NhlScheduleTeam
from models.player import NhlPlayer
from repos.games_repo import GamesRepo
from repos.goals_repo import GoalsRepo
from repos.players_repo import PlayersRepo
from repos.posts_repo import PostsRepo
from repos.teams_repo import TeamsRepo
from services.nhl_service import NhlService

logger = logging.getLogger(__name__)

# Play-by-play states after which nothing more can happen. Polls for these
# games cost zero API calls.
TERMINAL_STATES = frozenset({"FINAL", "OFF"})

# Player snapshots refresh only when missing or older than this. Keeps the
# per-poll cost at local DB reads after the first sighting.
PLAYER_REFRESH_DAYS = 7


class PollWorker:
    """One poll cycle for the followed team.

    1. Look up today's game in the DB; only hit the schedule API on a miss.
    2. Fetch one play-by-play payload: game state + the team's goals.
    3. Upsert goals, scorer/assister snapshots, and unseen highlight clips
       as unposted ``Post`` rows for the poster.
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

    async def poll_once(self) -> str:
        """Run a single cycle, returning a human-readable summary."""
        if not self._team:
            return "skipped: NHL_TEAM_ABBREVIATION not set"

        async with self._sessions() as session:
            teams = TeamsRepo(session)
            games = GamesRepo(session)
            goals_repo = GoalsRepo(session)
            posts = PostsRepo(session)
            players = PlayersRepo(session)

            game_row = await games.get_game_by_date(self._nhl.today, self._team)
            if game_row is None:
                return await self._discover(
                    teams, games, goals_repo, posts, players, session
                )
            return await self._refresh(
                teams, games, goals_repo, posts, players, session, game_row
            )

    async def _discover(
        self,
        teams: TeamsRepo,
        games: GamesRepo,
        goals_repo: GoalsRepo,
        posts: PostsRepo,
        players: PlayersRepo,
        session: AsyncSession,
    ) -> str:
        """First poll of game day: schedule lookup, then full cycle."""
        game = await self._nhl.get_team_game_on_date(team_abbreviation=self._team)
        if game is None:
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

        state, goals = await self._nhl.get_live_goals(game.id, team_id)
        new_clips, players_refreshed = await self._store_goals(
            goals_repo, posts, players, game.id, goals
        )
        await self._apply_live_state(games, game.id, state, goals)
        await session.commit()

        away = game.away_team.abbrev if game.away_team else "?"
        home = game.home_team.abbrev if game.home_team else "?"
        summary = (
            f"{away} @ {home} ({state or game.game_state}): "
            f"{len(goals)} goals, {new_clips} new clips, "
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
        session: AsyncSession,
        row: Game,
    ) -> str:
        """Steady state: game known, one play-by-play fetch (or none)."""
        away = await teams.get_team(row.away_team_id)
        home = await teams.get_team(row.home_team_id)
        if away is None or home is None:
            # Partial write from an earlier failure — rediscover to heal.
            return await self._discover(
                teams, games, goals_repo, posts, players, session
            )

        if row.game_state in TERMINAL_STATES:
            return (
                f"{away.tri_code} @ {home.tri_code} ({row.game_state}): "
                "final, no check needed"
            )

        team_id = away.id if away.tri_code.upper() == self._team else home.id
        state, goals = await self._nhl.get_live_goals(row.game_id, team_id)
        new_clips, players_refreshed = await self._store_goals(
            goals_repo, posts, players, row.game_id, goals
        )
        self._apply_row_state(row, state, goals)
        await session.commit()

        summary = (
            f"{away.tri_code} @ {home.tri_code} ({state or row.game_state}): "
            f"{len(goals)} goals, {new_clips} new clips, "
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
    ) -> tuple[int, int]:
        """Upsert goals, players, and unseen clips.

        Returns ``(new clips, players refreshed from API)``.
        """
        clips: dict[int, tuple[int, str | None]] = {}
        for play in plays:
            goal = await goals_repo.upsert_from_play(game_id, play)
            if goal is not None and goal.highlight_clip_id is not None:
                highlight_url = (
                    play.details.highlight_clip_sharing_url if play.details else None
                )
                clips[goal.highlight_clip_id] = (goal.event_id, highlight_url)
        players_refreshed = await self._ensure_players(players, plays)
        new_clips = 0
        for clip_id, (event_id, highlight_url) in clips.items():
            if await posts.claim_pending(
                clip_id, game_id, self._team, event_id, highlight_url=highlight_url
            ):
                new_clips += 1
        return new_clips, players_refreshed

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

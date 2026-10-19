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
from db.models.goal import Goal
from models.enum import PostKind
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

# Inner width of the scoreboard box, in characters. Constant so every
# game's box is the same size: tri codes are always 3 chars, the clock
# is fixed at 5 (MM:SS), and scores are formatted into a 2-wide field
# (fine up to 99 — no NHL game has ever had a 100-goal team). The
# max() in _scoreboard_box is a safety valve that widens the box for
# pathological data rather than breaking alignment; it never triggers
# in practice.
SCOREBOARD_INNER_WIDTH = 17


def _period_label(period_number: int | None) -> str:
    """Period with ordinal suffix for 1/2/3, OT for 4+, empty if None.

    Only ever called for scoring plays, so period >= 4 means overtime
    (the shootout is period 5 but has no scoring events).
    """
    if period_number is None:
        return ""
    if period_number == 1:
        return "1st"
    if period_number == 2:
        return "2nd"
    if period_number == 3:
        return "3rd"
    if period_number >= 4:
        return "OT"
    return str(period_number)


def _scoreboard_box(
    *,
    period_number: int | None,
    time_remaining: str | None,
    home_tri: str | None,
    home_score: int | None,
    away_tri: str | None,
    away_score: int | None,
) -> list[str]:
    """Two-row scoreboard (clock over score) in a unicode box.

    Always ``SCOREBOARD_INNER_WIDTH`` inner characters wide (21 total
    with the borders), so consecutive goal posts render identically
    regardless of period label length or score digits. Tri codes are
    padded to 3 and scores right-aligned in a 2-wide field:

        ┌─────────────────┐
        │ 3rd  ·  04:32   │
        ├─────────────────┤
        │ CBJ:  4 │ NYR:  2 │
        └─────────────────┘
    """
    clock = time_remaining or "--:--"
    period_label = _period_label(period_number)
    if period_label:
        # Two spaces around the dot so the clock row's visible content
        # fills more of the box and doesn't look sparse next to the
        # score row.
        row_clock = f"{period_label}  ·  {clock}"
    else:
        row_clock = clock
    home_goals = home_score or 0
    away_goals = away_score or 0
    row_score = (
        f"{(home_tri or '?'):<3}: {home_goals:>2} │ "
        f"{(away_tri or '?'):<3}: {away_goals:>2}"
    )

    width = max(SCOREBOARD_INNER_WIDTH, len(row_clock), len(row_score))
    bar = "─" * (width + 2)
    return [
        f"┌{bar}┐",
        f"│ {row_clock.ljust(width)} │",
        f"├{bar}┤",
        f"│ {row_score.ljust(width)} │",
        f"└{bar}┘",
    ]


def build_highlight_post_text(
    *,
    team_abbrev: str,
    scorer: object | None,
    scorer_season_goals: int,
    assists: list[object | None],
    home_tri: str | None,
    home_score: int | None,
    away_tri: str | None,
    away_score: int | None,
    time_remaining: str | None,
    period_number: int | None,
) -> str:
    """Build the Bluesky post text for a goal.

    Format:

        #{TEAM} goal!
        🏒⚫ {Scorer Name} ({season goals})
        Assists: {Assist1 Name} ({season assists}), {Assist2 Name} ({season assists})
        ┌─────────────────┐
        │ 3rd · 04:32     │
        ├─────────────────┤
        │ CBJ: 4 │ NYR: 2 │
        └─────────────────┘

    The leading ``#{TEAM}`` is a real hashtag: the poster detects the
    prefix and attaches the tag facet to it, so the post opens with a
    tappable ``#CBJ goal!``. The assists line is omitted when there are
    no assisting players.
    """
    lines: list[str] = []

    scorer_name = (
        f"{scorer.first_name} {scorer.last_name}" if scorer is not None else "Unknown"
    )
    lines.append(f"#{team_abbrev} goal!")
    lines.append(f"🏒⚫ {scorer_name} ({scorer_season_goals})")

    assist_players = [a for a in assists if a is not None][:2]
    if assist_players:
        assist_parts = [
            f"{p.first_name} {p.last_name} ({p.assists or 0})" for p in assist_players
        ]
        lines.append(f"Assists: {', '.join(assist_parts)}")

    lines.extend(
        _scoreboard_box(
            period_number=period_number,
            time_remaining=time_remaining,
            home_tri=home_tri,
            home_score=home_score,
            away_tri=away_tri,
            away_score=away_score,
        )
    )

    return "\n".join(lines)


def build_highlight_video_caption() -> str:
    """Caption for the standalone highlight video post.

    Deliberately minimal: the goal details live on the (quoted) text
    post, so the video post only carries the camera emoji — the team
    hashtag is appended by the poster.
    """
    return "🎥 "


class PollWorker:
    """One poll cycle for the followed team.

    1. Look up today's game in the DB; only hit the schedule API on a miss.
    2. Fetch one play-by-play payload: game state + the team's goals.
    3. Upsert goals, scorer/assister snapshots, and unposted ``Post`` rows
       for the poster: a text post per goal (claimed immediately at
       detection) and a video post once the highlight clip id appears.
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

            game_row = await games.get_game_by_date(self._nhl.today, self._team)
            if game_row is None:
                return await self._discover(
                    teams, games, goals_repo, posts, players, session
                )
            return await self._refresh(
                teams, games, goals_repo, posts, players, session, game_row, force=force
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
        new_text_posts, new_video_posts, players_refreshed = await self._store_goals(
            goals_repo,
            posts,
            players,
            game.id,
            goals,
            home_tri=game.home_team.abbrev if game.home_team else None,
            away_tri=game.away_team.abbrev if game.away_team else None,
        )
        await self._apply_live_state(games, game.id, state, goals)
        await session.commit()

        away = game.away_team.abbrev if game.away_team else "?"
        home = game.home_team.abbrev if game.home_team else "?"
        summary = (
            f"{away} @ {home} ({state or game.game_state}): "
            f"{len(goals)} goals, {new_text_posts} new text posts, "
            f"{new_video_posts} new video posts, "
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
        *,
        force: bool = False,
    ) -> str:
        """Steady state: game known, one play-by-play fetch (or none)."""
        away = await teams.get_team(row.away_team_id)
        home = await teams.get_team(row.home_team_id)
        if away is None or home is None:
            # Partial write from an earlier failure — rediscover to heal.
            return await self._discover(
                teams, games, goals_repo, posts, players, session
            )

        if row.game_state in TERMINAL_STATES and not force:
            return (
                f"{away.tri_code} @ {home.tri_code} ({row.game_state}): "
                "final, no check needed"
            )

        team_id = away.id if away.tri_code.upper() == self._team else home.id
        state, goals = await self._nhl.get_live_goals(row.game_id, team_id)
        new_text_posts, new_video_posts, players_refreshed = await self._store_goals(
            goals_repo,
            posts,
            players,
            row.game_id,
            goals,
            home_tri=home.tri_code,
            away_tri=away.tri_code,
        )
        self._apply_row_state(row, state, goals)
        await session.commit()

        summary = (
            f"{away.tri_code} @ {home.tri_code} ({state or row.game_state}): "
            f"{len(goals)} goals, {new_text_posts} new text posts, "
            f"{new_video_posts} new video posts, "
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
    ) -> tuple[int, int, int]:
        """Upsert goals, players, and the pending Bluesky posts.

        A text post row is claimed for every goal on every poll — the
        (game, event, kind) unique key dedupes re-polls — so the goal
        text goes out as soon as the poster runs, whether or not the
        highlight clip exists yet. A video row is claimed only once the
        NHL attaches the clip id, which can be several minutes later.

        Both texts are built at detection time (when goal/assist numbers
        are accurate) and stored on the ``Post`` rows so the poster stays
        simple. The text row is claimed before the video row so, within
        one game, the goal text post always precedes its highlight video.

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
                players, goal, home_tri=home_tri, away_tri=away_tri
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
        caption = build_highlight_video_caption()
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

    async def _build_goal_text(
        self,
        players: PlayersRepo,
        goal,
        *,
        home_tri: str | None = None,
        away_tri: str | None = None,
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

        return build_highlight_post_text(
            team_abbrev=self._team,
            scorer=scorer,
            scorer_season_goals=scorer_season_goals,
            assists=assist_players,
            home_tri=home_tri,
            home_score=goal.home_score,
            away_tri=away_tri,
            away_score=goal.away_score,
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

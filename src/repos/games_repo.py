"""DynamoDB access for the games table (PK: game_id NUMBER)."""

from __future__ import annotations

import os

import boto3
from boto3.dynamodb.conditions import Attr

from utils.date import now


class GamesRepo:
    """Upsert + fetch for tracked NHL games."""

    def __init__(self, table_name: str | None = None) -> None:
        name = table_name or os.environ["GAMES_TABLE_NAME"]
        self.table = boto3.resource("dynamodb").Table(name)

    def upsert_game(
        self,
        game_id: int,
        date: str,
        team: str,
        game_state: str | None = None,
        raw: dict | None = None,
    ) -> None:
        self.table.put_item(
            Item={
                "game_id": game_id,
                "date": date,
                "team": team,
                "game_state": game_state,
                "raw": raw or {},
                "updated_at": now(),
            }
        )

    def get_game(self, game_id: int) -> dict | None:
        resp = self.table.get_item(Key={"game_id": game_id})
        return resp.get("Item")

    def get_game_by_date(self, date: str, team: str) -> dict | None:
        """Cached game row for a date, if already stored. Scan is fine at
        ~84 rows/season; add a GSI on date if this table ever grows large."""
        resp = self.table.scan(
            FilterExpression=Attr("date").eq(date) & Attr("team").eq(team),
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None

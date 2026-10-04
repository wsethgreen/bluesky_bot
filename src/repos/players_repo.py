"""DynamoDB access for the players table (PK: player_id NUMBER, TTL: ttl)."""

from __future__ import annotations

import os
import time

import boto3

from utils.date import now


class PlayersRepo:
    """Small read-through cache so we don't hit the NHL player API per poll."""

    def __init__(self, table_name: str | None = None) -> None:
        name = table_name or os.environ["PLAYERS_TABLE_NAME"]
        self.table = boto3.resource("dynamodb").Table(name)

    def get_player(self, player_id: int) -> dict | None:
        resp = self.table.get_item(Key={"player_id": player_id})
        return resp.get("Item")

    def cache_player(
        self,
        player_id: int,
        data: dict,
        ttl_days: int = 30,
    ) -> None:
        self.table.put_item(
            Item={
                "player_id": player_id,
                "data": data,
                "cached_at": now(),
                "ttl": int(time.time()) + ttl_days * 86400,
            }
        )

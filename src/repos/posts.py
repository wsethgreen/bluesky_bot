"""DynamoDB access for the posts table (PK: clip_id NUMBER)."""

from __future__ import annotations

import os

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from utils.date import now


class PostsRepo:
    """Pending/posted highlight clips. Conditional writes dedupe re-polls."""

    def __init__(self, table_name: str | None = None) -> None:
        name = table_name or os.environ["POSTS_TABLE_NAME"]
        self.table = boto3.resource("dynamodb").Table(name)

    def claim_pending(
        self,
        clip_id: int,
        game_id: int,
        team: str,
        event_id: int | None = None,
    ) -> bool:
        """Insert as pending if new. Returns True when inserted, False if seen."""
        try:
            self.table.put_item(
                Item={
                    "clip_id": clip_id,
                    "game_id": game_id,
                    "event_id": event_id,
                    "team": team,
                    "status": "pending",
                    "created_at": now(),
                },
                ConditionExpression="attribute_not_exists(clip_id)",
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_post(self, clip_id: int) -> dict | None:
        resp = self.table.get_item(Key={"clip_id": clip_id})
        return resp.get("Item")

    def list_pending(self, limit: int = 25) -> list[dict]:
        resp = self.table.scan(
            FilterExpression=Attr("status").eq("pending"),
            Limit=limit,
        )
        return resp.get("Items", [])

    def mark_posted(self, clip_id: int, bsky_uri: str) -> None:
        self.table.update_item(
            Key={"clip_id": clip_id},
            UpdateExpression="SET #s = :s, bsky_uri = :u, posted_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "posted",
                ":u": bsky_uri,
                ":t": now(),
            },
        )

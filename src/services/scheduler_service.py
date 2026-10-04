"""Manage EventBridge Scheduler schedules for game-day polling."""

from __future__ import annotations

import json
import logging

import boto3

logger = logging.getLogger(__name__)


class SchedulerService:
    """Thin wrapper around the EventBridge Scheduler API."""

    def __init__(self, client=None) -> None:
        self.client = client or boto3.client("scheduler")

    def delete_schedule(self, name: str) -> None:
        try:
            self.client.delete_schedule(Name=name)
            logger.info("deleted schedule %s", name)
        except self.client.exceptions.ResourceNotFoundException:
            pass

    def upsert_schedule(
        self,
        *,
        name: str,
        cron: str,
        start,
        end,
        target_arn: str,
        role_arn: str,
        payload: dict,
    ) -> None:
        kwargs = {
            "Name": name,
            "ScheduleExpression": cron,
            "ScheduleExpressionTimezone": "UTC",
            "StartDate": start,
            "EndDate": end,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": target_arn,
                "RoleArn": role_arn,
                "Input": json.dumps(payload),
            },
        }
        try:
            self.client.get_schedule(Name=name)
        except self.client.exceptions.ResourceNotFoundException:
            self.client.create_schedule(**kwargs)
            logger.info("created schedule %s (%s)", name, cron)
        else:
            self.client.update_schedule(**kwargs)
            logger.info("updated schedule %s (%s)", name, cron)

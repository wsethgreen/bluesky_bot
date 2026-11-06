"""Tests for BlueskyClient's constructor: credentials are injected.

The client has no settings or account knowledge — whatever handle and
password it is built with are exactly the ones it logs in with, so the
caller (post_pending) owns pulling the right account from Settings.
"""

import asyncio

from clients.bluesky_client import BlueskyClient


class _RecordingLogin:
    """Stand-in for atproto's AsyncClient that records login calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def login(self, username: str, password: str) -> None:
        self.calls.append((username, password))


class TestCredentialInjection:
    def test_handle_and_password_are_kept(self) -> None:
        client = BlueskyClient(
            account_handle="cavs-bot.bsky.social", account_password="pw"
        )
        assert client._username == "cavs-bot.bsky.social"
        assert client._password == "pw"

    def test_login_uses_the_injected_credentials(self) -> None:
        client = BlueskyClient(
            account_handle="cavs-bot.bsky.social", account_password="pw"
        )
        stub = _RecordingLogin()
        client.client = stub

        asyncio.run(client._login())

        assert stub.calls == [("cavs-bot.bsky.social", "pw")]

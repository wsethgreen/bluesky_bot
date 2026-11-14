"""Tests for BlueskyClient: credentials are injected, and rich text
building facets every hashtag.

The client has no settings or account knowledge — whatever handle and
password it is built with are exactly the ones it logs in with, so the
caller (post_pending) owns pulling the right account from Settings.
"""

import asyncio

from clients.bluesky_client import BlueskyClient


def facet_pairs(builder) -> list[tuple[str, str]]:
    """(displayed text, tag) of each facet, decoded from byte offsets."""
    text = builder.build_text().encode()
    return [
        (
            text[f.index.byte_start : f.index.byte_end].decode(),
            f.features[0].tag,
        )
        for f in builder.build_facets()
    ]


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


def build_client() -> BlueskyClient:
    return BlueskyClient(account_handle="cavs-bot.bsky.social", account_password="pw")


class TestTextBuilding:
    def test_video_caption_tag_is_faceted(self) -> None:
        # The NHL video caption carries its own team hashtag in the
        # text — the builder facets it, never invents one.
        builder = build_client()._build_text("🎥 #CBJ")

        assert builder.build_text() == "🎥 #CBJ"
        assert facet_pairs(builder) == [("#CBJ", "CBJ")]

    def test_facets_every_hashtag_not_just_the_first(self) -> None:
        # Quarter-end posts carry #LetEmKnow on the header line: it
        # still needs its own facet or it renders as inert text.
        text = "End of the 1st quarter. #LetEmKnow\n\nCLE: 119  |  PHI: 95"
        builder = build_client()._build_text(text)

        assert builder.build_text() == text
        assert facet_pairs(builder) == [("#LetEmKnow", "LetEmKnow")]

    def test_facets_each_tag_in_a_multi_hashtag_post(self) -> None:
        # Team tag in the body plus the campaign tag — both tappable.
        text = "#CBJ power play goal!\n\nScoreboard\n\n#LetEmKnow"
        builder = build_client()._build_text(text)

        assert builder.build_text() == text
        assert facet_pairs(builder) == [
            ("#CBJ", "CBJ"),
            ("#LetEmKnow", "LetEmKnow"),
        ]

    def test_plain_text_stays_plain(self) -> None:
        builder = build_client()._build_text("no tags here")

        assert builder.build_text() == "no tags here"
        assert builder.build_facets() == []

    def test_builders_do_not_leak_state_across_calls(self) -> None:
        # atproto's TextBuilder mutates in place, so each call starts
        # from a clean self.text_builder — otherwise the previous
        # post's text and facets would ride into the next one.
        client = build_client()
        first = client._build_text("#CBJ goal!")
        second = client._build_text("🎥 #CBJ")

        assert first.build_text() == "#CBJ goal!"
        assert second.build_text() == "🎥 #CBJ"
        assert facet_pairs(second) == [("#CBJ", "CBJ")]
        # The class property is the builder each post used.
        assert client.text_builder is second

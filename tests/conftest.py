"""Test-environment defaults.

The route tests drive the real app (``from app import app``), and
importing it builds ``Settings`` immediately — ``db/engine`` parses
the database URL the moment the module is imported. CI (and a fresh
clone) has no ``.env`` file, so every settings key that has no
default needs a value before any test module is collected.

``setdefault`` keeps a real exported environment's values; the suite
never asserts on configuration, so the inert dummies are safe.
"""

import os

os.environ.setdefault("NHL_WEB_API_BASE_URL", "https://example.test/nhl-web")
os.environ.setdefault("NHL_STATS_API_BASE_URL", "https://example.test/nhl-stats")
os.environ.setdefault("NHL_TEAM_ABBREVIATION", "CBJ")
os.environ.setdefault("CBJ_BLUESKY_HANDLE", "test.bsky.social")
os.environ.setdefault("CAVS_BLUESKY_HANDLE", "test.bsky.social")
os.environ.setdefault("BRIGHTCOVE_ACCOUNT_ID", "test-account")
os.environ.setdefault("BRIGHTCOVE_BASE_URL", "https://example.test/brightcove")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("DB_HOST", "localhost")

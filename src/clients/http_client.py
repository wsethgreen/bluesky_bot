"""Shared async HTTP client used by API clients and services."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Self

import httpx2

logger = logging.getLogger(__name__)

# GET is idempotent, so these are safe to retry.
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# TransportError is the base of TimeoutException, NetworkError, and
# ProtocolError (e.g. RemoteProtocolError). Non-transport errors such as
# InvalidURL, TooManyRedirects, or HTTPStatusError are not retried here.
RETRYABLE_EXCEPTIONS = (httpx2.TransportError,)


class HttpClient:
    """Thin retrying wrapper around :class:`httpx2.AsyncClient`.

    Create one instance at startup and inject it into API clients/services:

        client = HttpClient(base_url="https://api.example.com")
        nhl = NhlClient(http=client)
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        follow_redirects: bool = True,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if backoff_factor < 0:
            raise ValueError("backoff_factor must be >= 0")

        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        default_headers = {
            "Accept": "application/json",
            "User-Agent": "bluesky-bot/0.1.0",
        }
        if headers:
            default_headers.update(headers)

        self.client = client or httpx2.AsyncClient(
            base_url=base_url,
            headers=default_headers,
            timeout=httpx2.Timeout(timeout),
            follow_redirects=follow_redirects,
        )

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx2.Timeout | None = None,
        max_retries: int | None = None,
        raise_for_status: bool = True,
        follow_redirects: bool | None = None,
    ) -> httpx2.Response:
        """Send a GET request, retrying transient failures.

        Args:
            url: Full URL or path (resolved against ``base_url`` if set).
            params: Optional query parameters appended to the URL.
            headers: Per-request headers merged over client defaults.
            timeout: Per-request timeout override in seconds.
            max_retries: Per-request retry override. ``0`` disables retries.
            raise_for_status: Call ``raise_for_status()`` on the response.
            follow_redirects: Per-request redirect override.

        Returns:
            The successful :class:`httpx2.Response`.

        Raises:
            httpx2.HTTPError: On non-retryable errors, or when retries
                are exhausted (last exception/status re-raised).
        """
        retries = self.max_retries if max_retries is None else max_retries
        if retries < 0:
            raise ValueError("max_retries must be >= 0")

        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if follow_redirects is not None:
            kwargs["follow_redirects"] = follow_redirects

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = await self.client.get(
                    url, params=params, headers=headers, **kwargs
                )

                if response.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                    delay = self._backoff_delay(attempt, response)
                    logger.warning(
                        "GET %s -> %s, retrying in %.2fs (attempt %d/%d)",
                        url,
                        response.status_code,
                        delay,
                        attempt + 1,
                        retries + 1,
                    )
                    await asyncio.sleep(delay)
                    continue

                if raise_for_status:
                    response.raise_for_status()
                return response

            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt >= retries:
                    logger.error(
                        "GET %s failed after %d attempts: %s", url, attempt + 1, exc
                    )
                    raise
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "GET %s failed (%s), retrying in %.2fs (attempt %d/%d)",
                    url,
                    exc.__class__.__name__,
                    delay,
                    attempt + 1,
                    retries + 1,
                )
                await asyncio.sleep(delay)

        # Unreachable in normal flow; keeps type-checkers happy if the
        # loop above ever exits without returning/raising.
        if last_exc is not None:
            raise last_exc
        raise httpx2.TransportError("GET request failed without response")

    def _backoff_delay(
        self, attempt: int, response: httpx2.Response | None = None
    ) -> float:
        """Exponential backoff with jitter, honoring ``Retry-After`` when present."""
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after is not None:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass  # fall through to exponential backoff
        base = self.backoff_factor * (2**attempt)
        return base + random.uniform(0, 0.1)

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self.client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

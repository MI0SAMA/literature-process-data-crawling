"""Polite document fetcher for open-access full text and supplementary files."""

from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass

from app.acquisition.models import FetchedResource
from app.http import HttpResponse, RateLimiter, RetryPolicy, Transport
from app.models import utc_now_iso


class DocumentFetcher:
    def __init__(
        self,
        transport: Transport,
        *,
        timeout: float = 45.0,
        max_bytes: int = 40 * 1024 * 1024,
        user_agent: str = "AlScN-PVD-Literature/0.3 (open-access full-text acquisition)",
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        sleep=time.sleep,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=8.0)
        self.rate_limiter = rate_limiter
        self._sleep = sleep

    def fetch(self, url: str, *, accept: str = "text/html,application/xhtml+xml,application/pdf,application/xml;q=0.9,*/*;q=0.5") -> FetchedResource:
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        last_error: FetchedResource | None = None
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            if self.rate_limiter:
                self.rate_limiter.wait()
            try:
                response = self.transport.request("GET", url, headers, self.timeout, None)
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as error:
                last_error = FetchedResource(
                    url=url,
                    final_url=url,
                    status=0,
                    content_type="",
                    body=b"",
                    fetched_at=utc_now_iso(),
                    access_status="error",
                    error_kind="timeout" if isinstance(error, TimeoutError) else "network",
                    error_message=str(error)[:500],
                    attempts=attempt,
                    retry_count=attempt - 1,
                )
                if attempt < self.retry_policy.max_attempts:
                    self._sleep(self.retry_policy.delay(attempt))
                    continue
                return last_error

            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if response.status == 200:
                if len(response.body) > self.max_bytes:
                    return FetchedResource(
                        url=url,
                        final_url=response.url,
                        status=response.status,
                        content_type=content_type,
                        body=b"",
                        headers=response.headers,
                        fetched_at=utc_now_iso(),
                        access_status="too_large",
                        error_kind="too_large",
                        error_message=f"document exceeds {self.max_bytes} bytes",
                        attempts=attempt,
                        retry_count=attempt - 1,
                    )
                return FetchedResource(
                    url=url,
                    final_url=response.url,
                    status=response.status,
                    content_type=content_type,
                    body=response.body,
                    headers=response.headers,
                    fetched_at=utc_now_iso(),
                    access_status="open_access",
                    attempts=attempt,
                    retry_count=attempt - 1,
                )

            retryable = response.status in self.retry_policy.retry_statuses
            last_error = FetchedResource(
                url=url,
                final_url=response.url,
                status=response.status,
                content_type=content_type,
                body=b"",
                headers=response.headers,
                fetched_at=utc_now_iso(),
                access_status=_access_status(response.status),
                error_kind="rate_limited" if response.status == 429 else _error_kind(response.status),
                error_message=f"HTTP {response.status}",
                attempts=attempt,
                retry_count=attempt - 1,
            )
            if retryable and attempt < self.retry_policy.max_attempts:
                self._sleep(self.retry_policy.delay(attempt, _retry_after(response.headers)))
                continue
            return last_error

        if last_error is not None:
            return last_error
        return FetchedResource(
            url=url,
            final_url=url,
            status=0,
            content_type="",
            body=b"",
            fetched_at=utc_now_iso(),
            access_status="error",
            error_kind="unknown",
            error_message="fetch failed without a response",
        )


def _access_status(status: int) -> str:
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 402:
        return "payment_required"
    if status == 429:
        return "rate_limited"
    return "error"


def _error_kind(status: int) -> str:
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 402:
        return "payment_required"
    return "http_error"


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

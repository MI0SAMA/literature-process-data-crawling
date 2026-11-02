"""HTTP transport, polite rate limiting, retries and JSON response caching."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

from app.models import utc_now_iso


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


@dataclass
class CachedResponse:
    status: int
    body: bytes
    fetched_at: str
    expires_at: str | None
    url: str = ""


class ResponseCache(Protocol):
    def get(self, key: str) -> CachedResponse | None: ...

    def put(
        self,
        key: str,
        *,
        source: str,
        url: str,
        status: int,
        body: bytes,
        fetched_at: str,
        expires_at: str | None,
    ) -> None: ...


class MemoryResponseCache:
    def __init__(self) -> None:
        self._data: dict[str, CachedResponse] = {}

    def get(self, key: str) -> CachedResponse | None:
        return self._data.get(key)

    def put(
        self,
        key: str,
        *,
        source: str,
        url: str,
        status: int,
        body: bytes,
        fetched_at: str,
        expires_at: str | None,
    ) -> None:
        self._data[key] = CachedResponse(status=status, body=body, fetched_at=fetched_at, expires_at=expires_at, url=url)


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse: ...


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str,
        status: int | None = None,
        url: str = "",
        attempts: int = 1,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.url = redact_url(url)
        self.attempts = attempts
        self.retryable = retryable

    def __str__(self) -> str:
        prefix = f"{self.kind}"
        if self.status is not None:
            prefix += f" {self.status}"
        return f"{prefix}: {self.args[0]} ({self.url})"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "status": self.status,
            "url": self.url,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "message": str(self.args[0]),
        }


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            # Honor a server-provided Retry-After even if it exceeds our
            # normal backoff cap; the server knows its own budget.
            return max(0.0, retry_after)
        delay = self.base_delay_seconds * (2 ** max(0, attempt - 1))
        return min(delay, self.max_delay_seconds)


class RateLimiter:
    def __init__(self, interval_seconds: float, *, clock=time.monotonic, sleep=time.sleep) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last = 0.0
        self._initialized = False

    def wait(self) -> None:
        if self.interval_seconds <= 0:
            return
        now = self._clock()
        if self._initialized:
            remaining = self.interval_seconds - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now
        self._initialized = True


@dataclass
class JsonResponse:
    data: dict
    status: int
    url: str
    cache_hit: bool
    stale: bool
    attempts: int
    retries: int


class UrllibTransport:
    """Small stdlib transport. It returns HTTP error responses to the caller."""

    def __init__(self, *, proxy: str | None = None) -> None:
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=response.read(),
                    url=response.geturl(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers={key.lower(): value for key, value in (error.headers or {}).items()},
                body=error.read(),
                url=url,
            )


class RequestsTransport:
    """requests-based transport with explicit HTTP(S) proxy support.

    ``requests`` handles Basic proxy authentication for HTTPS CONNECT tunnels
    correctly on Windows, unlike the stdlib/curl Schannel path in this
    environment. Proxy credentials must come from an environment variable
    (``ALSCN_PROXY``) or an explicit CLI argument; they are never logged.
    """

    def __init__(self, *, proxy: str | None = None) -> None:
        import requests

        self._requests = requests
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        body: bytes | None = None,
    ) -> HttpResponse:
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=timeout,
                data=body,
                allow_redirects=True,
            )
        except self._requests.exceptions.Timeout as error:
            raise TimeoutError(str(error)) from error
        except self._requests.exceptions.RequestException as error:
            raise ConnectionError(str(error)) from error
        return HttpResponse(
            status=response.status_code,
            headers={key.lower(): value for key, value in response.headers.items()},
            body=response.content,
            url=response.url,
        )


class CachedHttpClient:
    def __init__(
        self,
        transport: Transport,
        *,
        cache: ResponseCache | None = None,
        retry_policy: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout: float = 20.0,
        user_agent: str = "AlScN-PVD-Literature/0.2",
        cache_ttl_seconds: float = 168 * 3600,
        offline: bool = False,
        sleep=time.sleep,
        clock=time.time,
    ) -> None:
        self.transport = transport
        self.cache = cache
        self.retry_policy = retry_policy or RetryPolicy()
        self.rate_limiter = rate_limiter
        self.timeout = timeout
        self.user_agent = user_agent
        self.cache_ttl_seconds = cache_ttl_seconds
        self.offline = offline
        self._sleep = sleep
        self._clock = clock

    def get_json(
        self,
        url: str,
        *,
        source: str,
        ttl_seconds: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonResponse:
        key = cache_key(source, url)
        cached = self.cache.get(key) if self.cache else None
        now_iso = self._now_iso()
        if cached is not None:
            fresh = _is_fresh(cached.expires_at, now_iso)
            if fresh or self.offline:
                return JsonResponse(
                    data=_parse_json(cached.body, url),
                    status=cached.status,
                    url=url,
                    cache_hit=True,
                    stale=not fresh,
                    attempts=0,
                    retries=0,
                )
        if self.offline:
            raise ApiError(
                "no cached response for offline run",
                kind="cache_miss",
                url=url,
                retryable=False,
            )

        body, attempts, retries = self._request_with_retry("GET", url, headers or {}, None)
        data = _parse_json(body, url)
        ttl = self.cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        if self.cache:
            fetched_at = self._now_iso()
            expires_at = _iso_after(self._clock(), ttl)
            self.cache.put(
                key,
                source=source,
                url=redact_url(url),
                status=200,
                body=body,
                fetched_at=fetched_at,
                expires_at=expires_at,
            )
        return JsonResponse(data=data, status=200, url=url, cache_hit=False, stale=False, attempts=attempts, retries=retries)

    def post_json(
        self,
        url: str,
        payload: dict,
        *,
        source: str,
        headers: dict[str, str] | None = None,
        ttl_seconds: float | None = None,
    ) -> JsonResponse:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        body, attempts, retries = self._request_with_retry(
            "POST", url, request_headers, json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        return JsonResponse(
            data=_parse_json(body, url),
            status=200,
            url=url,
            cache_hit=False,
            stale=False,
            attempts=attempts,
            retries=retries,
        )

    def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[bytes, int, int]:
        merged_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        merged_headers.update(headers)
        last_error: ApiError | None = None
        retries = 0
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            if self.rate_limiter:
                self.rate_limiter.wait()
            try:
                response = self.transport.request(method, url, merged_headers, self.timeout, body)
            except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as error:
                last_error = ApiError(
                    str(error),
                    kind="timeout" if isinstance(error, TimeoutError) else "network",
                    url=url,
                    attempts=attempt,
                    retryable=True,
                )
                if attempt < self.retry_policy.max_attempts:
                    retries += 1
                    self._sleep(self.retry_policy.delay(attempt))
                    continue
                raise last_error from error

            if 200 <= response.status < 300:
                return response.body, attempt, retries

            retryable = response.status in self.retry_policy.retry_statuses
            last_error = ApiError(
                _error_message(response.body),
                kind="rate_limited" if response.status == 429 else "http_error",
                status=response.status,
                url=url,
                attempts=attempt,
                retryable=retryable,
            )
            if retryable and attempt < self.retry_policy.max_attempts:
                retries += 1
                self._sleep(self.retry_policy.delay(attempt, _retry_after_seconds(response.headers)))
                continue
            raise last_error

        if last_error is not None:
            raise last_error
        raise ApiError("request failed without an error", kind="unknown", url=url)

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).replace(microsecond=0).isoformat()


def cache_key(source: str, url: str) -> str:
    import hashlib

    return hashlib.sha256(f"{source}\n{url}".encode("utf-8")).hexdigest()


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = [
        (key, "***" if key.lower() in {"api_key", "apikey", "key", "token"} else value)
        for key, value in query
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_query, safe="*"), parsed.fragment)
    )


def _parse_json(body: bytes, url: str) -> dict:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiError(
            f"response is not valid JSON: {error}",
            kind="parse_error",
            url=url,
            retryable=False,
        ) from error
    if not isinstance(data, dict):
        raise ApiError("JSON response is not an object", kind="parse_error", url=url)
    return data


def _error_message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return "empty error response"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("error", "message", "detail"):
                if key in data:
                    return str(data[key])[:500]
    except json.JSONDecodeError:
        pass
    return re.sub(r"\s+", " ", text)[:500]


def _retry_after_seconds(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


def _is_fresh(expires_at: str | None, now_iso: str) -> bool:
    if not expires_at:
        return False
    return expires_at > now_iso


def _iso_after(now_epoch: float, ttl_seconds: float) -> str:
    return datetime.fromtimestamp(now_epoch + ttl_seconds, timezone.utc).replace(microsecond=0).isoformat()


def utc_now() -> str:
    return utc_now_iso()

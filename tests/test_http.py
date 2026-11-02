"""Tests for HTTP retry, rate limiting and response caching."""

from __future__ import annotations

import json
import unittest

from app.http import (
    ApiError,
    CachedHttpClient,
    HttpResponse,
    MemoryResponseCache,
    RetryPolicy,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, timeout, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(status=200, payload=None, headers=None, url="https://example.test"):
    body = json.dumps(payload if payload is not None else {"ok": True}).encode("utf-8")
    return HttpResponse(status=status, headers=headers or {}, body=body, url=url)


def client(transport, **kwargs):
    delays = []
    kwargs.setdefault("retry_policy", RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0))
    kwargs.setdefault("sleep", delays.append)
    kwargs.setdefault("cache", MemoryResponseCache())
    return CachedHttpClient(transport, **kwargs), delays


class HttpTests(unittest.TestCase):
    def test_retry_after_header_is_honored(self):
        transport = FakeTransport([response(429, {"error": "slow down"}, {"Retry-After": "2"}), response(200, {"ok": 1})])
        http, delays = client(transport)
        result = http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(result.data, {"ok": 1})
        self.assertEqual(result.attempts, 2)
        self.assertEqual(delays, [2.0])

    def test_500_is_retried(self):
        transport = FakeTransport([response(500), response(200, {"ok": 2})])
        http, _ = client(transport)
        result = http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(result.data, {"ok": 2})
        self.assertEqual(len(transport.calls), 2)

    def test_403_is_not_retried(self):
        transport = FakeTransport([response(403, {"error": "forbidden"})])
        http, _ = client(transport)
        with self.assertRaises(ApiError) as context:
            http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(context.exception.kind, "http_error")
        self.assertEqual(context.exception.status, 403)
        self.assertFalse(context.exception.retryable)
        self.assertEqual(len(transport.calls), 1)

    def test_timeout_is_retried_then_reported(self):
        transport = FakeTransport([TimeoutError("timed out"), TimeoutError("timed out")])
        http, _ = client(transport, retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, max_delay_seconds=0))
        with self.assertRaises(ApiError) as context:
            http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(context.exception.kind, "timeout")
        self.assertEqual(context.exception.attempts, 2)

    def test_invalid_json_is_parse_error(self):
        transport = FakeTransport([HttpResponse(200, {}, b"not json", "https://api.test/works")])
        http, _ = client(transport)
        with self.assertRaises(ApiError) as context:
            http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(context.exception.kind, "parse_error")

    def test_fresh_cache_avoids_second_request(self):
        transport = FakeTransport([response(200, {"ok": 3})])
        http, _ = client(transport)
        first = http.get_json("https://api.test/works", source="openalex")
        second = http.get_json("https://api.test/works", source="openalex")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.data, {"ok": 3})
        self.assertEqual(len(transport.calls), 1)

    def test_offline_cache_hit_returns_stale_data(self):
        transport = FakeTransport([response(200, {"ok": 4})])
        online, _ = client(transport, cache_ttl_seconds=-1)
        online.get_json("https://api.test/works", source="openalex")
        offline, _ = client(FakeTransport([]), offline=True, cache=online.cache)
        result = offline.get_json("https://api.test/works", source="openalex")
        self.assertTrue(result.cache_hit)
        self.assertTrue(result.stale)
        self.assertEqual(result.data, {"ok": 4})

    def test_offline_cache_miss_is_clear_error(self):
        http, _ = client(FakeTransport([]), offline=True)
        with self.assertRaises(ApiError) as context:
            http.get_json("https://api.test/works", source="openalex")
        self.assertEqual(context.exception.kind, "cache_miss")

    def test_api_key_is_redacted_in_errors(self):
        transport = FakeTransport([response(403, {"error": "forbidden"})])
        http, _ = client(transport)
        with self.assertRaises(ApiError) as context:
            http.get_json("https://api.test/works?api_key=super-secret", source="openalex")
        self.assertNotIn("super-secret", str(context.exception))
        self.assertIn("api_key=***", str(context.exception))


if __name__ == "__main__":
    unittest.main()

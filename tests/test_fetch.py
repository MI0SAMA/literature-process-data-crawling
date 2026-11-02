"""Tests for the Phase 3 polite document fetcher."""

from __future__ import annotations

import unittest

from app.acquisition.fetch import DocumentFetcher
from app.http import HttpResponse, RetryPolicy


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, timeout, body=None):
        self.calls.append((method, url))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(status=200, body=b"<html>full text</html>", content_type="text/html", headers=None, url="https://example.org"):
    return HttpResponse(status=status, headers=headers or {"content-type": content_type}, body=body, url=url)


class DocumentFetcherTests(unittest.TestCase):
    def fetcher(self, transport, **kwargs):
        delays = []
        fetcher = DocumentFetcher(
            transport,
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, max_delay_seconds=0),
            sleep=delays.append,
            **kwargs,
        )
        return fetcher, delays

    def test_200_returns_open_access(self):
        transport = FakeTransport([response(200, b"<html>ok</html>")])
        fetcher, _ = self.fetcher(transport)
        result = fetcher.fetch("https://example.org")
        self.assertTrue(result.ok)
        self.assertEqual(result.access_status, "open_access")
        self.assertEqual(result.body, b"<html>ok</html>")

    def test_403_is_forbidden_and_not_retried(self):
        transport = FakeTransport([response(403, b"", "text/html")])
        fetcher, _ = self.fetcher(transport)
        result = fetcher.fetch("https://example.org")
        self.assertFalse(result.ok)
        self.assertEqual(result.access_status, "forbidden")
        self.assertEqual(result.error_kind, "forbidden")
        self.assertEqual(len(transport.calls), 1)

    def test_429_is_retried(self):
        transport = FakeTransport([response(429, b"", "text/html", {"retry-after": "0"}), response(200, b"ok")])
        fetcher, _ = self.fetcher(transport)
        result = fetcher.fetch("https://example.org")
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(transport.calls), 2)

    def test_max_bytes_is_enforced(self):
        transport = FakeTransport([response(200, b"x" * 100)])
        fetcher, _ = self.fetcher(transport, max_bytes=10)
        result = fetcher.fetch("https://example.org")
        self.assertFalse(result.ok)
        self.assertEqual(result.access_status, "too_large")

    def test_network_error_is_reported(self):
        transport = FakeTransport([TimeoutError("timeout"), TimeoutError("timeout")])
        fetcher, _ = self.fetcher(transport)
        result = fetcher.fetch("https://example.org")
        self.assertEqual(result.error_kind, "timeout")
        self.assertEqual(result.status, 0)


if __name__ == "__main__":
    unittest.main()

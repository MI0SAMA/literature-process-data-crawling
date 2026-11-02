"""Tests for the optional AI title/abstract classifier."""

from __future__ import annotations

import os
import unittest

from app.config import LLMConfig
from app.discovery.llm import LLMClassifier, build_prompt
from app.http import JsonResponse
from tests.helpers import make_record


class FakeHttp:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    def post_json(self, url, payload, *, source, headers=None):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return JsonResponse(
            data={"choices": [{"message": {"content": self.content}}]},
            status=200,
            url=url,
            cache_hit=False,
            stale=False,
            attempts=1,
            retries=0,
        )


def paper():
    return make_record(
        title="AlScN thin films by reactive sputtering",
        abstract="AlScN films were grown by reactive magnetron sputtering.",
    )


class LLMClassifierTests(unittest.TestCase):
    def setUp(self):
        os.environ["ALSCN_TEST_KEY"] = "secret"
        self.config = LLMConfig(
            base_url="https://llm.test/v1",
            model="test-model",
            api_key_env="ALSCN_TEST_KEY",
        )

    def tearDown(self):
        os.environ.pop("ALSCN_TEST_KEY", None)

    def test_classifies_json_response(self):
        http = FakeHttp('{"label": "likely_AlScN_PVD", "confidence": 0.88, "reason": "sputtered AlScN"}')
        classifier = LLMClassifier(http, self.config, api_key="secret")
        result = classifier.classify(paper())
        self.assertEqual(result.label, "likely_AlScN_PVD")
        self.assertEqual(result.confidence, 0.88)
        self.assertEqual(http.calls[0]["url"], "https://llm.test/v1/chat/completions")
        user_content = http.calls[0]["payload"]["messages"][1]["content"]
        self.assertIn("AlScN thin films", user_content)
        self.assertEqual(http.calls[0]["headers"]["Authorization"], "Bearer secret")

    def test_fenced_json_is_accepted(self):
        http = FakeHttp('```json\n{"label": "unclear", "confidence": 0.4, "reason": "no method"}\n```')
        classifier = LLMClassifier(http, self.config, api_key="secret")
        self.assertEqual(classifier.classify(paper()).label, "unclear")

    def test_invalid_label_raises(self):
        http = FakeHttp('{"label": "maybe", "confidence": 0.5}')
        classifier = LLMClassifier(http, self.config, api_key="secret")
        with self.assertRaises(ValueError):
            classifier.classify(paper())

    def test_missing_configuration_raises(self):
        with self.assertRaises(ValueError):
            LLMClassifier(FakeHttp("{}"), LLMConfig(), api_key="secret")

    def test_prompt_does_not_include_internal_data(self):
        prompt = build_prompt(paper())
        self.assertIn("AlScN", prompt)
        self.assertNotIn("147", prompt)
        self.assertNotIn("internal", prompt.lower())


if __name__ == "__main__":
    unittest.main()

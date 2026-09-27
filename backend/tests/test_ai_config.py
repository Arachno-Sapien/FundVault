import json
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.ledger.receipt_extractor import AIConfig, parse_ai_config

CONFIG = json.dumps({
    "primary": {
        "provider": "openai_compatible",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-nano-12b-v2-vl",
        "api_key": "nvapi-secret",
    },
    "fallback": {"provider": "gemini", "model": "gemini-3.6-flash", "api_key": "gem-secret"},
})


class ParseTests(SimpleTestCase):
    def test_parses_both_providers(self):
        parsed = parse_ai_config(CONFIG)
        self.assertIsInstance(parsed["primary"], AIConfig)
        self.assertEqual(parsed["primary"].model, "nvidia/nemotron-nano-12b-v2-vl")
        self.assertEqual(parsed["fallback"].provider, "gemini")

    def test_blank_config_yields_no_providers(self):
        parsed = parse_ai_config("")
        self.assertIsNone(parsed["primary"])
        self.assertIsNone(parsed["fallback"])

    def test_fallback_is_optional(self):
        only_primary = json.dumps({
            "primary": {
                "provider": "openai_compatible",
                "base_url": "https://x/v1",
                "model": "m",
                "api_key": "k",
            }
        })
        self.assertIsNone(parse_ai_config(only_primary)["fallback"])

    def test_unknown_provider_is_dropped(self):
        bogus = json.dumps({"primary": {"provider": "mystery", "model": "m", "api_key": "k"}})
        self.assertIsNone(parse_ai_config(bogus)["primary"])

    def test_openai_compatible_without_base_url_is_dropped(self):
        bad = json.dumps({
            "primary": {"provider": "openai_compatible", "model": "m", "api_key": "k"}
        })
        self.assertIsNone(parse_ai_config(bad)["primary"])


class ExtractionContractTests(SimpleTestCase):
    def test_no_configured_provider_returns_an_error_not_an_exception(self):
        from apps.ledger.receipt_extractor import extract_from_receipt_image

        result = extract_from_receipt_image(b"", {"primary": None, "fallback": None})
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"].lower())


class SecretRedactionTests(SimpleTestCase):
    """API keys must never leak into an error message that could reach the client."""

    SECRET = "nvapi-supersecretvalue"

    def test_config_repr_hides_the_api_key(self):
        # A stray `logger.info(config)` or unhandled-exception traceback
        # must not print the key.
        config = AIConfig("openai_compatible", "m", self.SECRET, "https://x/v1")
        self.assertNotIn(self.SECRET, repr(config))

    def test_provider_failure_does_not_leak_the_api_key(self):
        from apps.ledger.receipt_extractor import extract_from_receipt_image

        config = {
            "primary": AIConfig("openai_compatible", "m", self.SECRET, "https://x/v1"),
            "fallback": None,
        }
        # Simulate a provider SDK echoing the key back in its error, the way
        # a real "401 Unauthorized: Incorrect API key provided: ..." body
        # would (a redaction bug means the assertion below fails).
        with patch("apps.ledger.receipt_extractor._compress_image", return_value=b"ok"), \
             patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.side_effect = Exception(
                f"401 Unauthorized: Incorrect API key provided: {self.SECRET}"
            )
            result = extract_from_receipt_image(b"img-bytes", config)
        self.assertNotIn(self.SECRET, result["error"])

    def test_check_ai_config_redacts_the_key_on_failure(self):
        from apps.ledger.receipt_extractor import check_ai_config

        config = {
            "primary": AIConfig("openai_compatible", "m", self.SECRET, "https://x/v1"),
            "fallback": None,
        }
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value.models.list.side_effect = Exception(
                f"401 Unauthorized: Incorrect API key provided: {self.SECRET}"
            )
            ok, message = check_ai_config(config)
        self.assertFalse(ok)
        self.assertNotIn(self.SECRET, message)

    def test_openai_client_does_not_follow_redirects(self):
        # The SSRF check vets base_url's host only; a 3xx to an internal
        # address must not be followed.
        from apps.ledger.receipt_extractor import _openai_client

        client = _openai_client(AIConfig("openai_compatible", "m", "k", "https://x/v1"))
        self.assertFalse(client._client.follow_redirects)


class ParseJsonFromTextTests(SimpleTestCase):
    def test_think_block_is_stripped(self):
        from apps.ledger.receipt_extractor import _parse_json_from_text

        text = '<think>reasoning about the receipt</think>{"amount": 10}'
        self.assertEqual(_parse_json_from_text(text), {"amount": 10})

    def test_unclosed_think_block_is_stripped(self):
        from apps.ledger.receipt_extractor import _parse_json_from_text

        text = '{"amount": 10}<think>trailing reasoning, cut off'
        self.assertEqual(_parse_json_from_text(text), {"amount": 10})

    def test_fenced_json_with_a_brace_inside_a_string(self):
        from apps.ledger.receipt_extractor import _parse_json_from_text

        text = '```json\n{"notes": "use { as a bracket", "amount": 10}\n```'
        self.assertEqual(
            _parse_json_from_text(text), {"notes": "use { as a bracket", "amount": 10}
        )


class FallbackTests(SimpleTestCase):
    def test_primary_failure_falls_through_to_fallback(self):
        from apps.ledger.receipt_extractor import extract_from_receipt_image

        config = {
            "primary": AIConfig("openai_compatible", "primary-model", "k1", "https://x/v1"),
            "fallback": AIConfig("gemini", "fallback-model", "k2"),
        }
        with patch("apps.ledger.receipt_extractor._compress_image", return_value=b"ok"), \
             patch("apps.ledger.receipt_extractor._run") as mock_run:
            mock_run.side_effect = [RuntimeError("primary is down"), {"amount": 5}]
            result = extract_from_receipt_image(b"img-bytes", config)
        self.assertEqual(result, {"amount": 5})
        self.assertEqual(mock_run.call_count, 2)

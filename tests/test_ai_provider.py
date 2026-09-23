import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kira_services.ai_provider import GeminiChatProvider, create_chat_provider
from kira_services.config import KiraConfig


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "hello"}, {"text": " there"}]
                    }
                }
            ]
        }


def config(provider="gemini"):
    return SimpleNamespace(
        ai_provider=provider,
        ai_api_key="test-key",
        ai_base_url="https://example.test/v1beta",
        chat_model="gemini-test-model",
    )


class GeminiProviderTests(unittest.TestCase):
    def test_factory_selects_gemini_provider(self):
        self.assertIsInstance(create_chat_provider(config()), GeminiChatProvider)

    def test_factory_handles_gemini_model_misconfigured_as_provider(self):
        self.assertIsInstance(create_chat_provider(config("gemini-3.7-flash")), GeminiChatProvider)

    def test_gemini_generate_uses_header_and_maps_messages(self):
        provider = GeminiChatProvider(config())
        messages = [
            {"role": "system", "content": "Be kind."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]

        with patch("kira_services.ai_provider.requests.post", return_value=FakeResponse()) as post:
            text = provider.generate(messages, max_tokens=123, temperature=0.2)

        self.assertEqual(text, "hello there")
        call = post.call_args.kwargs
        self.assertEqual(call["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(call["json"]["systemInstruction"]["parts"][0]["text"], "Be kind.")
        self.assertEqual(call["json"]["contents"][0]["role"], "user")
        self.assertEqual(call["json"]["contents"][1]["role"], "model")
        self.assertEqual(call["json"]["generationConfig"]["maxOutputTokens"], 123)
        self.assertEqual(call["json"]["generationConfig"]["temperature"], 0.2)
        self.assertEqual(call["timeout"], (5, 20))

    def test_gemini_37_uses_low_thinking_for_chat_latency(self):
        provider = GeminiChatProvider(config())

        with patch("kira_services.ai_provider.requests.post", return_value=FakeResponse()) as post:
            provider.generate(
                [{"role": "user", "content": "Hi"}],
                model="gemini-3.7-flash",
            )

        generation_config = post.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(generation_config["thinkingConfig"]["thinkingLevel"], "low")


class ConfigTests(unittest.TestCase):
    def test_provider_specific_key_wins_over_generic_key(self):
        with patch.dict("os.environ", {
            "KIRA_AI_PROVIDER": "gemini",
            "GEMINI_API_KEY": "gemini-key",
            "KIRA_AI_API_KEY": "old-generic-key",
        }, clear=True):
            loaded = KiraConfig.from_env()

        self.assertEqual(loaded.ai_api_key, "gemini-key")

    def test_model_accidentally_set_as_provider_is_normalized(self):
        with patch.dict("os.environ", {
            "KIRA_AI_PROVIDER": "gemini-3.7-flash",
            "GEMINI_API_KEY": "gemini-key",
        }, clear=True):
            loaded = KiraConfig.from_env()

        self.assertEqual(loaded.ai_provider, "gemini")
        self.assertEqual(loaded.chat_model, "gemini-3.7-flash")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

from tts import registry


class TTSProviderRegistryTests(unittest.TestCase):
    def test_unset_or_blank_order_uses_default(self):
        expected = ["elevenlabs", "pockettts", "pyttsx3"]

        self.assertEqual(registry.parse_provider_order(None), expected)
        self.assertEqual(registry.parse_provider_order("   "), expected)

    def test_explicit_provider_order_is_preserved(self):
        result = registry.parse_provider_order(
            "pyttsx3,pockettts,elevenlabs"
        )

        self.assertEqual(
            result,
            ["pyttsx3", "pockettts", "elevenlabs"],
        )

    def test_provider_order_is_normalized_and_deduplicated(self):
        result = registry.parse_provider_order(
            " PocketTTS,unknown,pockettts,,PYTTSX3 "
        )

        self.assertEqual(
            result,
            ["pockettts", "unknown", "pyttsx3"],
        )

    def test_resolve_providers_uses_configured_order(self):
        first_provider = Mock(name="first_provider")
        second_provider = Mock(name="second_provider")
        resolvers = {
            "first": Mock(return_value=first_provider),
            "second": Mock(return_value=second_provider),
        }

        result = registry.resolve_providers(
            ["second", "first"],
            resolvers,
        )

        self.assertEqual(result, [second_provider, first_provider])
        resolvers["first"].assert_called_once_with()
        resolvers["second"].assert_called_once_with()

    @patch("tts.registry.runtime_state.debug_print")
    def test_resolve_providers_reports_unknown_name(self, debug_print):
        provider = Mock(name="provider")

        result = registry.resolve_providers(
            ["unknown", "known"],
            {"known": Mock(return_value=provider)},
        )

        self.assertEqual(result, [provider])
        debug_print.assert_called_once_with(
            "[TTS] Unknown provider 'unknown' ignored."
        )

    def test_resolve_providers_skips_unconfigured_provider(self):
        configured_provider = Mock(name="configured_provider")

        result = registry.resolve_providers(
            ["unconfigured", "configured"],
            {
                "unconfigured": Mock(return_value=None),
                "configured": Mock(return_value=configured_provider),
            },
        )

        self.assertEqual(result, [configured_provider])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

import utils.speak_response as speech


class SpeechProviderResolutionTests(unittest.TestCase):
    def test_offline_resolution_returns_local_provider_only(self):
        local_provider = Mock(name="local_provider")

        with (
            patch.object(speech, "_pyttsx3_provider", local_provider),
            patch.object(speech, "is_connected", return_value=False),
            patch.object(speech, "ensure_api_key_loaded") as ensure_key,
        ):
            providers = speech.resolve_speech_providers()

        self.assertEqual(providers, [local_provider])
        ensure_key.assert_not_called()

    def test_online_resolution_reuses_ordered_providers(self):
        cloud_provider = Mock(name="cloud_provider")
        local_provider = Mock(name="local_provider")

        with (
            patch.object(speech, "_elevenlabs_provider", None),
            patch.object(speech, "_pyttsx3_provider", local_provider),
            patch.object(speech, "is_connected", return_value=True),
            patch.object(
                speech,
                "ensure_api_key_loaded",
                return_value=True,
            ),
            patch.object(
                speech,
                "ElevenLabsProvider",
                return_value=cloud_provider,
            ) as provider_type,
        ):
            first_result = speech.resolve_speech_providers()
            second_result = speech.resolve_speech_providers()

        self.assertEqual(
            first_result,
            [cloud_provider, local_provider],
        )
        self.assertEqual(second_result, first_result)
        provider_type.assert_called_once_with(
            speech._generate_elevenlabs_audio
        )

    def test_reload_config_discards_cached_cloud_provider(self):
        load_dotenv = Mock()

        with (
            patch.object(speech, "load_dotenv", load_dotenv),
            patch.object(speech, "_api_key_loaded", True),
            patch.object(speech, "_eleven_labs_disabled", True),
            patch.object(speech, "_elevenlabs_provider", Mock()),
            patch.object(
                speech,
                "ensure_api_key_loaded",
                return_value=True,
            ),
        ):
            reloaded = speech.reload_config()

            self.assertFalse(speech._api_key_loaded)
            self.assertFalse(speech._eleven_labs_disabled)
            self.assertIsNone(speech._elevenlabs_provider)

        self.assertTrue(reloaded)
        load_dotenv.assert_called_once_with(override=True)

    def test_invalid_cloud_key_disables_provider(self):
        with (
            patch.object(speech, "_eleven_labs_disabled", False),
            patch.object(
                speech,
                "ensure_api_key_loaded",
                return_value=True,
            ),
            patch.object(
                speech,
                "generate",
                side_effect=RuntimeError("invalid api key"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid api key"):
                speech._generate_elevenlabs_audio("hello")

            self.assertTrue(speech._eleven_labs_disabled)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

import requests

import utils.speak_response as speech


class SpeechProviderResolutionTests(unittest.TestCase):
    def test_configured_submitter_receives_speech(self):
        submitter = Mock(return_value=True)

        with patch.object(
            speech.dashboard_state,
            "record_ai_response",
        ) as record_response:
            speech.configure_speech_submitter(submitter)

            try:
                submitted = speech.speak_response("hello")
            finally:
                speech.configure_speech_submitter(None)

        self.assertTrue(submitted)
        submitter.assert_called_once_with("hello")
        record_response.assert_called_once_with("hello", source="tts")

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
        response = Mock(status_code=401)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.raise_for_status.side_effect = requests.HTTPError(
            "401 Unauthorized",
            response=response,
        )
        session = Mock()
        session.post.return_value = response

        with (
            patch.object(speech, "_eleven_labs_disabled", False),
            patch.object(
                speech,
                "ensure_api_key_loaded",
                return_value=True,
            ),
            patch.object(
                speech.requests,
                "Session",
                return_value=session,
            ),
        ):
            with self.assertRaises(requests.HTTPError):
                speech._generate_elevenlabs_audio("hello")

            self.assertTrue(speech._eleven_labs_disabled)

    def test_cloud_generation_uses_bounded_streaming_request(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b"audio", b" data"]
        session = Mock()
        session.post.return_value = response

        with (
            patch.object(
                speech,
                "ensure_api_key_loaded",
                return_value=True,
            ),
            patch.object(
                speech,
                "load_api_key",
                return_value="test-key",
            ),
            patch.object(
                speech.requests,
                "Session",
                return_value=session,
            ),
        ):
            audio = speech._generate_elevenlabs_audio(
                "hello",
                timeout_seconds=12.0,
            )

        self.assertEqual(audio, b"audio data")
        self.assertEqual(session.post.call_args.kwargs["timeout"], 12.0)
        self.assertTrue(session.post.call_args.kwargs["stream"])
        session.close.assert_called_once_with()
        response.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

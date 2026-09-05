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

    def test_synchronous_speech_uses_resolved_cloud_provider(self):
        provider = Mock(name="cloud_provider")
        provider.is_available.return_value = True
        session = provider.create_session.return_value
        session.play.return_value = True

        with (
            patch.object(speech, "_speech_submitter", None),
            patch.object(
                speech,
                "resolve_speech_providers",
                return_value=[provider],
            ),
            patch.object(
                speech.dashboard_state,
                "record_ai_response",
            ),
        ):
            played = speech.speak_response("hello")

        self.assertTrue(played)
        provider.create_session.assert_called_once_with("hello")
        cancel_event = session.play.call_args.args[0]
        self.assertFalse(cancel_event.is_set())

    def test_synchronous_speech_follows_provider_fallback_order(self):
        first_provider = Mock(name="first_provider")
        first_provider.name = "pockettts"
        first_provider.is_available.return_value = True
        first_provider.create_session.return_value.play.return_value = False
        fallback_provider = Mock(name="fallback_provider")
        fallback_provider.name = "pyttsx3"
        fallback_provider.is_available.return_value = True
        fallback_provider.create_session.return_value.play.return_value = True

        with (
            patch.object(speech, "_speech_submitter", None),
            patch.object(
                speech,
                "resolve_speech_providers",
                return_value=[first_provider, fallback_provider],
            ),
            patch.object(speech.dashboard_state, "record_ai_response"),
        ):
            played = speech.speak_response("hello")

        self.assertTrue(played)
        first_provider.create_session.assert_called_once_with("hello")
        fallback_provider.create_session.assert_called_once_with("hello")

    def test_offline_resolution_returns_local_providers(self):
        pocket_provider = Mock(name="pocket_provider")
        local_provider = Mock(name="local_provider")

        with (
            patch.dict(
                speech.os.environ,
                {"TTS_PROVIDER_ORDER": "pockettts,pyttsx3"},
                clear=True,
            ),
            patch.object(
                speech,
                "_pockettts_provider",
                pocket_provider,
            ),
            patch.object(speech, "_pyttsx3_provider", local_provider),
            patch.object(speech, "is_connected", return_value=False),
            patch.object(speech, "ensure_api_key_loaded") as ensure_key,
        ):
            providers = speech.resolve_speech_providers()

        self.assertEqual(providers, [pocket_provider, local_provider])
        ensure_key.assert_not_called()

    def test_online_resolution_reuses_ordered_providers(self):
        cloud_provider = Mock(name="cloud_provider")
        pocket_provider = Mock(name="pocket_provider")
        local_provider = Mock(name="local_provider")

        with (
            patch.dict(
                speech.os.environ,
                {
                    "TTS_PROVIDER_ORDER": (
                        "elevenlabs,pockettts,pyttsx3"
                    )
                },
                clear=True,
            ),
            patch.object(speech, "_elevenlabs_provider", None),
            patch.object(
                speech,
                "_pockettts_provider",
                pocket_provider,
            ),
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
            [cloud_provider, pocket_provider, local_provider],
        )
        self.assertEqual(second_result, first_result)
        provider_type.assert_called_once_with(
            speech._generate_elevenlabs_audio
        )

    def test_configured_order_reorders_providers(self):
        cloud_provider = Mock(name="cloud_provider")
        local_provider = Mock(name="local_provider")

        with (
            patch.dict(
                speech.os.environ,
                {"TTS_PROVIDER_ORDER": "pyttsx3,elevenlabs"},
                clear=True,
            ),
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
            ),
        ):
            providers = speech.resolve_speech_providers()

        self.assertEqual(providers, [local_provider, cloud_provider])

    def test_omitted_cloud_provider_is_not_checked(self):
        local_provider = Mock(name="local_provider")

        with (
            patch.dict(
                speech.os.environ,
                {"TTS_PROVIDER_ORDER": "pyttsx3"},
                clear=True,
            ),
            patch.object(speech, "_pyttsx3_provider", local_provider),
            patch.object(
                speech,
                "is_connected",
                return_value=False,
            ) as is_connected,
            patch.object(speech, "ensure_api_key_loaded") as ensure_key,
        ):
            providers = speech.resolve_speech_providers()

        self.assertEqual(providers, [local_provider])
        is_connected.assert_not_called()
        ensure_key.assert_not_called()

    def test_tts_availability_uses_resolved_providers(self):
        provider = Mock(name="provider")
        provider.is_available.return_value = True
        legacy_tts = Mock()
        legacy_tts.init.side_effect = RuntimeError("legacy path used")

        with (
            patch.dict(speech.os.environ, {}, clear=True),
            patch.dict("sys.modules", {"pyttsx3": legacy_tts}),
            patch.object(speech, "is_connected", return_value=False),
            patch.object(
                speech,
                "resolve_speech_providers",
                return_value=[provider],
            ) as resolve_providers,
        ):
            available = speech.is_tts_available()

        self.assertTrue(available)
        resolve_providers.assert_called_once_with()
        provider.is_available.assert_called_once_with()

    def test_reload_config_discards_cached_cloud_provider(self):
        load_dotenv = Mock()
        pocket_provider = Mock()

        with (
            patch.object(speech, "load_dotenv", load_dotenv),
            patch.object(speech, "_api_key_loaded", True),
            patch.object(speech, "_eleven_labs_disabled", True),
            patch.object(speech, "_elevenlabs_provider", Mock()),
            patch.object(
                speech,
                "_pockettts_provider",
                pocket_provider,
            ),
            patch.object(
                speech,
                "is_tts_available",
                return_value=True,
            ) as is_tts_available,
            patch.object(speech, "ensure_api_key_loaded") as ensure_key,
        ):
            reloaded = speech.reload_config()

            self.assertFalse(speech._api_key_loaded)
            self.assertFalse(speech._eleven_labs_disabled)
            self.assertIsNone(speech._elevenlabs_provider)
            self.assertIsNone(speech._pockettts_provider)

        self.assertTrue(reloaded)
        pocket_provider.close.assert_called_once_with()
        load_dotenv.assert_called_once_with(override=True)
        is_tts_available.assert_called_once_with()
        ensure_key.assert_not_called()

    def test_reload_recreates_pocket_provider_with_updated_settings(self):
        created_providers = []
        captured_settings = []

        def create_provider():
            captured_settings.append(
                (
                    speech.os.environ["CODA_POCKET_TTS_LANGUAGE"],
                    speech.os.environ["CODA_POCKET_TTS_VOICE"],
                )
            )
            provider = Mock(name=f"pocket_provider_{len(created_providers)}")
            provider.name = "pockettts"
            provider.is_available.return_value = True
            created_providers.append(provider)
            return provider

        def reload_environment(*, override):
            self.assertTrue(override)
            speech.os.environ["CODA_POCKET_TTS_LANGUAGE"] = "german_24l"
            speech.os.environ["CODA_POCKET_TTS_VOICE"] = "new-voice.wav"

        with (
            patch.dict(
                speech.os.environ,
                {
                    "TTS_PROVIDER_ORDER": "pockettts",
                    "CODA_POCKET_TTS_LANGUAGE": "english",
                    "CODA_POCKET_TTS_VOICE": "alba",
                },
                clear=True,
            ),
            patch.object(speech, "_pockettts_provider", None),
            patch.object(
                speech,
                "PocketTTSProvider",
                side_effect=create_provider,
            ),
            patch.object(
                speech,
                "load_dotenv",
                side_effect=reload_environment,
            ),
        ):
            initial = speech.resolve_speech_providers()[0]
            self.assertTrue(speech.reload_config())
            reloaded = speech._pockettts_provider

        self.assertIsNot(initial, reloaded)
        initial.close.assert_called_once_with()
        self.assertEqual(
            captured_settings,
            [
                ("english", "alba"),
                ("german_24l", "new-voice.wav"),
            ],
        )

    def test_pocket_provider_is_created_once_and_reused(self):
        pocket_provider = Mock(name="pocket_provider")

        with (
            patch.dict(
                speech.os.environ,
                {"TTS_PROVIDER_ORDER": "pockettts"},
                clear=True,
            ),
            patch.object(speech, "_pockettts_provider", None),
            patch.object(
                speech,
                "PocketTTSProvider",
                return_value=pocket_provider,
            ) as provider_type,
        ):
            first_result = speech.resolve_speech_providers()
            second_result = speech.resolve_speech_providers()

        self.assertEqual(first_result, [pocket_provider])
        self.assertEqual(second_result, first_result)
        provider_type.assert_called_once_with()

    def test_shutdown_closes_and_discards_pocket_provider(self):
        pocket_provider = Mock()

        with patch.object(
            speech,
            "_pockettts_provider",
            pocket_provider,
        ):
            speech.shutdown()

            self.assertIsNone(speech._pockettts_provider)

        pocket_provider.close.assert_called_once_with()

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
                speech,
                "load_api_key",
                return_value="invalid-test-key",
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

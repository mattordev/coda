import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ai.telemetry import logger
from ai.telemetry.models import (
    AttemptOutcome,
    PrivacyAction,
    ProviderAttempt,
    ProviderLocality,
    WorkloadPurpose,
)


class TelemetryLoggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.state_file = Path(self.temp.name) / "telemetry_state.json"
        self.log_dir = Path(self.temp.name) / "logs" / "telemetry"
        self.active_session_file = self.log_dir / "active-session.jsonl"

        self.legacy_state = {
            "openai": {
                "last_failed_on": 950.0,
                "last_attempt": 850.0,
            }
        }

        self.state_file.write_text(
            json.dumps(self.legacy_state),
            encoding="utf-8",
        )

        state_file_patch = mock.patch.object(
            logger,
            "_STATE_FILE",
            self.state_file,
        )
        state_file_patch.start()
        self.addCleanup(state_file_patch.stop)

        log_dir_patch = mock.patch.object(
            logger,
            "_TELEMETRY_LOG_DIR",
            self.log_dir,
        )
        log_dir_patch.start()
        self.addCleanup(log_dir_patch.stop)

        active_file_patch = mock.patch.object(
            logger,
            "_ACTIVE_SESSION_FILE",
            self.active_session_file,
        )
        active_file_patch.start()
        self.addCleanup(active_file_patch.stop)

        logger._provider_state = {}
        logger._attempts = []
        logger._attempts_pending_state_migration = False
        logger._state_loaded = False
        logger._session_ready = False

    @staticmethod
    def _attempt(trace_id="trace-1"):
        return ProviderAttempt(
            trace_id=trace_id,
            timestamp=1000.0,
            purpose=WorkloadPurpose.CONVERSATION,
            provider="openai",
            model="test-model",
            locality=ProviderLocality.CLOUD,
            sequence=1,
            outcome=AttemptOutcome.SUCCESS,
            privacy_action=PrivacyAction.SANITIZE,
        )

    def test_legacy_state_migrates_without_changing_cooldown(self):
        """Legacy cooldown data survives migration unchanged."""
        with mock.patch.object(
            logger.time,
            "time",
            return_value=1000.0,
        ):
            logger._load_state_once()

            before_migration = logger.should_skip_provider(
                "openai",
                cooldown=100,
            )

            logger._save_state_to_disk()

            persisted = json.loads(
                self.state_file.read_text(encoding="utf-8")
            )

            logger._provider_state = {}
            logger._state_loaded = False

            after_migration = logger.should_skip_provider(
                "openai",
                cooldown=100,
            )

        self.assertTrue(before_migration)
        self.assertTrue(after_migration)
        self.assertEqual(persisted["schema_version"], 1)
        self.assertNotIn("attempts", persisted)
        self.assertEqual(
            persisted["provider_state"]["openai"],
            self.legacy_state["openai"],
        )

    def test_corrupt_json_data(self):
        """Unreadable state safely starts with no saved telemetry."""
        self.state_file.write_text(
            "{ definitely not valid JSON :D",
            encoding="utf-8",
        )

        logger._load_state_once()

        self.assertEqual(logger._provider_state, {})
        self.assertEqual(logger._attempts, [])

    def test_missing_state_loads_as_empty_state(self):
        """A missing state file safely starts with no saved telemetry."""
        self.state_file.unlink()

        logger._load_state_once()

        self.assertEqual(logger._provider_state, {})
        self.assertEqual(logger._attempts, [])

    def test_partial_state_normalizes_invalid_values(self):
        """Partial state keeps valid cooldown data and discards bad values."""
        self.state_file.write_text(
            json.dumps({
                "schema_version": 1,
                "provider_state": {
                    "openai": {
                        "last_failed_on": "not a timestamp",
                        "last_attempt": 850.0,
                    },
                    "invalid-provider": "not an object",
                },
                "attempts": "not a list",
            }),
            encoding="utf-8",
        )

        logger._load_state_once()

        self.assertEqual(
            logger._provider_state,
            {
                "openai": {
                    "last_failed_on": None,
                    "last_attempt": 850.0,
                }
            },
        )
        self.assertEqual(logger._attempts, [])

    def test_log_attempt_survives_storage_error(self):
        """A failed state write does not prevent an attempt being recorded."""
        with mock.patch.object(logger.time, "time", return_value=1000.0):
            with mock.patch.object(
                logger,
                "_save_state_to_disk",
                side_effect=OSError("disk unavailable"),
            ):
                logger.log_attempt("openai")

        self.assertEqual(
            logger._provider_state["openai"]["last_attempt"],
            1000.0,
        )

    def test_log_failure_survives_storage_error(self):
        """A failed state write does not prevent a failure being recorded."""
        with mock.patch.object(logger.time, "time", return_value=1000.0):
            with mock.patch.object(
                logger,
                "_save_state_to_disk",
                side_effect=OSError("disk unavailable"),
            ):
                logger.log_failure("openai")

        self.assertEqual(
            logger._provider_state["openai"]["last_failed_on"],
            1000.0,
        )

    def test_attempt_is_written_to_active_session_without_sensitive_fields(self):
        """A typed attempt is durable without storing request or response text."""
        logger.record_attempt(self._attempt())

        records = [
            json.loads(line)
            for line in self.active_session_file.read_text(
                encoding="utf-8"
            ).splitlines()
        ]

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["trace_id"], "trace-1")
        self.assertEqual(records[0]["provider"], "openai")
        self.assertNotIn("prompt", records[0])
        self.assertNotIn("request", records[0])
        self.assertNotIn("response", records[0])

    def test_active_session_retention_is_bounded(self):
        """Only the newest configured number of attempts remain active."""
        with mock.patch.object(logger, "_MAX_SESSION_ATTEMPTS", 2):
            logger.record_attempt(self._attempt("trace-1"))
            logger.record_attempt(self._attempt("trace-2"))
            logger.record_attempt(self._attempt("trace-3"))

        records = [
            json.loads(line)
            for line in self.active_session_file.read_text(
                encoding="utf-8"
            ).splitlines()
        ]

        self.assertEqual(
            [record["trace_id"] for record in records],
            ["trace-2", "trace-3"],
        )

    def test_finalize_session_archives_active_file(self):
        """Graceful shutdown gives the completed session a dated filename."""
        logger.record_attempt(self._attempt())

        archived_file = logger.finalize_session()

        self.assertIsNotNone(archived_file)
        self.assertTrue(archived_file.exists())
        self.assertFalse(self.active_session_file.exists())
        self.assertRegex(
            archived_file.name,
            r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-\d+)?\.jsonl$",
        )

    def test_record_attempt_survives_session_storage_error(self):
        """Session storage failure cannot escape into a provider request."""
        with mock.patch.object(
            logger,
            "_save_session_to_disk",
            side_effect=OSError("disk unavailable"),
        ):
            logger.record_attempt(self._attempt())

        self.assertEqual(logger._attempts[0]["trace_id"], "trace-1")

    def test_concurrent_state_writes_remain_valid(self):
        """Concurrent cooldown updates leave a complete, readable state file."""
        providers = [f"provider-{index}" for index in range(20)]

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(logger.log_attempt, providers))

        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertEqual(
            set(persisted["provider_state"]),
            {"openai", *providers},
        )

    def test_concurrent_session_writes_remain_valid(self):
        """Concurrent attempt records leave a complete, readable session file."""
        trace_ids = [f"trace-{index}" for index in range(20)]

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(
                lambda trace_id: logger.record_attempt(self._attempt(trace_id)),
                trace_ids,
            ))

        records = [
            json.loads(line)
            for line in self.active_session_file.read_text(
                encoding="utf-8"
            ).splitlines()
        ]

        self.assertEqual(
            {record["trace_id"] for record in records},
            set(trace_ids),
        )

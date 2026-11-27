import io
import unittest

from runtime.process_output import capture_output


class ProcessOutputTests(unittest.TestCase):
    def test_truncated_secret_takes_precedence_over_a_shorter_match(self):
        result = capture_output(io.BytesIO(b"abcdef"), 4, ("abc", "abcdef"))
        self.assertEqual(result.text, "[redacted]")
        self.assertEqual(result.dropped_bytes, 2)

    def test_drains_beyond_capture_limit_and_redacts_a_cut_off_secret(self):
        data = b"prefix private-token" + b"!" * 20000
        stream = io.BytesIO(data)
        result = capture_output(stream, 14, ("private-token",))
        self.assertEqual(result.text, "prefix [redacted]")
        self.assertEqual(result.dropped_bytes, len(data) - 14)
        self.assertEqual(stream.tell(), len(data))

    def test_redacts_a_secret_split_between_reads(self):
        stream = io.BytesIO(b"x" * 8189 + "secret-é".encode("utf-8"))
        result = capture_output(stream, 9000, ("secret-é",))
        self.assertEqual(result.text, "x" * 8189 + "[redacted]")
        self.assertEqual(result.dropped_bytes, 0)

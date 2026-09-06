import io
import unittest
from unittest import mock

from utils import console


class CodaRuntimeEncodingTests(unittest.TestCase):
    def test_configures_stdout_as_utf8_for_unicode_llm_text(self):
        buffer = io.BytesIO()
        stdout = io.TextIOWrapper(buffer, encoding="cp1252")

        with mock.patch.object(console.sys, "stdout", stdout):
            console.configure_stdout_encoding()

        stdout.write("RAG—doesn’t")
        stdout.flush()
        self.assertEqual(buffer.getvalue().decode("utf-8"), "RAG—doesn’t")

    def test_ignores_stdout_without_reconfigure_support(self):
        stdout = mock.Mock(spec=[])

        with mock.patch.object(console.sys, "stdout", stdout):
            console.configure_stdout_encoding()


if __name__ == "__main__":
    unittest.main()

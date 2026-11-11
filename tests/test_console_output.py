"""Background diagnostics cannot split words or indefinitely retain log data."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event
import unittest
from unittest.mock import patch

from runtime.incremental_display import IncrementalDisplay
from utils.console import ConsoleOutput


class ConsoleOutputTests(unittest.TestCase):
    def test_background_thread_returns_without_interrupting_answer(self):
        console, output = ConsoleOutput(), StringIO()
        display = IncrementalDisplay(Event(), console=console,
                                     write=lambda text: print(text, end=''))
        self.addCleanup(display.finish, False)
        with patch('sys.stdout', output), ThreadPoolExecutor(1) as pool:
            display.feed('A sent')
            pool.submit(console.print_line, 'Listener ready').result(timeout=1)
            self.assertEqual(output.getvalue(), 'CODA: A sent')
            display.feed('ence.')
            display.finish(True)
            console.print_line('Next request')
        self.assertEqual(output.getvalue(), 'CODA: A sentence.\nListener ready\nNext request\n')

    def test_cancellation_flushes_after_incomplete_marker(self):
        console, output, cancel = ConsoleOutput(), StringIO(), Event()
        display = IncrementalDisplay(cancel, console=console,
                                     write=lambda text: print(text, end=''))
        with patch('sys.stdout', output):
            display.feed('Partial')
            console.print_line('Cancelled')
            cancel.set()
            display.finish(False)
            display.finish(False)
        self.assertEqual(output.getvalue(), 'CODA: Partial\n[Response incomplete.]\nCancelled\n')

    def test_bounded_queue_and_custom_file_is_immediate(self):
        console, output, file = ConsoleOutput(limit=2), StringIO(), StringIO()
        owner = object()
        with patch('sys.stdout', output):
            console.write_response(owner, 'Answer\n', lambda text: print(text, end=''))
            for index in range(4):
                console.print_line(f'log {index}')
            console.print_line('file log', file=file)
            self.assertEqual(file.getvalue(), 'file log\n')
            console.end_response(owner)
        self.assertIn('2 older background messages omitted', output.getvalue())
        self.assertTrue(output.getvalue().endswith('log 2\nlog 3\n'))
        self.assertNotIn('log 0', output.getvalue())

    def test_broken_response_writer_releases_console(self):
        console, output = ConsoleOutput(), StringIO()
        def broken(_text):
            raise OSError('terminal unavailable')
        with self.assertRaises(OSError):
            console.write_response(object(), 'text', broken)
        with patch('sys.stdout', output):
            console.print_line('Visible again')
        self.assertEqual(output.getvalue(), 'Visible again\n')

"""Worker archive/pipe isolation, ordered lines and chunk-safe UTF-8."""
from contextlib import nullcontext
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app
from process_output import ProcessLines, SessionOutput, parent_output


class ProcessOutputTests(unittest.TestCase):
    def test_utf8_can_split_at_every_byte_and_final_line_has_no_newline(self):
        output = ProcessLines()
        for byte in "Привет 🌌\r\nВторая строка\nХвост".encode():
            output.feed(bytes([byte]))
        self.assertEqual(list(output.lines), ["Привет 🌌", "Вторая строка"])
        output.feed(b"", final=True)
        output.feed(b"ignored", final=True)
        self.assertEqual(list(output.lines), ["Привет 🌌", "Вторая строка", "Хвост"])

    def test_invalid_bytes_do_not_crash_the_frontend_decoder(self):
        output = ProcessLines()
        output.feed(b"first\xff\nlast\xd0", final=True)
        self.assertEqual(list(output.lines), ["first\ufffd", "last\ufffd"])

    def test_threads_cannot_interleave_parts_of_structured_lines(self):
        archive, pipe, barrier = io.StringIO(), io.BytesIO(), Barrier(2)
        output = SessionOutput(archive, pipe)
        def write(prefix):
            for i in range(40):
                output.write(prefix)
                barrier.wait()
                output.write(f"{i}\n")
        threads = [Thread(target=write, args=(prefix,)) for prefix in ("UI_EVENT ", "Обычный лог ")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        output.finish()
        text = archive.getvalue()
        self.assertEqual(text, pipe.getvalue().decode("utf-8"))
        self.assertEqual(sorted(text.splitlines()), sorted(f"{prefix}{i}" for prefix in ("UI_EVENT ", "Обычный лог ") for i in range(40)))

    def test_pipe_failure_does_not_break_archiving(self):
        archive = io.StringIO()
        pipe = SimpleNamespace(write=lambda _: (_ for _ in ()).throw(BrokenPipeError()))
        output = SessionOutput(archive, pipe)
        output.write("first\n")
        output.write("final tail")
        output.finish()
        self.assertIsNone(output.pipe)
        self.assertEqual(archive.getvalue(), "first\nfinal tail")

    def test_short_binary_writes_preserve_utf8_and_flush_keeps_partial_text(self):
        data = bytearray()
        def write(part):
            data.extend(part[:3])
            return min(3, len(part))
        pipe = SimpleNamespace(write=write, flush=lambda: None)
        output = SessionOutput(io.StringIO(), pipe)
        output.write("Код: пример")
        output.flush()
        self.assertEqual(data.decode(), "Код: пример")
        self.assertEqual(output.write("\n"), 1)
        self.assertFalse(output.isatty())

    def test_interpreter_stream_is_not_closed_but_owned_duplicate_is(self):
        original = io.BytesIO()
        with patch.object(sys, "__stdout__", SimpleNamespace(buffer=original)):
            with parent_output() as output:
                output.write(b"hello")
        self.assertEqual(original.getvalue(), b"hello")
        duplicate = io.BytesIO()
        with patch.object(sys, "__stdout__", None), patch("process_output._duplicate_stdout", return_value=duplicate):
            with parent_output() as output:
                output.write(b"hello")
        self.assertTrue(duplicate.closed)

    @unittest.skipUnless(os.name == "nt", "Windows inherited-handle check")
    def test_missing_python_stdout_still_reaches_the_inherited_windows_pipe(self):
        code = "import sys; from process_output import parent_output; sys.__stdout__=None\nwith parent_output() as pipe: pipe.write('Привет из pythonw'.encode('utf-8')); pipe.flush()"
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout.decode(), "Привет из pythonw")

    def test_worker_flags_preserve_file_mode_and_forward_pipe_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.log"
            def worker():
                self.assertEqual(sys.argv[1:], ["--self-test"])
                print("Готово", flush=True)
                print("Ошибка", file=sys.stderr, flush=True)
                return 0
            pipe = io.BytesIO()
            with patch.dict(sys.modules, worker=SimpleNamespace(main=worker)), patch("process_output.parent_output", return_value=nullcontext(pipe)), patch.object(sys, "argv", ["app", "--bot", str(path), "--pipe", "--self-test"]):
                self.assertEqual(app.main(), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "Готово\nОшибка\n")
            self.assertEqual(pipe.getvalue().decode(), "Готово\nОшибка\n")
            with patch.dict(sys.modules, worker=SimpleNamespace(main=worker)), patch.object(sys, "argv", ["app", "--bot", str(path), "--self-test"]):
                self.assertEqual(app.main(), 0)

    def test_invalid_or_duplicate_flags_do_not_start_worker(self):
        for flags in (("--unknown",), ("--pipe", "--pipe")):
            with patch.object(sys, "argv", ["app", "--bot", "unused.log", *flags]), patch.dict(sys.modules, worker=SimpleNamespace(main=lambda: self.fail("Unexpected worker"))):
                self.assertEqual(app.main(), 2)


if __name__ == "__main__":
    unittest.main()

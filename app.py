"""Desktop entry point."""

from contextlib import redirect_stderr, redirect_stdout
import sys
from pathlib import Path


def main() -> int:
    if sys.argv[1:] == ["--self-test-gui"]:
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QApplication
        from gui import MainWindow
        from paths import data_dir, worker_command

        app = QApplication([])
        window = MainWindow()
        if window.windowIcon().isNull():
            raise RuntimeError("Bundled application icon is missing")
        window.show()
        app.processEvents()
        # Exercise the same executable/argument selection as the Start button.
        # The worker self-test reads fixtures and never connects to Twitch/AI.
        process = QProcess()
        log_path = data_dir() / "gui-worker-self-test.log"
        program, arguments = worker_command(log_path, self_test=True)
        process.start(program, arguments)
        if not process.waitForFinished(60000):
            process.kill()
            process.waitForFinished(5000)
            raise RuntimeError("GUI worker self-test timed out")
        if process.exitStatus() != QProcess.NormalExit or process.exitCode() != 0:
            raise RuntimeError("GUI worker self-test failed")
        if "WORKER_READY" not in log_path.read_text(encoding="utf-8"):
            raise RuntimeError("GUI worker did not load its configuration")
        window.close()
        app.quit()
        return 0
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--bot":
        log_path = Path(sys.argv[2]).resolve()
        if len(sys.argv) == 4 and sys.argv[3] != "--self-test":
            return 2
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", buffering=1) as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                sys.argv = [sys.argv[0]] + (["--self-test"] if len(sys.argv) == 4 else [])
                from worker import main as run_worker

                return run_worker()
    if len(sys.argv) != 1:
        return 2
    from gui import run_gui

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

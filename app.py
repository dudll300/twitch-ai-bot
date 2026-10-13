"""Desktop entry point."""

from contextlib import redirect_stderr, redirect_stdout
import sys
from pathlib import Path


def main() -> int:
    if sys.argv[1:] == ["--self-test-gui"]:
        from PySide6.QtWidgets import QApplication
        from gui import MainWindow

        app = QApplication([])
        window = MainWindow()
        if window.windowIcon().isNull():
            raise RuntimeError("Bundled application icon is missing")
        window.show()
        app.processEvents()
        if getattr(sys, "frozen", False):
            # Exercise the same executable through Qt's process launcher,
            # including PyInstaller's inherited environment and log routing.
            from PySide6.QtCore import QProcess
            log_path = window._root / "self-test-worker.log"
            window.process.start(sys.executable, ["--bot", str(log_path), "--self-test"])
            if not window.process.waitForFinished(30000):
                window.process.kill()
                window.process.waitForFinished(5000)
                raise RuntimeError("GUI could not launch its packaged worker")
            if window.process.exitStatus() != QProcess.NormalExit or window.process.exitCode() != 0:
                raise RuntimeError("Packaged GUI worker failed")
            if "WORKER_READY" not in log_path.read_text(encoding="utf-8"):
                raise RuntimeError("Packaged GUI worker did not become ready")
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

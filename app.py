"""Desktop entry point."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
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
        if app.property("studioFontFamily") != "Nunito":
            raise RuntimeError("Bundled Nunito font is missing")
        window.show()
        app.processEvents()
        window.close()
        app.quit()
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "--bot":
        log_path = Path(sys.argv[2]).resolve()
        flags = sys.argv[3:]
        if len(flags) != len(set(flags)) or set(flags) - {"--self-test", "--pipe"}:
            return 2
        log_path.parent.mkdir(parents=True, exist_ok=True)
        from process_output import SessionOutput, parent_output
        with ExitStack() as stack:
            stream = stack.enter_context(log_path.open("a", encoding="utf-8", buffering=1))
            try:
                pipe = stack.enter_context(parent_output()) if "--pipe" in flags else None
            except OSError:
                stream.write("Не удалось открыть канал связи с интерфейсом. Перезапустите приложение.\n")
                return 1
            output = SessionOutput(stream, pipe)
            with redirect_stdout(output), redirect_stderr(output):
                sys.argv = [sys.argv[0]] + (["--self-test"] if "--self-test" in flags else [])
                try:
                    from worker import main as run_worker
                    return run_worker()
                finally:
                    output.finish()
    if len(sys.argv) != 1:
        return 2
    from gui import run_gui

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

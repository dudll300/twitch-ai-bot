"""Desktop entry point."""

from contextlib import redirect_stderr, redirect_stdout
import sys
from pathlib import Path


def main() -> int:
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

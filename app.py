"""Desktop entry point."""

import sys


def main() -> int:
    if len(sys.argv) != 1:
        print("Использование: python app.py")
        return 2
    from gui import run_gui

    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

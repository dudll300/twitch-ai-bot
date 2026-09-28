"""Console worker used by the packaged GUI to keep logs visible in its window."""

import asyncio
import sys

from bot import Bot, config


def main() -> int:
    try:
        if sys.argv[1:] == ["--self-test"]:
            Bot(config())
            print("WORKER_READY", flush=True)
            return 0
        if len(sys.argv) != 1:
            print("Использование: worker.py [--self-test]", flush=True)
            return 2
        asyncio.run(Bot(config()).run())
    except (ValueError, RuntimeError) as exc:
        print(exc, flush=True)
        return 1
    except KeyboardInterrupt:
        print("Бот остановлен", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

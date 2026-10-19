"""Console worker used by the packaged GUI to keep logs visible in its window."""

import asyncio
import sys

from bot import Bot, config
from runtime_events import UiEventEmitter, install_ui_events, emit_ui


def main() -> int:
    try:
        if sys.argv[1:] == ["--self-test"]:
            Bot(config())
            print("WORKER_READY", flush=True)
            return 0
        if len(sys.argv) != 1:
            print("Использование: worker.py [--self-test]", flush=True)
            return 2
        cfg = config()
        bot = Bot(cfg)
        install_ui_events(UiEventEmitter(bot.history.session, secrets=(cfg.get("AI_API_KEY", ""),)))
        emit_ui("lifecycle", state="starting", channel=cfg["TWITCH_CHANNEL"],
                model=cfg["AI_MODEL"], reward=cfg["TWITCH_REWARD_TITLE"])
        emit_ui("settings", section="autonomous", version=bot.autonomous.revision)
        asyncio.run(bot.run())
    except (ValueError, RuntimeError) as exc:
        print(exc, flush=True)
        return 1
    except KeyboardInterrupt:
        print("Бот остановлен", flush=True)
    finally:
        install_ui_events(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

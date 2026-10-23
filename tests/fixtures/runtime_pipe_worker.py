"""Offline child process for the GUI's actual QProcess pipe lifecycle."""
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from process_output import SessionOutput, parent_output
from runtime_events import UiEventEmitter


def main():
    path, session, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    with path.open("a", encoding="utf-8") as archive, parent_output() as pipe:
        output = SessionOutput(archive, pipe)
        with redirect_stdout(output), redirect_stderr(output):
            emitter = UiEventEmitter(session)
            emitter.emit("lifecycle", state="starting", channel="fixture_channel", model="fixture/model")
            if mode == "tail":
                print("Последняя строка без перевода", end="", flush=True)
                return
            emitter.emit("auth", state="required", role="bot", account="fixture_bot", url="https://www.twitch.tv/activate", code="ABC-123", expires_in=60)
            print("Вопрос от fake принят", flush=True)
            print("Ответ отправлен для fake", flush=True)
            print("Жду вопросов по награде fake", flush=True)
            print("Откройте ссылку и войдите в Twitch под аккаунтом fake:", flush=True)
            print("https://www.twitch.tv/activate", flush=True)
            print("Код: WRONG", flush=True)
            if sys.stdin.readline().strip() != "continue":
                return
            if mode == "authfail":
                emitter.emit("auth", state="failed", reason="wrong_account")
                return 1
            # QProcess must merge stderr without mixing independent decoders.
            sys.__stderr__.buffer.write("Служебная строка stderr\n".encode())
            sys.__stderr__.buffer.flush()
            emitter.emit("auth", state="complete")
            emitter.emit("chat", state="ready")
            emitter.emit("eventsub", state="ready")
            emitter.emit("queue", size=1, phase="queued")
            emitter.emit("reply", state="sent", record_id="fixture")
            print("AUTO_EVENT {\"status\":\"silent\",\"reason\":\"no_reason\"}", flush=True)
            # Force code points to span native pipe notifications.
            encoded = "Разбитая строка: привет 🌌\n".encode()
            archive.write(encoded.decode())
            archive.flush()
            for byte in encoded:
                pipe.write(bytes([byte]))
                pipe.flush()
                time.sleep(.002)
            if sys.stdin.readline().strip() == "exit":
                print("Хвост перед завершением", end="", flush=True)


if __name__ == "__main__":
    raise SystemExit(main() or 0)

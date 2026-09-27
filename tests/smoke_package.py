"""Check the single-file Windows application without network access."""

import os
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "Chunda.exe"


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        env = os.environ.copy()
        env["APPDATA"] = temporary
        env["QT_QPA_PLATFORM"] = "offscreen"
        log_path = Path(temporary) / "worker.log"
        missing = subprocess.run([str(EXE), "--bot", str(log_path)], env=env, timeout=30)
        assert missing.returncode == 1, missing.returncode
        assert "TWITCH_CHANNEL" in log_path.read_text(encoding="utf-8")

        data = Path(temporary) / "ChundaBot"
        (data / ".env").write_text(
            "TWITCH_CHANNEL=sophie\nTWITCH_BOT_NAME=chundabot\n"
            "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
            "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
        )
        ready = subprocess.run([str(EXE), "--bot", str(log_path), "--self-test"], env=env, timeout=30)
        assert ready.returncode == 0, (ready.returncode, log_path.read_text(encoding="utf-8"))
        assert "WORKER_READY" in log_path.read_text(encoding="utf-8")
        assert (data / "memory.json").exists()

        gui = subprocess.Popen([str(EXE)], env=env)
        try:
            time.sleep(3)
            assert gui.poll() is None, f"GUI завершился сразу с кодом {gui.returncode}"
        finally:
            gui.terminate()
            gui.wait(timeout=10)
    print("Single-file GUI and worker started successfully.")


if __name__ == "__main__":
    main()

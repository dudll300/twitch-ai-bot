"""Quick check of the two packaged Windows executables without network access."""

import os
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "Chunda"


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        env = os.environ.copy()
        env["APPDATA"] = temporary
        env["PYTHONIOENCODING"] = "utf-8"
        env["QT_QPA_PLATFORM"] = "offscreen"
        worker = subprocess.run(
            [str(DIST / "ChundaWorker.exe")], env=env, capture_output=True,
            text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert worker.returncode == 1, (worker.returncode, worker.stdout, worker.stderr)
        assert "TWITCH_CHANNEL" in worker.stdout, (worker.stdout, worker.stderr)

        data = Path(temporary) / "ChundaBot"
        data.mkdir(exist_ok=True)
        (data / ".env").write_text(
            "TWITCH_CHANNEL=sophie\nTWITCH_BOT_NAME=chundabot\n"
            "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
            "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
        )
        ready = subprocess.run(
            [str(DIST / "ChundaWorker.exe"), "--self-test"], env=env,
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert ready.returncode == 0, (ready.returncode, ready.stdout, ready.stderr)
        assert "WORKER_READY" in ready.stdout
        assert (data / "memory.json").exists()

        gui = subprocess.Popen([str(DIST / "Chunda.exe")], env=env)
        try:
            time.sleep(3)
            assert gui.poll() is None, f"GUI завершился сразу с кодом {gui.returncode}"
        finally:
            gui.terminate()
            gui.wait(timeout=10)
    print("Packaged GUI and worker started successfully.")


if __name__ == "__main__":
    main()

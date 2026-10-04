"""Check standalone and single-file Windows builds without network access."""

import os
import json
import subprocess
import struct
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("TWITCH_AI_TEST_EXE", str(ROOT / "dist" / "singlefile" / "TwitchAIBot.exe"))).resolve()
LAYOUT = os.environ.get("TWITCH_AI_TEST_LAYOUT", "onefile")


def main() -> None:
    assert LAYOUT in ("onefile", "standalone"), LAYOUT
    if LAYOUT == "standalone":
        assert (EXE.parent / "assets" / "app.ico").read_bytes() == (ROOT / "assets" / "app.ico").read_bytes()
        assert (EXE.parent / "memory.example.json").read_bytes() == (ROOT / "memory.example.json").read_bytes()
    # Verify the actual PE
    # resources, not just a nonempty window icon (which could be Python's icon).
    import pefile
    source = (ROOT / "assets" / "app.ico").read_bytes()
    count = struct.unpack_from("<H", source, 4)[0]
    expected = set()
    for index in range(count):
        length, offset = struct.unpack_from("<II", source, 6 + index * 16 + 8)
        expected.add(source[offset:offset + length])
    with pefile.PE(str(EXE)) as image:
        actual = set()
        for resource in image.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id == 3:  # RT_ICON
                for icon in resource.directory.entries:
                    for language in icon.directory.entries:
                        data = language.data.struct
                        actual.add(image.get_data(data.OffsetToData, data.Size))
        assert expected <= actual, "Executable is missing the application's icon resources"
    with tempfile.TemporaryDirectory() as temporary:
        launch = EXE
        if LAYOUT == "onefile":
            # The distribution must work with just this file in a new location.
            launch_dir = Path(temporary) / "Чистая папка запуска"
            launch_dir.mkdir()
            launch = launch_dir / "Переименованный ИИ бот.exe"
            shutil.copy2(EXE, launch)
        env = os.environ.copy()
        env["APPDATA"] = temporary
        env["QT_QPA_PLATFORM"] = "offscreen"
        log_path = Path(temporary) / "worker.log"
        missing = subprocess.run([str(launch), "--bot", str(log_path)], cwd=temporary, env=env, timeout=120)
        assert missing.returncode == 1, missing.returncode
        assert "Укажите ваш канал Twitch" in log_path.read_text(encoding="utf-8")

        data = Path(temporary) / "TwitchAIBot"
        (data / ".env").write_text(
            "TWITCH_CHANNEL=streamer\nTWITCH_BOT_NAME=helper_bot\n"
            "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
            "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
        )
        (data / "profiles.json").write_text(json.dumps({"version": 1, "profiles": [
            {"login": "viewer", "user_id": "123", "prompt": "Personal instruction", "enabled": True}
        ]}), encoding="utf-8")
        ready = subprocess.run([str(launch), "--bot", str(log_path), "--self-test"], cwd=temporary, env=env, timeout=120)
        assert ready.returncode == 0, (ready.returncode, log_path.read_text(encoding="utf-8"))
        assert "WORKER_READY" in log_path.read_text(encoding="utf-8")
        assert (data / "memory.json").exists()

        dictionary = data / "local-context.json"
        dictionary.write_text("{ damaged dictionary", encoding="utf-8")
        degraded = subprocess.run([str(launch), "--bot", str(log_path), "--self-test"], cwd=temporary, env=env, timeout=120)
        assert degraded.returncode == 0, (degraded.returncode, log_path.read_text(encoding="utf-8"))
        assert "WORKER_READY" in log_path.read_text(encoding="utf-8")
        assert "Локальный контекст" in log_path.read_text(encoding="utf-8")
        assert dictionary.read_text(encoding="utf-8") == "{ damaged dictionary"

        invalid_path = data / "profiles.json"
        saved_profiles = invalid_path.read_bytes()
        invalid_path.write_text("broken", encoding="utf-8")
        invalid = subprocess.run([str(launch), "--bot", str(log_path), "--self-test"], cwd=temporary, env=env, timeout=120)
        assert invalid.returncode == 1
        assert "profiles.json" in log_path.read_text(encoding="utf-8")
        invalid_path.write_bytes(saved_profiles)

        gui = subprocess.run([str(launch), "--self-test-gui"], cwd=temporary, env=env, timeout=120)
        assert gui.returncode == 0, f"GUI check failed with code {gui.returncode}"
        if LAYOUT == "onefile":
            assert list(launch.parent.iterdir()) == [launch], "Launch created sidecar files beside the EXE"
    print(f"{LAYOUT} GUI and worker started successfully.")


if __name__ == "__main__":
    main()

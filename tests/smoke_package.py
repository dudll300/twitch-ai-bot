"""Check the native packaged GUI and its worker without network access."""

import os
import json
import subprocess
import struct
import sys
import plistlib
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_EXE = (
    ROOT / "dist" / "TwitchAIBot.exe" if sys.platform == "win32" else
    ROOT / "dist" / "TwitchAIBot.app" / "Contents" / "MacOS" / "TwitchAIBot"
    if sys.platform == "darwin" else ROOT / "dist" / "TwitchAIBot" / "TwitchAIBot"
)
EXE = Path(os.environ.get("TWITCH_AI_TEST_EXE", str(DEFAULT_EXE))).resolve()


def check_native_icon() -> None:
    if sys.platform == "darwin":
        contents = EXE.parent.parent
        with (contents / "Info.plist").open("rb") as stream:
            metadata = plistlib.load(stream)
        from version import VERSION
        assert metadata["CFBundleShortVersionString"] == VERSION
        icon = contents / "Resources" / metadata["CFBundleIconFile"]
        assert icon.read_bytes() == (ROOT / "assets" / "app.icns").read_bytes()
        return
    if sys.platform != "win32":
        return
    # PyInstaller's Windows dependencies include pefile. Verify the actual PE
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


def main() -> None:
    check_native_icon()
    with tempfile.TemporaryDirectory() as temporary:
        env = os.environ.copy()
        if sys.platform == "win32":
            env["APPDATA"] = temporary
            data = Path(temporary) / "TwitchAIBot"
        elif sys.platform == "darwin":
            env["HOME"] = temporary
            data = Path(temporary) / "Library" / "Application Support" / "TwitchAIBot"
        else:
            env["HOME"] = temporary
            env["XDG_CONFIG_HOME"] = str(Path(temporary) / ".config")
            data = Path(temporary) / ".config" / "TwitchAIBot"
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        # Launch from an unrelated directory: resources must come from the
        # bundle and writable settings from the native user-data location.
        env.pop("PYTHONPATH", None)
        log_path = Path(temporary) / "worker.log"
        def launch(*arguments):
            return subprocess.run([str(EXE), *arguments], env=env, cwd=temporary, timeout=60)

        missing = launch("--bot", str(log_path))
        assert missing.returncode == 1, missing.returncode
        assert "Укажите ваш канал Twitch" in log_path.read_text(encoding="utf-8")

        (data / ".env").write_text(
            "TWITCH_CHANNEL=streamer\nTWITCH_BOT_NAME=helper_bot\n"
            "TWITCH_CLIENT_ID=client123\nAI_BASE_URL=https://ai.starimg.ru/v1\n"
            "AI_API_KEY=sk-test\nAI_MODEL=deepseek-v4-pro\n", encoding="utf-8",
        )
        (data / "profiles.json").write_text(json.dumps({"version": 1, "profiles": [
            {"login": "viewer", "user_id": "123", "prompt": "Personal instruction", "enabled": True}
        ]}), encoding="utf-8")
        ready = launch("--bot", str(log_path), "--self-test")
        assert ready.returncode == 0, (ready.returncode, log_path.read_text(encoding="utf-8"))
        assert "WORKER_READY" in log_path.read_text(encoding="utf-8")
        assert (data / "memory.json").exists()

        dictionary = data / "local-context.json"
        dictionary.write_text("{ damaged dictionary", encoding="utf-8")
        degraded = launch("--bot", str(log_path), "--self-test")
        assert degraded.returncode == 0, (degraded.returncode, log_path.read_text(encoding="utf-8"))
        assert "WORKER_READY" in log_path.read_text(encoding="utf-8")
        assert "Локальный контекст" in log_path.read_text(encoding="utf-8")
        assert dictionary.read_text(encoding="utf-8") == "{ damaged dictionary"

        invalid_path = data / "profiles.json"
        saved_profiles = invalid_path.read_bytes()
        invalid_path.write_text("broken", encoding="utf-8")
        invalid = launch("--bot", str(log_path), "--self-test")
        assert invalid.returncode == 1
        assert "profiles.json" in log_path.read_text(encoding="utf-8")
        invalid_path.write_bytes(saved_profiles)

        gui = launch("--self-test-gui")
        assert gui.returncode == 0, f"GUI check failed with code {gui.returncode}"
    print("Packaged GUI and worker started successfully.")


if __name__ == "__main__":
    main()

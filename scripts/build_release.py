"""Build and smoke-test a native GUI package, then create its release archive.

Run on each target OS with Python 3.12 and requirements-build.txt installed:
    python scripts/build_release.py
"""

import hashlib
from pathlib import Path
import platform
import runpy
import shutil
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = runpy.run_path(str(ROOT / "version.py"))["VERSION"]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    system = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform)
    machine = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if not system or not architecture:
        raise RuntimeError(f"Unsupported release target: {sys.platform}/{machine}")
    run(sys.executable, "assets/generate_icon.py")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "TwitchAIBot.spec")
    run(sys.executable, "tests/smoke_package.py")
    distribution = ROOT / "dist"
    output = distribution / "release"
    output.mkdir(parents=True, exist_ok=True)
    basename = f"TwitchAIBot-v{VERSION}-{system}-{architecture}"
    instructions = (ROOT / "docs" / "release-readme.txt").read_bytes()
    if system == "Windows":
        target = output / (basename + ".zip")
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(distribution / "TwitchAIBot.exe", "TwitchAIBot.exe")
            archive.writestr("README.txt", instructions)
    elif system == "macOS":
        staging = ROOT / "build" / "dmg"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        shutil.copytree(distribution / "TwitchAIBot.app", staging / "TwitchAIBot.app", symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        (staging / "README.txt").write_bytes(instructions)
        target = output / (basename + ".dmg")
        run("codesign", "--verify", "--deep", "--strict", str(staging / "TwitchAIBot.app"))
        run("hdiutil", "create", "-volname", "Twitch AI Bot", "-srcfolder", str(staging),
            "-ov", "-format", "UDZO", str(target))
        run("hdiutil", "verify", str(target))
    else:
        application = distribution / "TwitchAIBot"
        (application / "README.txt").write_bytes(instructions)
        target = output / (basename + ".tar.gz")
        with tarfile.open(target, "w:gz") as archive:
            archive.add(application, arcname="TwitchAIBot")
    # The publishing job combines the per-package hashes into SHA256SUMS.txt.
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target.with_name(target.name + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8", newline="\n",
    )
    print(f"Release package: {target}")


if __name__ == "__main__":
    main()

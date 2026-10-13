"""Native GUI builds: Windows EXE, macOS app bundle, Linux directory."""

import runpy
import sys
from pathlib import Path

root = Path(SPECPATH)
version = runpy.run_path(str(root / "version.py"))["VERSION"]
name = "TwitchAIBot"
analysis = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    datas=[(str(root / "assets" / "app.ico"), "assets"),
           (str(root / "assets" / "checkmark.svg"), "assets"),
           (str(root / "memory.example.json"), ".")],
)
archive = PYZ(analysis.pure)
if sys.platform == "win32":
    executable = EXE(
        archive, analysis.scripts, analysis.binaries, analysis.datas, [],
        name=name, console=False, upx=False,
        icon=str(root / "assets" / "app.ico"),
    )
else:
    executable = EXE(
        archive, analysis.scripts, [], exclude_binaries=True,
        name=name, console=sys.platform != "darwin", upx=False,
    )
    collection = COLLECT(
        executable, analysis.binaries, analysis.datas,
        name=name, upx=False,
    )
    if sys.platform == "darwin":
        application = BUNDLE(
            collection, name=name + ".app",
            icon=str(root / "assets" / "app.icns"),
            bundle_identifier="com.dudll300.twitchaibot",
            version=version,
            info_plist={
                "CFBundleShortVersionString": version,
                "LSMinimumSystemVersion": "12.0",
                "NSHighResolutionCapable": True,
            },
        )

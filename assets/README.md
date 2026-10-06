# Studio application icon

`app-source.png` is the approved original artwork (1254 × 1254 pixels): a blue
liquid-glass assistant with two bright eyes, on a graphite tile matching the
Studio interface. The blue highlights match the application's `#168BFF`
accent. The original is kept unchanged, including transparency outside the
rounded tile.

`app.png` is exported at 512 × 512 pixels. `app.ico` embeds separate PNG frames
at 16, 24, 32, 48, 64, 128 and 256 pixels, each resized directly from the
original artwork with smooth scaling. The Windows executable, application
window and sidebar brand all use `app.ico`.

To regenerate the exported assets with the application's PySide6 environment:

```powershell
python assets/generate_icon.py
```

The generator uses PySide6 and Python's standard library. It needs no extra
dependencies or external tools.

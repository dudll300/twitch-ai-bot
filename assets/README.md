# Studio application icon

`app.svg` is the editable source: a blue reply bubble with a white AI sparkle,
on a graphite tile matching the Studio interface. The subtle blue highlight
uses the same `#168BFF` accent as the application. Outside the rounded tile,
the image is transparent.

`app.png` is rendered at 512 × 512 pixels. `app.ico` embeds separate PNG frames
at 16, 24, 32, 48, 64, 128 and 256 pixels, each rendered directly from the
vector source for crisp small sizes. The Windows executable and application
window both use `app.ico`.

To regenerate the exported assets with the application's PySide6 environment:

```powershell
python assets/generate_icon.py
```

The generator uses QtSvg and Python's standard library. It needs no extra
dependencies or external tools.

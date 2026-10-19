# Project guidance

- Desktop UI: Python and PySide6; current UI/UX work targets Windows.
- Read `docs/windows-studio-design.md` before changing desktop layouts or save/apply behavior.
- Keep existing settings, profiles, memory and SQLite formats compatible.
- UI threads must not perform provider requests or SQLite reads. Workers return plain data through queues.
- Preserve reward priority, privacy checks and the separate meaning of autonomous preview/publication.
- Do not display editor drafts as the active worker configuration.
- Never emit credentials, raw chat or prompt text in structured UI events.
- Use shared theme tokens and widgets instead of isolated style sheets or new icon systems.
- Check compact layouts and both themes after layout changes.
- Tests use temporary data and mock APIs; never launch the real bot to test UI.
- Regression checks: unittest discovery, existing GUI smoke scripts, `tests/smoke_windows_studio.py`, and the Windows package smoke test.

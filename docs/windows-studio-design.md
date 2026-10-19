# Windows studio: UI/UX contract

The current redesign targets Windows and retains PySide6 6.8.3 and all bot data formats.
The UI is Russian. Dark and light themes share semantic tokens in `studio_theme.py`.

## Navigation and hierarchy

- Work: Эфир, История.
- Bot: Поведение, Зрители, Самостоятельные реплики, Знания канала.
- Tools: Тестирование, Безопасность, Настройки.
- Настройки contains Подключение, Интерфейс and Справка.
- First setup is resumable and optional. It reuses the actual editor screens.
- Keep global Start/Stop visible. Stop ends the whole worker; disabling autonomous participation does not stop rewards.
- Show a primary task before secondary configuration. Put long help and diagnostics in disclosures.

## Shared visual rules

- Segoe UI; 15 logical pixels for body text, 13 for help, 18 for sections, 28 for page titles.
- Spacing uses 4/8/12/16/24/32; card radius 16, field/button radius 10.
- Primary buttons use #0066CC with white text. Use semantic theme colors elsewhere.
- Text contrast target: at least 4.5:1. Important icons and focus indicators must remain visible in both themes.
- Status uses text plus color. A gray or green pill alone must not carry the meaning.
- Native drawn icons avoid missing glyphs. Use Qt palettes in custom-painted controls.
- Do not use real credentials, personal profiles or chat history for screenshots.

## Responsive behavior

- Default 1240x860; target minimum 800x480, constrained to available screen geometry.
- Menu becomes an icon rail below window width 1100; it remains scrollable.
- Responsive forms use two columns from content width 900.
- MasterDetail switches to list/detail navigation below content width 760.
- Reparented panes must be explicitly shown on return to the wide layout.
- Long editors remain scrollable. Never squeeze their labels and fields to make a large form fit.
- Retain NoWheelComboBox and nested editor-to-page wheel handling.

## Data and operations

- Draft, saved and used-by-worker are distinct states.
- Connection, behavior and viewers have independent saving and require restart.
- Saving new connection data during a run must not invalidate the running worker's tokens. Invalidate them before the next launch.
- Safety, participation and knowledge keep existing live-application semantics.
- Live toggles use saved values; they never apply unrelated drafts.
- All dirty editors participate in close protection, including autonomous instructions and numbers.
- Global save validates before writing. Files are individually atomic; partial success is reported honestly.
- UI preferences store only appearance, geometry, splitter sizes and setup progress.
- Worker state comes from versioned UI_EVENT data. Chat, EventSub and AI readiness are independent.
- Settings revisions are opaque hashes. Never emit prompt text, raw chat, credentials or raw provider errors in UI_EVENT.
- Cancellable operations retain old queues and cancellation objects in old workers. Late results cannot reach a new operation.

## Verification

Run the existing unittest suite and GUI smoke checks, plus `tests/smoke_windows_studio.py`.
Use `--screenshots` to capture every page in both themes at wide and compact sizes.
Use QT_SCALE_FACTOR 1, 1.25 and 1.5 in separate processes for DPI checks.
Windows packaged checks use `tests/smoke_package.py`; TWITCH_AI_TEST_EXE can select a candidate EXE.

Before a public release, conduct five-user onboarding/usability trials. Local deterministic
tests do not establish that novice users can finish those tasks without help.

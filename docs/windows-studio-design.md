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

- Orbit uses bundled Onest (OFL); 14 logical pixels for body text, 13 for help, 16 for sections, 40 for page titles.
- Spacing uses 4/8/12/16/24/32; card radius 16, field radius 14, pill buttons radius 22.
- Primary buttons use pale blue with dark ink. All colors come from semantic theme tokens.
- A native painted blue horizon and a full-width brand bar identify Orbit. Decorative movement is finite and respects the animation preference.
- Panels use a restrained diagonal gradient and soft blue/violet light beneath their lower edge. Lighting is painted behind controls without graphics effects. Header hover adds eased cursor parallax and a brighter halo; leaving or disabling motion resets it.
- Theme changes retain the shared selector tree and refresh cached palette brushes in parent order. Animation preferences never rebuild the application stylesheet. Verify actual painted colors after switching, not only the theme property.
- Shared StudioButton wraps long captions and sizes their height; dropdowns use opaque, padded rows with a selection mark and native keyboard behavior.
- Interface preferences expose theme and an animation toggle; do not expose animation durations.
- Panels share a separate title strip, body padding and dividers. History, profiles, knowledge and the stream conversation use native row delegates, not multiline text editors as lists.
- Navigation has a filled selected state without a light outline. Keyboard focus remains distinct from mouse selection.
- The wheel never edits combos, numbers or tab selections, even when they have focus. Open catalogs still scroll. Native scrollbars retain their hit areas with a slim painted thumb; outer pages ease wheel movement.
- Step controls and checkmarks are drawn explicitly and use the shared palette. All numeric editors use NumberInput.
- Page changes crossfade and move a short distance using transient snapshots. Remove the snapshot at completion so settled native text remains sharp. Rapid navigation and resize cancel previous transitions.
- Text contrast target: at least 4.5:1. Important icons and focus indicators must remain visible in both themes.
- Status uses text plus color. A gray or green pill alone must not carry the meaning.
- Native drawn icons avoid missing glyphs. Use Qt palettes in custom-painted controls.
- Do not use real credentials, personal profiles or chat history for screenshots.

## Responsive behavior

- Default 1240x860; target minimum 800x480, constrained to available screen geometry.
- A 74-pixel icon rail remains scrollable and provides tooltips and accessible names at every width.
- Responsive forms use two columns from content width 900.
- MasterDetail switches to list/detail navigation below content width 760.
- Reparented panes must be explicitly shown on return to the wide layout.
- Long editors remain scrollable. Never squeeze their labels and fields to make a large form fit.
- Retain NoWheelComboBox and nested editor-to-page wheel handling.
- Master/detail cards have a 20-pixel splitter gap; verify pane bounds as well as the overall window bounds.

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
- Testing exposes an editable single-model selector above the question. Its default tracks the current model from settings; catalog loading is explicit. Checked comparison models override this selector and are listed beside it.
- The latest published reply on Эфир comes from the existing background history read. Previews and blocked replies never appear as published.
- The stream conversation shows existing reward questions and bot decisions from session history, not an invented full Twitch chat. Queue detail shows confirmed counts rather than fabricated viewers or question text.
- A configured legacy installation dismisses first-run onboarding regardless of the old setup flag. The setup card depends on saved or active connection values, never unsaved form contents.

## Verification

Run the existing unittest suite and GUI smoke checks, plus `tests/smoke_windows_studio.py`, `tests/smoke_orbit.py`, `tests/smoke_orbit_fidelity.py` and `tests/smoke_orbit_polish.py`.
Use `--screenshots` to capture every page in both themes at wide and compact sizes.
Use QT_SCALE_FACTOR 1, 1.25 and 1.5 in separate processes for DPI checks.
Windows packaged checks use `tests/smoke_package.py`; TWITCH_AI_TEST_EXE can select a candidate EXE.

Before a public release, conduct five-user onboarding/usability trials. Local deterministic
tests do not establish that novice users can finish those tasks without help.
